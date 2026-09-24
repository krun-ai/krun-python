# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

Initial SDK, licensed under Apache-2.0.

- `Krun` (sync) and `AsyncKrun` (async) clients for Krun API v1 (OpenAPI `1.0.0-beta`).
- `decide()`: context + 1–16 named `choice` questions, including tool routing (`task_type="tool"`), returns
  `DecisionResult` (`model`, `answers`, `usage.input_tokens`, `request_id` from `X-Request-ID`).
- `feedback()` and `models()`.
- Error hierarchy mapped from the API's error codes, carrying `message`, `request_id`, `status_code`, `error_code`.
- 70 s default timeout; conservative retries (decide/models only, connection errors and 502/503/504, default 1).
- OpenAPI snapshot, contract tests and drift check.
