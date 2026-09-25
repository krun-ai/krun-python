"""Opt-in checks against production (https://api.krun.ai). Never run by default or in CI.

    KRUN_LIVE_TESTS=1 KRUN_API_KEY=krun_live_... uv run pytest tests/test_live.py -v

Costs: `test_live_openapi_matches_snapshot` and `test_live_models` are free; `test_live_decide_and_feedback` makes one
real decision (one GPU job) and stores one feedback row tagged `{"source": "krun-python-smoke"}`.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from krun import DEFAULT_BASE_URL, InvalidRequestError, Krun

from .test_contract import SNAPSHOT, canonical_sha256

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("KRUN_LIVE_TESTS") != "1", reason="set KRUN_LIVE_TESTS=1 to call production"),
]

BASE_URL = os.environ.get("KRUN_BASE_URL", DEFAULT_BASE_URL)


def test_live_openapi_matches_snapshot() -> None:
    live = httpx.get(f"{BASE_URL}/openapi.json", timeout=30).raise_for_status().json()
    assert canonical_sha256(live) == canonical_sha256(json.loads(SNAPSHOT.read_text())), (
        "production OpenAPI drifted from openapi/openapi.json: run scripts/check_openapi.py"
    )


@pytest.fixture
def client() -> Krun:
    if not os.environ.get("KRUN_API_KEY"):
        pytest.skip("KRUN_API_KEY not set")
    return Krun(base_url=BASE_URL)


def test_live_models(client: Krun) -> None:
    assert any(m.id == "krun-one-v0" for m in client.models())


def test_live_invalid_request_is_mapped(client: Krun) -> None:
    # Rejected by validation before any GPU job is created: free.
    with pytest.raises(InvalidRequestError) as exc_info:
        client.decide(context="x", questions={"q": {"type": "choice", "options": {"only": ""}}})
    assert exc_info.value.error_code == "INVALID_OPTIONS"
    assert exc_info.value.request_id


def test_live_decide_and_feedback(client: Krun) -> None:
    result = client.decide(
        context="I was charged twice for my order, can you move it to express shipping?",
        questions={
            "department": {
                "type": "choice",
                "options": {
                    "shipping": "Shipping and delivery issues",
                    "returns": "Returns and refunds",
                    "billing": "Billing and payment issues",
                },
            },
            "tool": {
                "type": "choice",
                "task_type": "tool",
                "options": {"refund_payment": "Refund a duplicate charge", "calendar_search": "Search calendar events"},
            },
        },
    )
    assert list(result.answers) == ["department", "tool"]
    assert result.request_id
    assert result.choice("tool").abstention_status == "advisory"
    fb = client.feedback(
        request_id=result.request_id,
        question_id="department",
        correct=True,
        metadata={"source": "krun-python-smoke"},
    )
    assert fb.request_id == result.request_id
