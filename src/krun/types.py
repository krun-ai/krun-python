"""Public request and response types.

Three decision primitives: `choice` (pick an option), `noul` (probability that a yes/no proposition holds) and
`score` (rate on ordered levels). Requests accept plain dicts (typed as `TypedDict`s) or the `ChoiceQuestion` /
`NoulQuestion` / `ScoreQuestion` dataclasses. Responses are frozen dataclasses, decoded into `ChoiceAnswer` /
`NoulAnswer` / `ScoreAnswer` by their `type`. Question ids, option ids and level order are whatever the caller chose:
they are never renamed, validated against an enum or re-ordered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TypedDict, TypeVar

__all__ = [
    "AbstentionStatus",
    "Answer",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "ChoiceQuestionParam",
    "DecisionResult",
    "ExpectedParam",
    "Feedback",
    "Model",
    "NoulAnswer",
    "NoulCriteriaParam",
    "NoulQuestion",
    "NoulQuestionParam",
    "QuestionParam",
    "QuestionType",
    "QuestionsParam",
    "ScoreAnswer",
    "ScoreQuestion",
    "ScoreQuestionParam",
    "TaskType",
    "Usage",
]

QuestionType = Literal["choice", "noul", "score"]
"""The decision primitive of a question (and of its answer)."""

TaskType = Literal["intent", "tool"]
"""`intent` (the API default) or `tool` (tool/function routing)."""

AbstentionStatus = Literal["calibrated", "advisory"]
"""`calibrated`: abstention was validated for this kind of question (intent, label-only options).
`advisory`: `abstain` is a hint, not a validated guarantee (tool routing, intents with descriptions)."""


# --------------------------------------------------------------------------------------------------------- requests


class _ChoiceQuestionRequired(TypedDict):
    type: Literal["choice"]
    options: Mapping[str, str | None]


class ChoiceQuestionParam(_ChoiceQuestionRequired, total=False):
    """A `choice` question as a plain dict.

    `options` maps option id → description (2–64 options). Use `""` or `None` for label-only options.
    """

    task_type: TaskType | None


@dataclass(frozen=True)
class ChoiceQuestion:
    """A `choice` question as an object: `ChoiceQuestion(options={...}, task_type="tool")`.

    Equivalent to `{"type": "choice", "options": {...}, "task_type": "tool"}`.
    """

    options: Mapping[str, str | None]
    task_type: TaskType | None = None
    type: Literal["choice"] = "choice"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "options": dict(self.options)}
        if self.task_type is not None:
            out["task_type"] = self.task_type
        return out


class NoulCriteriaParam(TypedDict, total=False):
    true: str
    false: str


"""Optional texts for what makes a `noul` proposition true / false (either may be omitted)."""


class _NoulQuestionRequired(TypedDict):
    type: Literal["noul"]
    instructions: str


class NoulQuestionParam(_NoulQuestionRequired, total=False):
    """A `noul` question as a plain dict: the probability that a yes/no proposition about the context holds.

    `instructions` is the proposition phrased as a yes/no question, e.g. "Is the customer asking for a human?".
    """

    criteria: NoulCriteriaParam


@dataclass(frozen=True)
class NoulQuestion:
    """A `noul` question as an object: `NoulQuestion("Is the customer asking for a human?")`.

    Equivalent to `{"type": "noul", "instructions": ..., "criteria": {...}}`.
    """

    instructions: str
    criteria: Mapping[str, str] | None = None
    """Optional `{"true": ..., "false": ...}` descriptions (either key may be omitted)."""
    type: Literal["noul"] = "noul"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            out["criteria"] = dict(self.criteria)
        return out


class ScoreQuestionParam(TypedDict):
    """A `score` question as a plain dict: rate the context on ORDERED levels, lowest first (2–16 levels)."""

    type: Literal["score"]
    instructions: str
    levels: Sequence[str]


@dataclass(frozen=True)
class ScoreQuestion:
    """A `score` question as an object: `ScoreQuestion("How severe?", ["Minor", "Moderate", "Critical"])`.

    Level order is semantic (index 0 = lowest level) and is sent exactly as given.
    """

    instructions: str
    levels: Sequence[str]
    type: Literal["score"] = "score"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "instructions": self.instructions, "levels": list(self.levels)}


QuestionParam = (
    ChoiceQuestionParam | NoulQuestionParam | ScoreQuestionParam | ChoiceQuestion | NoulQuestion | ScoreQuestion
)

QuestionsParam = Mapping[str, QuestionParam | Mapping[str, Any]]
"""`questions` argument of `decide()`: question id → question. Plain dicts are accepted as-is (the API validates
them), so dicts built at runtime type-check too."""


# -------------------------------------------------------------------------------------------------------- responses


@dataclass(frozen=True)
class ChoiceAnswer:
    """The answer to one `choice` question."""

    type: Literal["choice"]
    choice: str | None
    """Selected option id, or `None` when the model abstains. The SDK never replaces `None` with a best guess:
    the ranking is still available in `probabilities`."""
    confidence: float
    """Margin between the two most likely options: top-1 probability minus top-2 probability, in [0, 1].
    It is the abstention score, NOT the probability that `choice` is correct."""
    probabilities: dict[str, float]
    """Calibrated probability per option id, keyed exactly as sent, in request order."""
    abstain: bool
    """True when `confidence` is below the model's abstention threshold (then `choice` is `None`)."""
    abstention_status: AbstentionStatus
    """`calibrated` or `advisory` (see `AbstentionStatus`)."""


