"""SDK defaults and the API contract this release was built against."""

DEFAULT_BASE_URL = "https://api.krun.ai"

# A Krun One V1 cold start can take up to ~150 s (the API edge waits 150 s upstream); 180 s leaves room for the
# response.
DEFAULT_TIMEOUT = 180.0

# Retries apply to `decide()` and `models()` only (see `_retry.py`). `feedback()` is never retried.
DEFAULT_MAX_RETRIES = 1

API_KEY_ENV = "KRUN_API_KEY"

# Krun API major version this SDK speaks (the `/v1` path prefix).
API_VERSION = "v1"

# `info.version` and SHA-256 of the canonical JSON (sorted keys, no whitespace) of `openapi/openapi.json`, the
# snapshot of https://api.krun.ai/openapi.json this release was written against. `scripts/check_openapi.py`
# compares the live document with the snapshot; `tests/test_contract.py` keeps these values in sync with it.
OPENAPI_VERSION = "1.0.0-beta"
OPENAPI_SHA256 = "ae96a4ebfca51f2f1e4c6f5101799e00457d901c15935bc33939f77bc9f37121"
