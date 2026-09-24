"""Catching SDK errors. Every error carries message, status_code, error_code and request_id when available."""

import krun

client = krun.Krun(max_retries=1, timeout=70)

try:
    client.decide(
        context="Customer wants to return an item.",
        questions={"department": {"type": "choice", "options": {"returns": ""}}},  # 1 option: invalid
    )
except krun.InvalidRequestError as e:
    print(f"invalid request [{e.error_code}] {e.message} (request_id={e.request_id})")
except krun.AuthenticationError:
    print("check KRUN_API_KEY")
except krun.RateLimitError as e:
    print(f"slow down; retry in {e.retry_after}s")
except krun.QuotaExceededError:
    print("monthly quota exhausted")
except (krun.UpstreamTimeoutError, krun.ServiceUnavailableError, krun.APITimeoutError) as e:
    print(f"model temporarily unavailable, try again later: {e}")
except krun.KrunError as e:
    print(f"other Krun error: {e}")
