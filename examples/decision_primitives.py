"""Choice, noul and score questions about the same context, answered in a single API call."""

from krun import Krun, NoulQuestion, ScoreQuestion

client = Krun()

result = client.decide(
    context="Customer says this is the third time exports failed and wants a human immediately.",
    questions={
        # choice: pick one option
        "department": {"type": "choice", "options": {"billing": "", "support": "", "sales": ""}},
        # noul: probability that a yes/no proposition holds
        "needs_human": NoulQuestion(
            "Is the customer asking to speak with a human?",
            criteria={"true": "Explicitly requests a person or human agent"},
        ),
        # score: ordered levels, lowest first (order is meaning: never shuffle it)
        "severity": ScoreQuestion(
            "How severe is the reported issue?",
            ["Minor issue", "Feature degraded", "Blocking issue"],
        ),
    },
)

department = result.choice("department")
print("department:", department.choice, f"(margin {department.confidence:.2f})")

needs_human = result.noul("needs_human")
print(f"needs_human: {needs_human.noul:.1%} probability")

severity = result.score("severity")
print(f"severity: {severity.score:.2f} / {len(severity.legend) - 1}")
for index, level in severity.legend.items():
    print(f"  {level:<18} {severity.probabilities[index]:.0%}")
