# Krun Python SDK

Official Python SDK for the Krun API.

```bash
pip install krun-ai
```

> **Not published yet.** The package is ready but not on PyPI yet. It is distributed as **`krun-ai`** (the name
> `krun` belongs to an unrelated PyPI project) and imported as `krun`. Until it is published, install from a checkout:
>
> ```bash
> pip install "git+https://github.com/krun-ai/krun-python.git"   # or: pip install -e . (local development)
> ```

- Python 3.10+
- One runtime dependency: [`httpx`](https://www.python-httpx.org/)
- Sync (`Krun`) and async (`AsyncKrun`) clients with the same methods
- Fully typed (`py.typed`), frozen dataclasses for results, plain dicts for requests
- Supports **Krun API v1** (`/v1/*`, OpenAPI `1.0.0-beta`)

## Quickstart

```python
from krun import Krun

client = Krun(api_key="krun_live_...")  # or Krun() to read KRUN_API_KEY

result = client.decide(
    context="Customer wants to return an item.",
    questions={
        "department": {
            "type": "choice",
            "options": {
                "shipping": "Shipping and delivery issues",
                "returns": "Returns and refunds",
                "billing": "Billing and payment issues",
            },
        }
    },
)

answer = result.answers["department"]

print(answer.choice)          # "returns", or None if the model abstains
print(answer.confidence)      # 0.9788: top-1 minus top-2 probability
print(answer.probabilities)   # {"shipping": 0.0056, "returns": 0.9866, "billing": 0.0078}
print(result.request_id)      # "req_...": pass it to feedback()
```

## Reading an answer

Every question gets one `ChoiceAnswer`:

| field | type | meaning |
|---|---|---|
| `type` | `"choice"` | question type (the only type today) |
| `choice` | `str \| None` | selected option id, **`None` when `abstain` is true** |
| `confidence` | `float` | **top-1 probability − top-2 probability**, in [0, 1] |
| `probabilities` | `dict[str, float]` | calibrated probability per option id, keys exactly as sent, in request order |
| `abstain` | `bool` | `confidence` is below the model's abstention threshold |
| `abstention_status` | `"calibrated" \| "advisory"` | how much to trust `abstain` (below) |

**`confidence` is a margin, not "the probability that the answer is correct".** It measures how clearly the best
option beats the runner-up: `0.97` means a clear winner, `0.02` means a near tie. It is the score the abstention
threshold is applied to.

**`choice` can be `None`.** When the model abstains, `choice` is `None` and the SDK does **not** replace it with the most
likely option. The ranking is still in `probabilities` if you want a best guess:

```python
if answer.abstain:
    best_guess = max(answer.probabilities, key=answer.probabilities.__getitem__)
    route_to_human(best_guess)
else:
    route(answer.choice)
```

**`abstention_status`**:

- `calibrated`: intent questions with label-only options (`""`/`None` descriptions). Abstention was validated for
  this setup.
- `advisory`: tool routing and options with descriptions. `abstain` is a hint, not a validated guarantee.

`result.usage.input_tokens` is the number of input tokens the model processed, summed over questions. Krun scores
options and does not generate text, so there are no output tokens.

## Multiple questions

Ask several questions about the same context in **one call**. Answers come back under the same ids, in request
order:

```python
result = client.decide(
    context="My card was charged twice and I need the money back today!",
    questions={
        "department": {"type": "choice", "options": {"shipping": "", "returns": "", "billing": ""}},
        "priority": {"type": "choice", "options": {"low": "Can wait", "normal": "", "high": "Urgent"}},
        "risk": {"type": "choice", "options": {"low": "", "high": ""}},
    },
)

result.answers["department"].choice   # "billing"
result.answers["priority"].choice     # "high"
result.answers["risk"].choice
```

Limits (enforced by the API, reported as `InvalidRequestError` before any inference): 1–16 questions, 2–64 options
per question, `context` 1–8,000 characters. Option descriptions can be a string, `""` or `None` (label-only).
Question and option ids are yours: they are sent and returned verbatim (`transaction_charged_twice`,
`calendar_search`, ...).

## Tool routing

Set `task_type: "tool"` to pick a tool or function:

```python
from krun import ChoiceQuestion

result = client.decide(
    context="Find my meetings tomorrow.",
    questions={
        "tool": {
            "type": "choice",
            "task_type": "tool",
            "options": {
                "calendar_search": "Search calendar events",
                "send_email": "Send an email",
            },
        }
    },
)
# or, as an object: {"tool": ChoiceQuestion(options={...}, task_type="tool")}

answer = result.answers["tool"]
answer.choice              # "calendar_search"
answer.abstention_status   # "advisory": tool abstention is a hint, keep your own fallback
```

## Feedback

Tell Krun whether an answer was right. `question_id` is always required:

```python
client.feedback(
    request_id=result.request_id,
    question_id="department",
    correct=False,
    expected_decision="billing",
    metadata={"ticket": "T-1234"},  # optional JSON object, ≤ 8 KiB; no personal data
)
```

Feedback for a `request_id` this project never decided raises `NotFoundError`.

## Models

```python
for model in client.models():
    print(model.id, model.status)   # krun-one-v0 available
```

## Async

```python
import asyncio
from krun import AsyncKrun

async def main() -> None:
    async with AsyncKrun() as client:
        result = await client.decide(context="...", questions={...})
        await client.feedback(request_id=result.request_id, question_id="department", correct=True)

asyncio.run(main())
```

`Krun` and `AsyncKrun` take the same arguments and return the same types.

## Configuration

```python
client = Krun(
    api_key="krun_live_...",          # default: KRUN_API_KEY
    base_url="http://localhost:8080",  # default: https://api.krun.ai
    timeout=70.0,                      # seconds per attempt (default 70)
    max_retries=1,                     # decide()/models() only (default 1)
    http_client=None,                  # optional httpx.Client (proxies, custom transport, ...)
)
```

Per call: `client.decide(..., timeout=10.0, request_id="my-trace-id")`. Use `with Krun() as client:` or
`client.close()` to release connections.

### Timeouts

The default is **70 seconds** because a Serverless cold start can use most of the API's own 60-second deadline. The
timeout applies to each attempt (connect and read). It cannot be disabled: `None`, `0` and `inf` are rejected. When it
elapses you get `APITimeoutError`.

### Retries

The API already retries its model backend, so the SDK retries only a little:

| call | retried on | default |
|---|---|---|
| `decide()`, `models()` | connection errors, HTTP 502 / 503 / 504 | 1 retry (`max_retries`) |
| `feedback()` | never: it writes a row and the API has no idempotency key | – |

- The wait between attempts follows `Retry-After` when the API sends it (capped at 10 s). Otherwise it is 0.5 s,
  then 1 s, 2 s, and so on.
- SDK timeouts (`APITimeoutError`) are not retried.
- 4xx errors and 500 are not retried.
- A retried `decide()` can count one extra decision against usage if the first attempt reached the model.
- Use `max_retries=0` to disable retries.

### Request IDs

Every decision gets an id from the `X-Request-ID` response header. The SDK puts it on `result.request_id`. Errors
carry it too (`exc.request_id`, from the error body or the header), so you can quote it to support. You can send your
own with `decide(..., request_id="...")` (1–128 characters of `[A-Za-z0-9._:-]`). The API keeps it and returns it.

## Errors

All errors inherit from `krun.KrunError` and expose `message`, `request_id`, `status_code` and `error_code` when
available:

```text
KrunError
├── APIError                     the API answered with an error (status_code always set)
│   ├── InvalidRequestError      400/413  INVALID_REQUEST, INVALID_OPTIONS, PAYLOAD_TOO_LARGE
│   ├── AuthenticationError      401      UNAUTHORIZED
│   ├── NotFoundError            404      NOT_FOUND
│   ├── RateLimitError           429      RATE_LIMITED       (.retry_after)
│   ├── QuotaExceededError       429      QUOTA_EXCEEDED
│   ├── InferenceFailedError     502      INFERENCE_FAILED
│   ├── ServiceUnavailableError  503      UPSTREAM_UNAVAILABLE (.retry_after)
│   ├── UpstreamTimeoutError     504      UPSTREAM_TIMEOUT
│   └── InternalServerError      500      INTERNAL_ERROR
├── APIConnectionError           no HTTP response (DNS, refused, reset, TLS)
│   └── APITimeoutError          the SDK timeout elapsed
└── APIResponseValidationError   a 2xx response did not match the contract
```

```python
import krun

try:
    client.decide(context="...", questions={...})
except krun.InvalidRequestError as e:
    print(e.error_code, e.message, e.request_id)   # INVALID_OPTIONS questions.q: 1 options given; ...
except krun.RateLimitError as e:
    time.sleep(e.retry_after or 1)
except krun.KrunError as e:
    log.warning("krun failed: %s", e)
```

Wrong argument types (e.g. `context=None`) raise `TypeError` before any request is made.

## Privacy and logging

- No telemetry: the SDK sends requests only to the Krun API and records nothing itself. Usage is recorded by the
  API.
- Silent by default. The SDK logs to the `krun` logger at DEBUG, and only method, path, status, request id and
  retries. It never logs the API key, context, options or answers. To see these logs:
  `logging.getLogger("krun").setLevel(logging.DEBUG)`.
- The API key never appears in `repr(client)`, error messages or logs.
- Requests carry `User-Agent: krun-python/<version>`.

## Examples

[`examples/`](examples/): `basic_decision.py`, `multiple_questions.py`, `tool_routing.py`, `feedback.py`,
`async_decision.py`, `error_handling.py`.

```bash
export KRUN_API_KEY=krun_live_...
python examples/basic_decision.py
```

## Development

```bash
uv sync                       # Python 3.10+ venv with dev tools
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                 # unit + contract + mock-server integration tests (no network)
uv build                      # dist/krun_ai-0.1.0-py3-none-any.whl + .tar.gz
uv run python scripts/bench.py   # SDK overhead vs raw httpx on a local mock server
```

### Tests

- `tests/test_client.py`: unit tests with `httpx.MockTransport`. Covers auth header, serialization, parsing,
  `choice=None`, `input_tokens`, `X-Request-ID`, the full error mapping, retries, timeouts, base URL, feedback,
  models, key redaction and the absence of output tokens.
- `tests/test_integration.py`: real HTTP against `tests/mock_server.py`, a local server that validates every
  request body against the OpenAPI snapshot. Covers sync and async clients, real timeouts, connection refused and
  retry after 503.
- `tests/test_contract.py`: offline checks that the hand-written models match `openapi/openapi.json` (every error
  code mapped, answer/usage/feedback fields, OpenAPI examples round-trip).
- `tests/test_live.py`: **opt-in** production checks, never run in CI:

  ```bash
  KRUN_LIVE_TESTS=1 KRUN_API_KEY=krun_live_... uv run pytest tests/test_live.py -v   # 1 real decision
  KRUN_API_KEY=krun_live_... uv run python scripts/smoke.py                          # manual smoke, 1 real decision
  ```

### OpenAPI contract and drift

The public API is hand-written, and `https://api.krun.ai/openapi.json` is the reference:

- `openapi/openapi.json` is the snapshot this release was written against. Its version and hash are pinned in
  `src/krun/_constants.py`, and `tests/test_contract.py` fails if they disagree.
- `python scripts/check_openapi.py` compares production with the snapshot. It exits 1 on drift and prints what
  changed. `--update` refreshes the snapshot.
- The `contract-drift` workflow runs that check weekly and on demand. The unit tests never use the network.

## Versioning and releases

SemVer, starting at `0.1.0`. SDK versions are independent of model versions (`krun-one-v0`) and of the API version
(v1). See [CHANGELOG.md](CHANGELOG.md).

Release flow (not yet executed):

1. Bump `version` in `pyproject.toml` and `src/krun/_version.py`, and update `CHANGELOG.md`.
2. Merge to `main`. CI runs lint, type check, tests on 3.10–3.13 and the package build.
3. Tag `vX.Y.Z` and publish a GitHub Release.
4. `.github/workflows/publish.yml` runs on the release. It checks that the tag matches the package version, builds
   the wheel and sdist, and uploads them to PyPI with **Trusted Publishing** (OIDC, no stored token).

Trusted Publishing is configured on PyPI (owner `krun-ai`, repo `krun-python`, workflow `publish.yml`, environment
`pypi`); the `pypi` environment exists in the GitHub repo settings.

## License

[Apache License 2.0](LICENSE).
