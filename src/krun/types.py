"""Public request and response types.

Requests accept plain dicts (typed as `TypedDict`s) or the `ChoiceQuestion` dataclass. Responses are frozen
dataclasses. Question ids and option ids are whatever the caller chose: they are never renamed, validated against an
enum or re-ordered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TypedDict

__all__ = [
    "AbstentionStatus",
    "Answer",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "ChoiceQuestionParam",
    "DecisionResult",
    "Feedback",
    "Model",
    "QuestionParam",
    "QuestionsParam",
    "TaskType",
    "Usage",
]

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


# Only `choice` exists today. New question types (score, boolean, ...) will join these unions when the API ships them.
QuestionParam = ChoiceQuestionParam | ChoiceQuestion

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


# Union of every answer type the API can return. Only `choice` exists today.
Answer = ChoiceAnswer


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
    answers: dict[str, ChoiceAnswer]
    usage: Usage
    request_id: str
    """Value of the `X-Request-ID` response header. Pass it to `feedback()`."""


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
