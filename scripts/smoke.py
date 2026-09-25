"""Manual production smoke test: one real decision (2 questions), one feedback row, models, and error mapping.

    KRUN_API_KEY=krun_live_... uv run python scripts/smoke.py [--base-url https://api.krun.ai]

Cost: one GPU job. The feedback row is tagged {"source": "krun-python-smoke"}. The key is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import krun


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=krun.DEFAULT_BASE_URL)
    args = parser.parse_args()
    client = krun.Krun(base_url=args.base_url)
    print(f"krun-python {krun.__version__} -> {client.base_url}")

    models = client.models()
    print("models:", [(m.id, m.status) for m in models])

    try:  # validation error: rejected before any GPU work
        client.decide(context="x", questions={"q": {"type": "choice", "options": {"only": ""}}})
    except krun.InvalidRequestError as e:
        print(f"invalid request mapped: {type(e).__name__} code={e.error_code} status={e.status_code} "
              f"request_id={e.request_id}")  # fmt: skip

    try:
        krun.Krun(api_key="krun_live_invalidinvalidinvalidinvalid00", base_url=args.base_url).models()
    except krun.AuthenticationError as e:
        print(f"bad key mapped: {type(e).__name__} code={e.error_code} request_id={e.request_id}")

    t0 = time.perf_counter()
    result = client.decide(
        context="I was charged twice for my order, can you refund the duplicate payment?",
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
    elapsed = time.perf_counter() - t0
    print(f"decide: {elapsed:.2f}s model={result.model} request_id={result.request_id} "
          f"input_tokens={result.usage.input_tokens}")  # fmt: skip
    for qid in result.answers:
        a = result.choice(qid)
        print(f"  {qid}: choice={a.choice!r} confidence={a.confidence:.4f} abstain={a.abstain} "
              f"status={a.abstention_status} probabilities={a.probabilities}")  # fmt: skip

    fb = client.feedback(
        request_id=result.request_id,
        question_id="department",
        correct=result.choice("department").choice == "billing",
        expected_decision="billing",
        metadata={"source": "krun-python-smoke"},
    )
    print(f"feedback: id={fb.id} question_id={fb.question_id} created_at={fb.created_at.isoformat()}")

    async def async_models() -> list[str]:
        async with krun.AsyncKrun(base_url=args.base_url) as ac:
            return [m.id for m in await ac.models()]

    print("async models:", asyncio.run(async_models()))
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
