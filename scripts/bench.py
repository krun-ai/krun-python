"""SDK overhead vs raw httpx against a local mock server (no network, no cost).

    uv run python scripts/bench.py [-n 2000]

Both sides send the same 3-question request over keep-alive to the same local server; the difference is what the
SDK adds (serialization, response parsing into dataclasses, error/retry plumbing).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from krun import Krun
from krun._models import decide_body, parse_decision
from tests.mock_server import MockKrunAPI

QUESTIONS = {
    "department": {"type": "choice", "options": {"shipping": "Shipping", "returns": "Returns", "billing": "Billing"}},
    "priority": {"type": "choice", "options": {"low": "", "normal": "", "high": ""}},
    "tool": {"type": "choice", "task_type": "tool", "options": {"a": "Tool A", "b": "Tool B", "c": "Tool C"}},
}
KEY = "krun_test_mockkey"


def timed(fn: Callable[[], object], n: int) -> list[float]:
    for _ in range(50):
        fn()
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1e6)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=2000)
    n = parser.parse_args().n
    with MockKrunAPI(api_key=KEY) as api:
        raw_client = httpx.Client(base_url=api.url, headers={"Authorization": f"Bearer {KEY}"})
        body = {"context": "Customer wants to return an item.", "questions": QUESTIONS}

        def raw() -> None:
            raw_client.post("/v1/decide", json=body).json()

        sdk = Krun(api_key=KEY, base_url=api.url)

        def via_sdk() -> None:
            sdk.decide(context="Customer wants to return an item.", questions=QUESTIONS)

        # Alternate to spread noise evenly.
        raw_t, sdk_t = [], []
        for _ in range(4):
            raw_t += timed(raw, n // 4)
            sdk_t += timed(via_sdk, n // 4)

    sample = {"model": "krun-one-v0", "answers": {q: {"type": "choice", "choice": "a", "confidence": 0.5,
              "probabilities": {"a": 0.7, "b": 0.2, "c": 0.1}, "abstain": False, "abstention_status": "advisory"}
              for q in QUESTIONS}, "usage": {"input_tokens": 99}}  # fmt: skip
    pure = timed(lambda: parse_decision(json.loads(json.dumps(sample)), "req"), n)
    ser = timed(lambda: json.dumps(decide_body("ctx", QUESTIONS, None)).encode(), n)

    def p(xs: list[float], q: float) -> float:
        return statistics.quantiles(xs, n=100)[int(q) - 1]

    print(f"n={n} (local mock server, keep-alive, 3 questions)")
    print(f"raw httpx    p50 {p(raw_t, 50):8.1f} us  p99 {p(raw_t, 99):8.1f} us")
    print(f"krun SDK     p50 {p(sdk_t, 50):8.1f} us  p99 {p(sdk_t, 99):8.1f} us")
    print(f"SDK overhead p50 {p(sdk_t, 50) - p(raw_t, 50):8.1f} us")
    print(f"serialize    p50 {p(ser, 50):8.1f} us | parse p50 {p(pure, 50):8.1f} us (json decode + dataclasses)")


if __name__ == "__main__":
    main()
