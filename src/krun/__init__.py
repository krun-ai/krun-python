"""Official Python SDK for the Krun API."""

import logging as _logging

from ._client import AsyncKrun, Krun
from ._constants import API_VERSION, DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
from ._exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    InferenceFailedError,
    InternalServerError,
    InvalidRequestError,
    KrunError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    ServiceUnavailableError,
    UpstreamTimeoutError,
)
from ._version import __version__
from .types import (
    AbstentionStatus,
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    ChoiceQuestionParam,
    DecisionResult,
    Feedback,
    Model,
    QuestionParam,
    QuestionsParam,
    TaskType,
    Usage,
)

# Silent by default: the SDK never configures logging. Enable with logging.getLogger("krun").setLevel(DEBUG).
_logging.getLogger("krun").addHandler(_logging.NullHandler())

__all__ = [
    "API_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "APIConnectionError",
    "APIError",
    "APIResponseValidationError",
    "APITimeoutError",
    "AbstentionStatus",
    "Answer",
    "AsyncKrun",
    "AuthenticationError",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "ChoiceQuestionParam",
    "DecisionResult",
    "Feedback",
    "InferenceFailedError",
    "InternalServerError",
    "InvalidRequestError",
    "Krun",
    "KrunError",
    "Model",
    "NotFoundError",
    "QuestionParam",
    "QuestionsParam",
    "QuotaExceededError",
    "RateLimitError",
    "ServiceUnavailableError",
    "TaskType",
    "UpstreamTimeoutError",
    "Usage",
    "__version__",
]
