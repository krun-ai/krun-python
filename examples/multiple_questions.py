"""Several questions about the same context, answered in a single API call."""

from krun import Krun

client = Krun()

result = client.decide(
    context="My card was charged twice for the same order and I need the money back today, this is ridiculous.",
    questions={
        "department": {
            "type": "choice",
            "options": {
                "shipping": "Shipping and delivery issues",
                "returns": "Returns and refunds",
                "billing": "Billing and payment issues",
            },
        },
        "priority": {
            "type": "choice",
            "options": {"low": "Can wait", "normal": "Normal priority", "high": "Needs quick attention"},
        },
        "sentiment": {
            # Label-only options: "" (or None) as the description.
            "type": "choice",
            "options": {"positive": "", "neutral": "", "negative": ""},
        },
    },
)

for question_id, answer in result.answers.items():  # same ids, same order as the request
    if answer.abstain:
        best_guess = max(answer.probabilities, key=answer.probabilities.__getitem__)
        print(f"{question_id:<10} abstained (best guess {best_guess!r}, margin {answer.confidence:.3f})")
    else:
        print(f"{question_id:<10} {answer.choice!r} (margin {answer.confidence:.3f}, {answer.abstention_status})")

print("input tokens:", result.usage.input_tokens, "| request id:", result.request_id)
