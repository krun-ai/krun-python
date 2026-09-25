"""One question, one answer.  Run: KRUN_API_KEY=krun_live_... python examples/basic_decision.py"""

from krun import Krun

client = Krun()  # reads KRUN_API_KEY

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

answer = result.choice("department")
print("choice:          ", answer.choice)  # None when the model abstains
print("confidence:      ", answer.confidence)  # top-1 minus top-2 probability, not P(correct)
print("probabilities:   ", answer.probabilities)
print("abstain:         ", answer.abstain, f"({answer.abstention_status})")
print("input tokens:    ", result.usage.input_tokens)
print("request id:      ", result.request_id)
