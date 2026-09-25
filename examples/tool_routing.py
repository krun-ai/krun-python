"""Tool / function routing: `task_type: "tool"`.

Abstention for tool routing is `advisory`: `abstain` is a hint, not a validated guarantee. Keep your own guard
(e.g. a "none of these" path) for requests that match no tool.
"""

from krun import ChoiceQuestion, Krun

client = Krun()

tools = {
    "calendar_search": "Search calendar events by date, person or title",
    "send_email": "Send an email to a contact",
    "weather_lookup": "Get the weather forecast for a city",
}

result = client.decide(
    context="Find my meetings tomorrow.",
    questions={"tool": ChoiceQuestion(options=tools, task_type="tool")},
    # dict form, equivalent: {"tool": {"type": "choice", "task_type": "tool", "options": tools}}
)

answer = result.choice("tool")
print("abstention_status:", answer.abstention_status)  # "advisory" for tool routing
if answer.choice is None:
    print("no tool selected; probabilities:", answer.probabilities)
else:
    print(f"call {answer.choice} (margin {answer.confidence:.3f})")
