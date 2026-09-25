"""Request serialization and response parsing, shared by the sync and async clients.

Wire format (snake_case JSON) is documented in `openapi/openapi.json`. Question and option ids are copied verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from ._exceptions import APIResponseValidationError
from .types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    ExpectedParam,
    Feedback,
    Model,
    NoulAnswer,
    NoulQuestion,
    QuestionParam,
    QuestionsParam,
    ScoreAnswer,
    ScoreQuestion,
    Usage,
)

JSON = Any


# ---------------------------------------------------------------------------------------------------- serialization


def _question_to_wire(question_id: str, question: QuestionParam | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(question, (ChoiceQuestion, NoulQuestion, ScoreQuestion)):
        return question.to_dict()
    if not isinstance(question, Mapping):
        raise TypeError(
            f"questions[{question_id!r}] must be a dict, ChoiceQuestion, NoulQuestion or ScoreQuestion, "
            f"got {type(question).__name__}"
        )
    out = dict(question)
    options = out.get("options")
    if isinstance(options, Mapping):
        out["options"] = dict(options)
    criteria = out.get("criteria")
    if isinstance(criteria, Mapping):
        out["criteria"] = dict(criteria)
    levels = out.get("levels")
    if isinstance(levels, (list, tuple)):
        out["levels"] = list(levels)  # order preserved: it is semantic
    return out


def decide_body(
    context: str,
    questions: QuestionsParam,
    model: str | None,
) -> dict[str, Any]:
    """Build the `/v1/decide` body. Limits (1–16 questions, 2–64 options, ...) are enforced by the API, which
    answers `InvalidRequestError` before any inference, so they never drift from the SDK."""
    if not isinstance(context, str):
        raise TypeError(f"context must be a str, got {type(context).__name__}")
    if not isinstance(questions, Mapping):
        raise TypeError(f"questions must be a dict of question id -> question, got {type(questions).__name__}")
    body: dict[str, Any] = {
        "context": context,
        "questions": {qid: _question_to_wire(qid, q) for qid, q in questions.items()},
    }
    if model is not None:
        body["model"] = model
    return body


def feedback_body(
    request_id: str,
    question_id: str,
    correct: bool,
    expected_decision: str | None,
    metadata: Mapping[str, Any] | None,
    expected: ExpectedParam | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(request_id, str) or not request_id:
        raise TypeError("request_id must be a non-empty str (DecisionResult.request_id)")
    if not isinstance(question_id, str) or not question_id:
        raise TypeError("question_id must be a non-empty str")
    if not isinstance(correct, bool):
        raise TypeError(f"correct must be a bool, got {type(correct).__name__}")
    body: dict[str, Any] = {"request_id": request_id, "question_id": question_id, "correct": correct}
    if expected is not None:
        if not isinstance(expected, Mapping) or expected.get("type") not in ("choice", "noul", "score"):
            raise TypeError('expected must be {"type": "choice" | "noul" | "score", "value": ...}')
        body["expected"] = {"type": expected["type"], "value": expected.get("value")}
    if expected_decision is not None:
        body["expected_decision"] = expected_decision
    if metadata is not None:
        if not isinstance(metadata, Mapping):
            raise TypeError(f"metadata must be a dict, got {type(metadata).__name__}")
        body["metadata"] = dict(metadata)
    return body


# ---------------------------------------------------------------------------------------------------------- parsing


def _fail(what: str) -> APIResponseValidationError:
    return APIResponseValidationError(f"unexpected response from the Krun API: {what}")


def _obj(value: JSON, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"{where} is not an object")
    return value


def _str(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise _fail(f"{where}.{key} is not a string")
    return value


def _number(value: JSON, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{where} is not a number")
    return float(value)


def _choice_answer(data: dict[str, Any], where: str) -> ChoiceAnswer:
    choice = data.get("choice")
    if choice is not None and not isinstance(choice, str):
        raise _fail(f"{where}.choice is not a string or null")
    abstain = data.get("abstain")
    if not isinstance(abstain, bool):
        raise _fail(f"{where}.abstain is not a boolean")
    probs = _obj(data.get("probabilities"), f"{where}.probabilities")
    return ChoiceAnswer(
        type="choice",
        choice=choice,
        confidence=_number(data.get("confidence"), f"{where}.confidence"),
        probabilities={k: _number(v, f"{where}.probabilities[{k!r}]") for k, v in probs.items()},
        abstain=abstain,
        abstention_status=_str(data, "abstention_status", where),  # type: ignore[arg-type]
    )


def _probabilities(data: dict[str, Any], where: str) -> dict[str, float]:
    probs = _obj(data.get("probabilities"), f"{where}.probabilities")
    return {k: _number(v, f"{where}.probabilities[{k!r}]") for k, v in probs.items()}


def _noul_answer(data: dict[str, Any], where: str) -> NoulAnswer:
    return NoulAnswer(type="noul", noul=_number(data.get("noul"), f"{where}.noul"))


def _score_answer(data: dict[str, Any], where: str) -> ScoreAnswer:
    legend = _obj(data.get("legend"), f"{where}.legend")
    for k, v in legend.items():
        if not isinstance(v, str):
            raise _fail(f"{where}.legend[{k!r}] is not a string")
    return ScoreAnswer(
        type="score",
        score=_number(data.get("score"), f"{where}.score"),
        confidence=_number(data.get("confidence"), f"{where}.confidence"),
        legend=dict(legend),
        probabilities=_probabilities(data, where),
    )


# Answer parsers by `type`. A type this SDK does not know raises APIResponseValidationError ("please upgrade").
_ANSWER_PARSERS: dict[str, Callable[[dict[str, Any], str], Answer]] = {
    "choice": _choice_answer,
    "noul": _noul_answer,
    "score": _score_answer,
}


def parse_decision(data: JSON, request_id: str) -> DecisionResult:
    body = _obj(data, "body")
    answers_raw = _obj(body.get("answers"), "answers")
    answers: dict[str, Answer] = {}
    for qid, raw in answers_raw.items():
        where = f"answers[{qid!r}]"
        answer = _obj(raw, where)
        kind = answer.get("type")
        parser = _ANSWER_PARSERS.get(kind) if isinstance(kind, str) else None
        if parser is None:
            raise _fail(f"{where}.type {kind!r} is not supported by this SDK version; please upgrade")
        answers[qid] = parser(answer, where)
    usage = _obj(body.get("usage", {}), "usage")
    tokens = usage.get("input_tokens")
    if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int)):
        raise _fail("usage.input_tokens is not an integer")
    return DecisionResult(
        model=_str(body, "model", "body"),
        answers=answers,
        usage=Usage(input_tokens=tokens),
        request_id=request_id,
    )


_FRACTION = re.compile(r"\.(\d+)")


def _parse_datetime(value: str) -> datetime:
    # Python 3.10's fromisoformat accepts neither "Z" nor more than 6 fractional digits (RFC 3339 allows both).
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail(f"created_at {value!r} is not an RFC 3339 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feedback(data: JSON) -> Feedback:
    body = _obj(data, "body")
    return Feedback(
        id=_str(body, "id", "body"),
        object=_str(body, "object", "body"),
        request_id=_str(body, "request_id", "body"),
        question_id=_str(body, "question_id", "body"),
        created_at=_parse_datetime(_str(body, "created_at", "body")),
    )


def parse_models(data: JSON) -> list[Model]:
    body = _obj(data, "body")
    items = body.get("data")
    if not isinstance(items, list):
        raise _fail("data is not a list")
    out = []
    for i, item in enumerate(items):
        m = _obj(item, f"data[{i}]")
        out.append(
            Model(
                id=_str(m, "id", f"data[{i}]"),
                object=_str(m, "object", f"data[{i}]"),
                status=_str(m, "status", f"data[{i}]"),
            )
        )
    return out
