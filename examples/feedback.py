"""Report whether an answer was right, so Krun can measure quality on real traffic."""

from krun import Krun, NotFoundError

client = Krun()

result = client.decide(
    context="I was charged twice for the same order.",
    questions={
        "department": {
            "type": "choice",
            "options": {"shipping": "", "returns": "", "billing": ""},
        }
    },
)
print("decided:", result.choice("department").choice, "| request id:", result.request_id)

# Later, once a human (or a downstream system) knows the right answer:
try:
    fb = client.feedback(
        request_id=result.request_id,
        question_id="department",  # always required, also for single-question requests
        correct=result.choice("department").choice == "billing",
        expected_decision="billing",
        metadata={"source": "examples/feedback.py"},  # optional; no personal data
    )
    print("feedback stored:", fb.id, fb.created_at.isoformat())
except NotFoundError:
    print("unknown request id for this project")
