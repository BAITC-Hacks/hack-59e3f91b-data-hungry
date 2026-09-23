"""OpenAI wire contract, provider routing and cache isolation without live API calls."""
from __future__ import annotations

import io
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import attachments, config, main, ocr, openai_media, recognition
from tests.fixtures.make_fixtures import make_all


@pytest.fixture(autouse=True)
def local_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "ATTACHMENT_DB_PATH", tmp_path / "attachments.sqlite3")
    monkeypatch.setenv("MEDIA_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")


def _mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(openai_media.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.asyncio
async def test_ocr_uses_responses_image_and_structured_text(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-only"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-5.6-luna" and payload["store"] is False
        assert payload["text"]["format"]["type"] == "json_schema"
        image = payload["input"][0]["content"][1]
        assert image["type"] == "input_image" and image["detail"] == "original"
        assert image["image_url"].startswith("data:image/jpeg;base64,")
        return httpx.Response(200, json={"output": [{"content": [
            {"type": "output_text", "text": '{"text":"Артикул | Шт\\n027228 | 2"}'}
        ]}]})

    _mock_client(monkeypatch, handler)
    assert await openai_media.ocr_image(b"\xff\xd8\xff", "https://api.openai.com/v1", "test-only") == "Артикул | Шт\n027228 | 2"


@pytest.mark.asyncio
async def test_transcription_uses_gpt_transcribe_and_normalizes_extension(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        body = request.read()
        assert b'gpt-transcribe' in body
        assert b'voice.webm' in body
        assert b'response_format' not in body
        return httpx.Response(200, json={"text": "Нужен кабель ВВГ"})

    _mock_client(monkeypatch, handler)
    assert await openai_media.transcribe_audio(b"fake", "voice.weba", "https://api.openai.com/v1", "test-only") == "Нужен кабель ВВГ"


@pytest.mark.asyncio
async def test_cache_is_separate_for_openai_and_nitec(monkeypatch):
    calls = []

    async def openai_ocr(*_):
        calls.append("openai")
        return "027228 | 2"

    async def nitec_ocr(*_):
        calls.append("nitec")
        return [{"text": "027228", "box": [1, 2, 3, 4]}]

    monkeypatch.setattr(openai_media, "ocr_image", openai_ocr)
    monkeypatch.setattr(ocr, "ocr_image", nitec_ocr)
    monkeypatch.setenv("NITEC_API_KEY", "test-only")
    image = Image.new("RGB", (25, 25), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()

    first = await attachments.save_and_parse("photo.png", raw, "image/png")
    repeat = await attachments.save_and_parse("photo.png", raw, "image/png")
    monkeypatch.setenv("MEDIA_AI_PROVIDER", "nitec")
    other = await attachments.save_and_parse("photo.png", raw, "image/png")
    assert first.text == repeat.text == "027228 | 2"
    assert first.boxes == [] and other.boxes[0]["text"] == "027228"
    assert calls == ["openai", "nitec"]
    with sqlite3.connect(config.ATTACHMENT_DB_PATH) as db:
        assert db.execute("SELECT count(*) FROM parsed_files_v2").fetchone()[0] == 2


def test_cache_signature_tracks_model(monkeypatch):
    assert recognition.cache_signature("word") == "local-v2"
    old = recognition.cache_signature("audio")
    monkeypatch.setenv("OPENAI_ASR_MODEL", "another-transcriber")
    assert recognition.cache_signature("audio") != old


def test_local_file_lab_renders_and_parses_docx(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(config, "ENABLE_FILE_LAB", False)
    assert client.get("/lab").status_code == 404
    monkeypatch.setattr(config, "ENABLE_FILE_LAB", True)
    assert "Лаборатория распознавания файлов" in client.get("/lab").text
    assert client.post("/api/lab/parse", headers={"Origin": "https://unrelated.example"},
                       files={"file": ("x.txt", b"test", "text/plain")}).status_code == 404
    document = make_all()["docx"].read_bytes()
    response = client.post("/api/lab/parse", files={"file": (
        "spec.docx", document, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )})
    assert response.status_code == 200
    body = response.json()
    assert body["processor"] == "local-v2"
    assert body["kind"] == "word" and "027228" in body["text"]
    assert body["lines"][0]["article"] == "027228"
