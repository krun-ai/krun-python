"""AsyncKrun: the same API as coroutines; run several decisions concurrently."""

import asyncio

from krun import AsyncKrun

QUESTIONS = {
    "department": {
        "type": "choice",
        "options": {
            "shipping": "Shipping and delivery issues",
            "returns": "Returns and refunds",
            "billing": "Billing and payment issues",
        },
    }
}

MESSAGES = [
    "Where is my package? It was due on Monday.",
    "I want to send these shoes back.",
    "Why is there a second charge on my card?",
]


async def main() -> None:
    async with AsyncKrun() as client:
        results = await asyncio.gather(*(client.decide(context=m, questions=QUESTIONS) for m in MESSAGES))
    for message, result in zip(MESSAGES, results, strict=True):
        print(f"{result.choice('department').choice!s:<9} <- {message}")


asyncio.run(main())
