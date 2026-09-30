"""Krun One V1: content-part contexts, `multi` questions, assets and the multimodal error codes."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from krun import (
    APIError,
    APIResponseValidationError,
    Asset,
    AsyncKrun,
    AudioPart,
    ChoiceAnswer,
    DeletedAsset,
    DocumentPart,
    ImagePart,
    InferenceFailedError,
    InternalServerError,
    InvalidRequestError,
    Krun,
    MultiAnswer,
    MultiQuestion,
    NotFoundError,
    NoulQuestion,
    PermissionDeniedError,
    ScoreQuestion,
    ServiceUnavailableError,
    TextPart,
)
from krun._client import _MIME_BY_EXTENSION
from krun._models import decide_body

from .mock_server import OPENAPI, MockKrunAPI, Scripted

KEY = "krun_test_mockkey"
ASSET_ID = "asset_7fQ2mZkP0aLxAAAAAAAAAAAA"
ASSET_JSON: dict[str, Any] = {
    "id": ASSET_ID,
    "object": "asset",
    "mime_type": "image/png",
    "size_bytes": 4,
    "sha256": "ab" * 32,
    "created_at": "2026-09-29T12:00:00.123456789Z",
    "expires_at": "2026-09-30T12:00:00Z",
}
MULTI_OK: dict[str, Any] = {
    "model": "krun-one-v1",
    "answers": {
        "tags": {"type": "multi", "values": ["invoice", "overdue"],
                 "probabilities": {"invoice": 0.97, "receipt": 0.04, "overdue": 0.81}},
        "paid": {"type": "noul", "noul": 0.12},
    },
    "usage": {"input_tokens": 300},
}  # fmt: skip

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: Any) -> tuple[Krun, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return Krun(api_key=KEY, http_client=httpx.Client(transport=httpx.MockTransport(wrapped)), **kwargs), seen


def make_async_client(handler: Handler, **kwargs: Any) -> tuple[AsyncKrun, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    return AsyncKrun(api_key=KEY, http_client=httpx.AsyncClient(transport=transport), **kwargs), seen


def ok(body: Any, status: int = 200) -> Handler:
    return lambda _req: httpx.Response(status, json=body, headers={"X-Request-ID": "req_mm"})


def error(status: int, code: str | None) -> Handler:
    detail: dict[str, Any] = {"message": "details", "request_id": "req_e"}
    if code is not None:
        detail["code"] = code
    return lambda _req: httpx.Response(status, json={"error": detail})


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("krun._client.time.sleep", slept.append)
    return slept


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------------------------------------- backward compatibility


def test_legacy_string_context_wire_body_is_byte_identical() -> None:
    """Exact bytes produced by krun-python 0.2.0 (before content parts existed) for this call."""
    client, seen = make_client(ok({"model": "m", "answers": {}, "usage": {"input_tokens": 1}}))
    client.decide(
        context="Customer wants to return an item. Olá “quotes”",
        questions={
            "department": {"type": "choice", "options": {"shipping": "Shipping", "returns": None}},
            "h": NoulQuestion("Human?"),
            "s": ScoreQuestion("How?", ["a", "b"]),
        },
        model="krun-one-v0",
    )
    assert seen[0].content == (
        b'{"context": "Customer wants to return an item. Ol\xc3\xa1 \xe2\x80\x9cquotes\xe2\x80\x9d", "questions": '
        b'{"department": {"type": "choice", "options": {"shipping": "Shipping", "returns": null}}, "h": {"type": '
        b'"noul", "instructions": "Human?"}, "s": {"type": "score", "instructions": "How?", "levels": ["a", "b"]}}, '
        b'"model": "krun-one-v0"}'
    )
    assert seen[0].headers["content-type"] == "application/json"


# ---------------------------------------------------------------------------------------------------- content parts


def test_context_parts_serialization() -> None:
    body = decide_body(
        [
            TextPart("Is this invoice paid?"),
            {"type": "text", "text": "raw dict", "id": "t2"},
            DocumentPart(ASSET_ID, id="doc"),
            ImagePart(ASSET_ID),
            AudioPart(ASSET_ID),
        ],
        {"q": {"type": "noul", "instructions": "Paid?"}},
        None,
    )
    assert body["context"] == [
        {"type": "text", "text": "Is this invoice paid?"},
        {"type": "text", "text": "raw dict", "id": "t2"},
        {"type": "document", "asset_id": ASSET_ID, "id": "doc"},
        {"type": "image", "asset_id": ASSET_ID},
        {"type": "audio", "asset_id": ASSET_ID},
    ]


def test_context_tuple_and_contract_example_round_trip() -> None:
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": "Is this invoice paid?"},
        {"type": "document", "asset_id": ASSET_ID},
    ]
    questions: dict[str, Any] = {
        "paid": {"type": "noul", "instructions": "Is the document marked as paid?"},
        "tags": {"type": "multi", "options": {"invoice": None, "receipt": None, "overdue": None}},
    }
    example = {"context": parts, "questions": questions}
    assert decide_body(tuple(parts), questions, None) == example
    typed = decide_body(
        [TextPart("Is this invoice paid?"), DocumentPart(ASSET_ID)],
        {
            "paid": NoulQuestion("Is the document marked as paid?"),
            "tags": MultiQuestion({"invoice": None, "receipt": None, "overdue": None}),
        },
        None,
    )
    assert typed == example


@pytest.mark.parametrize("bad", [b"bytes", 42, None, {"type": "text", "text": "a dict, not a list"}])
def test_context_type_errors(bad: Any) -> None:
    client, seen = make_client(ok(MULTI_OK))
    with pytest.raises(TypeError, match="context"):
        client.decide(context=bad, questions={"q": {"type": "noul", "instructions": "?"}})
    bad_parts: Any = [TextPart("ok"), "not a part"]
    with pytest.raises(TypeError, match=r"context\[1\]"):
        client.decide(context=bad_parts, questions={"q": {"type": "noul", "instructions": "?"}})
    assert seen == []


# ------------------------------------------------------------------------------------------------------------ multi


def test_multi_question_serialization() -> None:
    assert MultiQuestion({"a": "", "b": None}).to_dict() == {"type": "multi", "options": {"a": "", "b": None}}
    assert MultiQuestion({"a": "x", "b": "y"}, instructions="Select all").to_dict() == {
        "type": "multi", "options": {"a": "x", "b": "y"}, "instructions": "Select all"
    }  # fmt: skip


def test_multi_answer_parsing() -> None:
    client, seen = make_client(ok(MULTI_OK))
    result = client.decide(
        context=[TextPart("Is this invoice paid?"), DocumentPart(ASSET_ID)],
        questions={"tags": MultiQuestion({"invoice": None, "receipt": None, "overdue": None}),
                   "paid": NoulQuestion("Paid?")},
    )  # fmt: skip
    sent = json.loads(seen[0].content)
    assert sent["context"][1] == {"type": "document", "asset_id": ASSET_ID}
    assert sent["questions"]["tags"] == {
        "type": "multi",
        "options": {"invoice": None, "receipt": None, "overdue": None},
    }
    tags = result.multi("tags")
    assert tags == MultiAnswer(
        type="multi", values=["invoice", "overdue"], probabilities={"invoice": 0.97, "receipt": 0.04, "overdue": 0.81}
    )
    assert list(tags.probabilities) == ["invoice", "receipt", "overdue"]
    assert isinstance(result.answers["tags"], MultiAnswer)
    with pytest.raises(TypeError):
        result.choice("tags")
    with pytest.raises(TypeError):
        result.multi("paid")


def test_multi_answer_may_select_nothing() -> None:
    body = {"model": "m", "answers": {"t": {"type": "multi", "values": [], "probabilities": {"a": 0.1, "b": 0.2}}}}
    client, _ = make_client(ok(body))
    assert client.decide(context="x", questions={"t": MultiQuestion({"a": "", "b": ""})}).multi("t").values == []


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "multi", "probabilities": {"a": 0.5}},
        {"type": "multi", "values": "a", "probabilities": {"a": 0.5}},
        {"type": "multi", "values": [1], "probabilities": {"a": 0.5}},
        {"type": "multi", "values": ["a"], "probabilities": {"a": "high"}},
        {"type": "multi", "values": ["a"]},
    ],
)
def test_malformed_multi_answer(answer: dict[str, Any]) -> None:
    client, _ = make_client(ok({"model": "m", "answers": {"t": answer}}))
    with pytest.raises(APIResponseValidationError):
        client.decide(context="x", questions={"t": MultiQuestion({"a": "", "b": ""})})


def test_unknown_answer_type_still_asks_to_upgrade_and_choice_is_unchanged() -> None:
    client, _ = make_client(ok({"model": "m", "answers": {"t": {"type": "rank", "order": []}}}))
    with pytest.raises(APIResponseValidationError, match="please upgrade"):
        client.decide(context="x", questions={"t": {"type": "rank"}})
    choice = {"type": "choice", "choice": "a", "confidence": 0.5, "probabilities": {"a": 0.75, "b": 0.25},
              "abstain": False, "abstention_status": "calibrated"}  # fmt: skip
    client, _ = make_client(ok({"model": "m", "answers": {"c": choice}}))
    result = client.decide(context="x", questions={"c": {"type": "choice", "options": {}}})
    assert isinstance(result.choice("c"), ChoiceAnswer)


# ----------------------------------------------------------------------------------------------------------- assets


EXPECTED_ASSET = Asset(
    id=ASSET_ID,
    object="asset",
    mime_type="image/png",
    size_bytes=4,
    sha256="ab" * 32,
    created_at=datetime(2026, 9, 29, 12, 0, 0, 123456, tzinfo=timezone.utc),
    expires_at=datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc),
)


def test_assets_create_from_bytes() -> None:
    client, seen = make_client(ok(ASSET_JSON, status=201))
    asset = client.assets.create(b"\x89PNG", mime_type="image/png")
    assert asset == EXPECTED_ASSET
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://api.krun.ai/v1/assets"
    assert req.headers["content-type"] == "image/png"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.content == b"\x89PNG"


def test_assets_create_from_path_infers_mime_type(tmp_path: Path) -> None:
    client, seen = make_client(ok(ASSET_JSON, status=201))
    for name, mime in [("scan.PDF", "application/pdf"), ("clip.mp3", "audio/mpeg"), ("a.jpg", "image/jpeg")]:
        path = tmp_path / name
        path.write_bytes(b"data-" + name.encode())
        client.assets.create(path)
        client.assets.create(str(path))
        assert seen[-1].headers["content-type"] == seen[-2].headers["content-type"] == mime
        assert seen[-1].content == b"data-" + name.encode()
    client.assets.create(tmp_path / "scan.PDF", mime_type="image/png")  # an explicit type wins
    assert seen[-1].headers["content-type"] == "image/png"


def test_assets_create_from_file_object(tmp_path: Path) -> None:
    client, seen = make_client(ok(ASSET_JSON, status=201))
    path = tmp_path / "voice.wav"
    path.write_bytes(b"RIFF....WAVE")
    with path.open("rb") as fh:
        client.assets.create(fh)
    assert seen[-1].headers["content-type"] == "audio/wav" and seen[-1].content == b"RIFF....WAVE"
    client.assets.create(io.BytesIO(b"hello"), mime_type="text/plain")
    assert seen[-1].headers["content-type"] == "text/plain" and seen[-1].content == b"hello"


def test_assets_create_argument_errors(tmp_path: Path) -> None:
    client, seen = make_client(ok(ASSET_JSON, status=201))
    with pytest.raises(ValueError, match="mime_type"):
        client.assets.create(b"bytes without a type")
    with pytest.raises(ValueError, match="mime_type"):
        client.assets.create(io.BytesIO(b"no name"))
    (tmp_path / "x.unknown").write_bytes(b"x")
    with pytest.raises(ValueError, match="mime_type"):
        client.assets.create(tmp_path / "x.unknown")
    text_file: Any = io.StringIO("text mode")
    with pytest.raises(TypeError, match="binary"):
        client.assets.create(text_file, mime_type="text/plain")
    not_a_file: Any = 12345
    with pytest.raises(TypeError):
        client.assets.create(not_a_file)
    with pytest.raises(TypeError):
        client.assets.create(b"x", mime_type="")
    assert seen == []


def test_assets_create_and_delete_are_never_retried(no_sleep: list[float]) -> None:
    client, seen = make_client(error(503, "UPSTREAM_UNAVAILABLE"), max_retries=3)
    with pytest.raises(ServiceUnavailableError):
        client.assets.create(b"x", mime_type="image/png")
    with pytest.raises(ServiceUnavailableError):
        client.assets.delete(ASSET_ID)
    assert len(seen) == 2 and no_sleep == []


def test_assets_get_is_retried_like_models(no_sleep: list[float]) -> None:
    responses = [
        httpx.Response(503, json={"error": {"code": "UPSTREAM_UNAVAILABLE"}}),
        httpx.Response(200, json=ASSET_JSON),
    ]
    client, seen = make_client(lambda _r: responses.pop(0))
    assert client.assets.get(ASSET_ID) == EXPECTED_ASSET
    assert len(seen) == 2 and len(no_sleep) == 1


def test_assets_get_and_delete() -> None:
    client, seen = make_client(ok(ASSET_JSON))
    assert client.assets.get(ASSET_ID) == EXPECTED_ASSET
    assert seen[0].method == "GET" and str(seen[0].url) == f"https://api.krun.ai/v1/assets/{ASSET_ID}"
    assert seen[0].content == b"" and "content-type" not in seen[0].headers
    client, seen = make_client(ok({"id": ASSET_ID, "object": "asset", "deleted": True}))
    assert client.assets.delete(ASSET_ID) == DeletedAsset(id=ASSET_ID, object="asset", deleted=True)
    assert seen[0].method == "DELETE" and seen[0].url.path == f"/v1/assets/{ASSET_ID}"
    client.assets.delete("asset_../../v1/models")  # path-escaped, never re-routed
    assert seen[1].url.raw_path == b"/v1/assets/asset_..%2F..%2Fv1%2Fmodels"
    with pytest.raises(TypeError):
        client.assets.get("")


def test_assets_malformed_responses() -> None:
    for broken in ({**ASSET_JSON, "size_bytes": "4"}, {**ASSET_JSON, "expires_at": "tomorrow"}, {"id": ASSET_ID}):
        client, _ = make_client(ok(broken))
        with pytest.raises(APIResponseValidationError):
            client.assets.get(ASSET_ID)
    client, _ = make_client(ok({"id": ASSET_ID, "object": "asset"}))
    with pytest.raises(APIResponseValidationError):
        client.assets.delete(ASSET_ID)


def test_asset_errors() -> None:
    client, _ = make_client(error(415, "UNSUPPORTED_MIME_TYPE"))
    with pytest.raises(InvalidRequestError) as exc_info:
        client.assets.create(b"PNG bytes", mime_type="audio/wav")
    assert exc_info.value.code == "UNSUPPORTED_MIME_TYPE" and exc_info.value.status_code == 415
    client, _ = make_client(error(404, "ASSET_NOT_FOUND"))
    with pytest.raises(NotFoundError):
        client.assets.get(ASSET_ID)


def test_mime_map_only_uses_types_the_api_accepts() -> None:
    accepted = set(OPENAPI["paths"]["/v1/assets"]["post"]["requestBody"]["content"])
    assert set(_MIME_BY_EXTENSION.values()) == accepted


@pytest.mark.anyio
async def test_async_assets(tmp_path: Path) -> None:
    client, seen = make_async_client(ok(ASSET_JSON, status=201))
    async with client:
        assert await client.assets.create(b"\x89PNG", mime_type="image/png") == EXPECTED_ASSET
        path = tmp_path / "page.webp"
        path.write_bytes(b"RIFFWEBP")
        await client.assets.create(path)
        assert await client.assets.get(ASSET_ID) == EXPECTED_ASSET
    assert [r.method for r in seen] == ["POST", "POST", "GET"]
    assert seen[0].headers["content-type"] == "image/png" and seen[0].content == b"\x89PNG"
    assert seen[1].headers["content-type"] == "image/webp" and seen[1].content == b"RIFFWEBP"

    client, seen = make_async_client(ok({"id": ASSET_ID, "object": "asset", "deleted": True}))
    async with client:
        deleted = await client.assets.delete(ASSET_ID)
    assert deleted.deleted is True and seen[0].method == "DELETE"


@pytest.mark.anyio
async def test_async_upload_is_never_retried() -> None:
    client, seen = make_async_client(error(502, "INFERENCE_FAILED"), max_retries=2)
    async with client:
        with pytest.raises(InferenceFailedError):
            await client.assets.create(b"x", mime_type="image/png")
    assert len(seen) == 1


@pytest.mark.anyio
async def test_async_decide_with_parts_and_multi() -> None:
    client, seen = make_async_client(ok(MULTI_OK))
    async with client:
        result = await client.decide(
            context=[TextPart("Is this invoice paid?"), {"type": "document", "asset_id": ASSET_ID}],
            questions={"tags": {"type": "multi", "options": {"invoice": None, "receipt": None, "overdue": None}},
                       "paid": NoulQuestion("Paid?")},
        )  # fmt: skip
    assert result.multi("tags").values == ["invoice", "overdue"]
    assert json.loads(seen[0].content)["context"][0] == {"type": "text", "text": "Is this invoice paid?"}


# ------------------------------------------------------------------------------------------------------ error codes


@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        (400, "UNSUPPORTED_MODALITY", InvalidRequestError),
        (415, "UNSUPPORTED_MIME_TYPE", InvalidRequestError),
        (404, "ASSET_NOT_FOUND", NotFoundError),
        (403, "ASSET_FORBIDDEN", PermissionDeniedError),
        (410, "ASSET_EXPIRED", NotFoundError),
        (413, "ASSET_TOO_LARGE", InvalidRequestError),
        (400, "TOO_MANY_IMAGES", InvalidRequestError),
        (400, "TOO_MANY_DOCUMENTS", InvalidRequestError),
        (400, "TOO_MANY_AUDIO", InvalidRequestError),
        (400, "DOCUMENT_TOO_MANY_PAGES", InvalidRequestError),
        (400, "AUDIO_TOO_LONG", InvalidRequestError),
        (422, "DECODE_FAILED", InvalidRequestError),
        (502, "OCR_FAILED", InferenceFailedError),
        (502, "ASR_FAILED", InferenceFailedError),
        (502, "VISION_FAILED", InferenceFailedError),
        (500, "MULTIMODAL_INFERENCE_FAILED", InternalServerError),
    ],
)
def test_multimodal_error_codes(status: int, code: str, cls: type[APIError]) -> None:
    client, _ = make_client(error(status, code), max_retries=0)
    with pytest.raises(cls) as exc_info:
        client.decide(context=[DocumentPart(ASSET_ID)], questions={"q": NoulQuestion("?")})
    exc = exc_info.value
    assert type(exc) is cls
    assert exc.code == exc.error_code == code
    assert exc.status_code == status
    assert str(exc) == f"details (code={code}, status={status}, request_id=req_e)"


@pytest.mark.parametrize(("status", "cls"), [(410, NotFoundError), (415, InvalidRequestError)])
def test_new_statuses_without_code(status: int, cls: type[APIError]) -> None:
    client, _ = make_client(lambda _r: httpx.Response(status, text="proxy"), max_retries=0)
    with pytest.raises(cls) as exc_info:
        client.assets.get(ASSET_ID)
    assert type(exc_info.value) is cls and exc_info.value.code is None


# --------------------------------------------------------------------------------- mock server (real HTTP, schema)


@pytest.fixture
def api() -> Iterator[MockKrunAPI]:
    with MockKrunAPI(api_key=KEY) as server:
        yield server


def test_end_to_end_upload_decide_delete(api: MockKrunAPI, tmp_path: Path) -> None:
    path = tmp_path / "invoice.pdf"
    path.write_bytes(b"%PDF-1.7 fake")
    with Krun(api_key=KEY, base_url=api.url, timeout=5) as client:
        asset = client.assets.create(path)
        assert asset.mime_type == "application/pdf" and asset.size_bytes == len(b"%PDF-1.7 fake")
        assert api.uploads[asset.id] == b"%PDF-1.7 fake"
        assert api.requests[-1].headers["content-type"] == "application/pdf"
        assert client.assets.get(asset.id) == asset
        result = client.decide(
            context=[TextPart("Is this invoice paid?"), DocumentPart(asset.id)],
            questions={"tags": MultiQuestion({"invoice": None, "receipt": "", "overdue": "Past due"}),
                       "paid": NoulQuestion("Is the document marked as paid?")},
        )  # fmt: skip
        assert result.multi("tags").values == ["invoice"]
        assert set(result.multi("tags").probabilities) == {"invoice", "receipt", "overdue"}
        assert client.assets.delete(asset.id).deleted is True
        with pytest.raises(NotFoundError) as exc_info:
            client.assets.get(asset.id)
        assert exc_info.value.code == "ASSET_NOT_FOUND"
        with pytest.raises(InvalidRequestError) as exc_info2:
            client.assets.create(b"x", mime_type="video/mp4")
        assert exc_info2.value.code == "UNSUPPORTED_MIME_TYPE"
        # The schema rejects fields the V1 contract does not allow (e.g. a `url` on a part).
        with pytest.raises(InvalidRequestError):
            client.decide(context=[{"type": "image", "asset_id": asset.id, "url": "https://x"}],
                          questions={"q": NoulQuestion("?")})  # fmt: skip


def test_end_to_end_upload_is_not_retried(api: MockKrunAPI) -> None:
    api.enqueue(Scripted(503, {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "down"}}))
    with (
        Krun(api_key=KEY, base_url=api.url, timeout=5, max_retries=2) as client,
        pytest.raises(ServiceUnavailableError),
    ):
        client.assets.create(b"\x89PNG", mime_type="image/png")
    assert len(api.requests) == 1
