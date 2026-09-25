"""SDK defaults and the API contract this release was built against."""

DEFAULT_BASE_URL = "https://api.krun.ai"

# Serverless cold starts can take most of the API's 60 s upstream deadline; 70 s leaves room for the response.
DEFAULT_TIMEOUT = 70.0

# Retries apply to `decide()` and `models()` only (see `_retry.py`). `feedback()` is never retried.
DEFAULT_MAX_RETRIES = 1

API_KEY_ENV = "KRUN_API_KEY"

# Krun API major version this SDK speaks (the `/v1` path prefix).
API_VERSION = "v1"

# `info.version` and SHA-256 of the canonical JSON (sorted keys, no whitespace) of `openapi/openapi.json`, the
# snapshot of https://api.krun.ai/openapi.json this release was written against. `scripts/check_openapi.py`
# compares the live document with the snapshot; `tests/test_contract.py` keeps these values in sync with it.
OPENAPI_VERSION = "1.0.0-beta"
OPENAPI_SHA256 = "17b10db5cfabc239dd9f0be95961d07825ccd0393b080e41932154960a28b777"
