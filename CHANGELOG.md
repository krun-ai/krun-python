# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-09-30

First PyPI release since 0.1.0: it also ships everything listed under [0.2.0], which was never published.

### Krun One V1

Krun One V1 is live on api.krun.ai: model id `krun-one-v1` (the API's default model; `krun-one-v0` and
`krun-one-v0.3` remain accepted as aliases), multimodal contexts, `multi` questions and `/v1/assets`. Existing
text-only usage is unchanged (a `context="..."` call sends byte-identical requests).

- `decide(context=...)` also accepts a list of content parts: `TextPart`, `ImagePart`, `DocumentPart`, `AudioPart`
  or plain dicts (`TextPartParam`, ... `TypedDict`s). Only types are checked; limits are enforced by the API.
- `multi` questions: `MultiQuestion` / `MultiQuestionParam`, answered by `MultiAnswer` (`values`, `probabilities`);
  `Answer` includes it and `DecisionResult.multi(id)` returns it typed. Unknown answer types still raise
  `APIResponseValidationError` ("please upgrade").
- `client.assets.create(file, mime_type=...)` (bytes, path or binary file object; raw body, MIME type inferred from
  the extension when omitted), `client.assets.get(id)`, `client.assets.delete(id)`, also on `AsyncKrun`. Returns
  `Asset` / `DeletedAsset`. Uploads and deletes are never retried.
- New error codes mapped onto existing classes (`InvalidRequestError`, `NotFoundError`, `PermissionDeniedError`,
  `InferenceFailedError`, `InternalServerError`); HTTP 410 and 415 without a code map to `NotFoundError` and
  `InvalidRequestError`. New `KrunError.code` alias of `error_code`.
- OpenAPI snapshot refreshed from production (V1 contract, same `info.version` `1.0.0-beta`, new hash).

### Changed

- Default timeout raised from 70 s to **180 s**: a Krun One V1 cold start can take up to ~150 s (the API waits up
  to 150 s for the model).
- Docs and docstrings no longer describe V1 as upcoming; `model` defaults to the API's default model, `krun-one-v1`.

## [0.2.0] - Unreleased

Never published to PyPI; these changes ship in [0.3.0](#030---2026-09-30).

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