@dataclass(frozen=True)
class NoulAnswer:
    """The answer to one `noul` question."""

    type: Literal["noul"]
    noul: float
    """Calibrated probability that the proposition holds, in [0, 1]: 0 = clearly false, 0.5 = uncertain,
    1 = clearly true. There is no separate confidence: the probability is the answer."""


@dataclass(frozen=True)
class ScoreAnswer:
    """The answer to one `score` question."""

    type: Literal["score"]
    score: float
    """Expected level: sum of level index × probability, in [0, len(levels) - 1]. Not the most likely level."""
    confidence: float
    """Concentration of the distribution, in [0, 1]: 1 − variance / maximum variance of the level index (1 = all
    probability on one level, 0 = split between the lowest and the highest level)."""
    legend: dict[str, str]
    """Level index ("0", "1", ...) → the level text sent in the request."""
    probabilities: dict[str, float]
    """Level index → calibrated probability, in level order."""


Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer
"""Union of every answer type. Narrow with `isinstance(answer, ScoreAnswer)` or `answer.type == "score"`."""


class _ExpectedChoice(TypedDict):
    type: Literal["choice"]
    value: str


class _ExpectedNoul(TypedDict):
    type: Literal["noul"]
    value: bool


class _ExpectedScore(TypedDict):
    type: Literal["score"]
    value: int


ExpectedParam = _ExpectedChoice | _ExpectedNoul | _ExpectedScore
"""`expected` argument of `feedback()`: `{"type": "choice", "value": "billing"}`, `{"type": "noul", "value": True}`
or `{"type": "score", "value": 2}` (level index)."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    """Input tokens processed by the model, summed over the questions (each question is scored as its own
    sequence). Krun scores options and generates no text, so there are no output tokens. `None` only if the
    backend did not report it."""


@dataclass(frozen=True)
class DecisionResult:
    """Result of `decide()`: one answer per question, keyed by the question ids of the request, in request order."""

    model: str
    answers: dict[str, Answer]
    """Question id → typed answer (`ChoiceAnswer`, `NoulAnswer` or `ScoreAnswer`, matching the question's type)."""
    usage: Usage
    request_id: str
    """Value of the `X-Request-ID` response header. Pass it to `feedback()`."""

    def choice(self, question_id: str) -> ChoiceAnswer:
        """The answer to a `choice` question, typed (raises `TypeError` if that question is not a choice)."""
        return _typed(self.answers, question_id, ChoiceAnswer)

    def noul(self, question_id: str) -> NoulAnswer:
        """The answer to a `noul` question, typed (raises `TypeError` if that question is not a noul)."""
        return _typed(self.answers, question_id, NoulAnswer)

    def score(self, question_id: str) -> ScoreAnswer:
        """The answer to a `score` question, typed (raises `TypeError` if that question is not a score)."""
        return _typed(self.answers, question_id, ScoreAnswer)


_A = TypeVar("_A", ChoiceAnswer, NoulAnswer, ScoreAnswer)


def _typed(answers: Mapping[str, Answer], question_id: str, cls: type[_A]) -> _A:
    answer = answers[question_id]
    if not isinstance(answer, cls):
        raise TypeError(f"answer {question_id!r} is a {answer.type!r} answer, not {cls.__name__}")
    return answer


@dataclass(frozen=True)
class Feedback:
    """Stored feedback, as returned by `feedback()`."""

    id: str
    request_id: str
    question_id: str
    created_at: datetime
    object: str = field(default="feedback")


@dataclass(frozen=True)
class Model:
    id: str
    status: str
    object: str = field(default="model")
