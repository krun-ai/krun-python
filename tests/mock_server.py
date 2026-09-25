"""A local HTTP server that speaks the Krun API contract (for contract/integration tests).

* Validates every request body against the committed OpenAPI snapshot (`openapi/openapi.json`) and answers
  `400 INVALID_REQUEST` like the real API when it does not match.
* Checks `Authorization: Bearer <key>`, echoes/generates `X-Request-ID`.
* Answers `/v1/decide` with one well-formed answer per question (first option wins; a context containing
  "unsure" makes every answer abstain), `/v1/feedback` and `/v1/models` like production.
* `enqueue()` scripts the next responses (status, body, headers, delay) to simulate errors and slowness.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

OPENAPI = json.loads((Path(__file__).resolve().parents[1] / "openapi" / "openapi.json").read_text())


def schema_validator(name: str) -> Draft202012Validator:
    # OpenAPI 3.1 schemas are JSON Schema 2020-12; the document root carries `components` for `$ref` resolution.
    return Draft202012Validator({**OPENAPI, "$ref": f"#/components/schemas/{name}"})


VALIDATORS = {name: schema_validator(name) for name in ("DecideRequest", "FeedbackRequest")}


@dataclass
class Scripted:
    status: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    raw: bytes | None = None


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


class MockKrunAPI:
    def __init__(self, api_key: str = "krun_test_mockkey") -> None:
        self.api_key = api_key
        self.requests: list[Recorded] = []
        self.script: list[Scripted] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                # Headers and body are written separately: without this, Nagle + delayed ACK add ~40 ms per request.
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def log_message(self, *args: Any) -> None:  # silence
                pass

            def do_GET(self) -> None:
                outer._handle(self)

            def do_POST(self) -> None:
                outer._handle(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    def __enter__(self) -> MockKrunAPI:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def enqueue(self, *responses: Scripted) -> None:
        with self._lock:
            self.script.extend(responses)

    # ----------------------------------------------------------------------------------------------------------------

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode("utf-8", "replace")
        with self._lock:
            self.requests.append(Recorded(h.command, h.path, {k.lower(): v for k, v in h.headers.items()}, body))
            scripted = self.script.pop(0) if self.script else None
        incoming = h.headers.get("X-Request-ID")
        rid = incoming if incoming else f"req_{uuid.uuid4().hex}"
        if scripted is not None:
            time.sleep(scripted.delay)
            payload = scripted.raw if scripted.raw is not None else json.dumps(scripted.body).encode()
            return self._send(h, scripted.status, payload, {"X-Request-ID": rid, **scripted.headers})
        status, out = self._route(h.command, h.path, h.headers.get("Authorization"), body, rid)
        self._send(h, status, json.dumps(out).encode(), {"X-Request-ID": rid})

    @staticmethod
    def _send(h: BaseHTTPRequestHandler, status: int, payload: bytes, headers: dict[str, str]) -> None:
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(payload)))
        for k, v in headers.items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(payload)

    @staticmethod
    def _error(status: int, code: str, message: str, rid: str) -> tuple[int, Any]:
        return status, {"error": {"code": code, "message": message, "request_id": rid}}

    def _route(self, method: str, path: str, auth: str | None, body: Any, rid: str) -> tuple[int, Any]:
        if auth != f"Bearer {self.api_key}":
            return self._error(401, "UNAUTHORIZED", "missing, invalid or revoked API key", rid)
        if (method, path) == ("GET", "/v1/models"):
            return 200, {"object": "list", "data": [{"id": "krun-one-v0", "object": "model", "status": "available"}]}
        if (method, path) == ("POST", "/v1/feedback"):
            errors = sorted(VALIDATORS["FeedbackRequest"].iter_errors(body), key=str)
            if errors:
                return self._error(400, "INVALID_REQUEST", errors[0].message, rid)
            return 201, {
                "id": f"fb_{uuid.uuid4().hex}",
                "object": "feedback",
                "request_id": body["request_id"],
                "question_id": body["question_id"],
                "created_at": "2026-09-24T12:34:56.123456789Z",
            }
        if (method, path) == ("POST", "/v1/decide"):
            errors = sorted(VALIDATORS["DecideRequest"].iter_errors(body), key=str)
            if errors:
                return self._error(400, "INVALID_REQUEST", errors[0].message, rid)
            answers = {}
            tokens = 0
            for qid, q in body["questions"].items():
                if q["type"] == "noul":
                    answers[qid] = {"type": "noul", "noul": 0.25 if "unsure" in body["context"] else 0.973}
                    tokens += 12 + len(body["context"].split())
                    continue
                if q["type"] == "score":
                    k = len(q["levels"])
                    if not 2 <= k <= 16:
                        return self._error(400, "INVALID_REQUEST", f"questions.{qid}: {k} levels given", rid)
                    probs = {str(i): round(1 / k, 6) for i in range(k)}
                    answers[qid] = {
                        "type": "score",
                        "score": round(sum(i * p for i, p in enumerate(probs.values())), 6),
                        "confidence": 0.0 if k == 2 else 0.25,
                        "legend": {str(i): lv for i, lv in enumerate(q["levels"])},
                        "probabilities": probs,
                    }
                    tokens += 12 + len(body["context"].split()) + 3 * k
                    continue
                options = list(q["options"])
                if not 2 <= len(options) <= 64:
                    message = f"questions.{qid}: {len(options)} options given; between 2 and 64 are required"
                    return self._error(400, "INVALID_OPTIONS", message, rid)
                unsure = "unsure" in body["context"]
                rest = 0.3 if unsure else 0.1
                probs = {o: round(rest / (len(options) - 1), 6) for o in options}
                probs[options[0]] = round(1 - rest, 6)
                second = max(v for k, v in probs.items() if k != options[0])
                label_only = all(not d for d in q["options"].values())
                calibrated = q.get("task_type") in (None, "intent") and label_only
                answers[qid] = {
                    "type": "choice",
                    "choice": None if unsure else options[0],
                    "confidence": round(probs[options[0]] - second, 6),
                    "probabilities": probs,
                    "abstain": unsure,
                    "abstention_status": "calibrated" if calibrated else "advisory",
                }
                tokens += 10 + len(body["context"].split()) + 3 * len(options)
            model = body.get("model") or "krun-one-v0"
            return 200, {"model": model, "answers": answers, "usage": {"input_tokens": tokens}}
        return self._error(404, "NOT_FOUND", "no route", rid)
