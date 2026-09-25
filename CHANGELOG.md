# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - Unreleased

Decision primitives (Krun API OpenAPI snapshot refreshed).

- New question types next to `choice`: **`noul`** (probability that a yes/no proposition holds) and **`score`**
  (rate on ordered levels). Pass them as dicts (`{"type": "noul", "instructions": ...}`,
  `{"type": "score", "instructions": ..., "levels": [...]}`) or as `NoulQuestion` / `ScoreQuestion`. Types can be
  mixed in one `decide()` call.
- Answers are decoded by `type` into `ChoiceAnswer`, `NoulAnswer` (`noul`) or `ScoreAnswer` (`score`, `confidence`,
  `legend`, `probabilities`). `Answer` is now the union of the three; `DecisionResult.choice(id)`, `.noul(id)` and
  `.score(id)` return the typed answer.
- `feedback(expected=...)`: typed expected value (`{"type": "noul", "value": True}`, `{"type": "score", "value": 2}`,
  `{"type": "choice", "value": "billing"}`). `expected_decision` still works for choice.
- New error classes for codes the API already returns: `InsufficientCreditsError` (402), `PermissionDeniedError`,
  `ConflictError`.
- **Typing note:** `DecisionResult.answers` values are now `ChoiceAnswer | NoulAnswer | ScoreAnswer`. Runtime behaviour
  for choice questions is unchanged, but type checkers need narrowing: use `result.choice("id")` or
  `isinstance(answer, ChoiceAnswer)` where 0.1.0 code read `result.answers["id"].choice`.

## [0.1.0] - 2026-09-24

Initial SDK, licensed under Apache-2.0.

- `Krun` (sync) and `AsyncKrun` (async) clients for Krun API v1 (OpenAPI `1.0.0-beta`).
- `decide()`: context + 1–16 named `choice` questions, including tool routing (`task_type="tool"`), returns
  `DecisionResult` (`model`, `answers`, `usage.input_tokens`, `request_id` from `X-Request-ID`).
- `feedback()` and `models()`.
- Error hierarchy mapped from the API's error codes, carrying `message`, `request_id`, `status_code`, `error_code`.
- 70 s default timeout; conservative retries (decide/models only, connection errors and 502/503/504, default 1).
- OpenAPI snapshot, contract tests and drift check.
