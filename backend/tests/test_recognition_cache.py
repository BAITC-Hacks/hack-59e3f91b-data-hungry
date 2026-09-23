"""SHA-256 reuse and OpenAI OCR/ASR integration without live provider calls."""
from __future__ import annotations

import io
import sqlite3

import fitz
import pytest
from PIL import Image

from app import attachment_cache, attachments, config, openai_media


@pytest.fixture(autouse=True)
def local_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "ATTACHMENT_DB_PATH", tmp_path / "attachments.sqlite3")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")


@pytest.mark.asyncio
async def test_same_image_recognized_once_and_persisted(monkeypatch):
    calls = []

    async def fake_ocr(data, api_url, api_key, model):
        calls.append(model)
        return "Автомат 16А"

    monkeypatch.setattr(openai_media, "ocr_image", fake_ocr)
    image = Image.new("RGB", (25, 25), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    raw = output.getvalue()

    one = await attachments.save_and_parse("photo.png", raw, "image/png", "session-a")
    two = await attachments.save_and_parse("copy.png", raw, "image/png", "session-b")
    assert one.id != two.id and one.sha256 == two.sha256
    assert one.text == two.text == "Автомат 16А"
    assert one.boxes == two.boxes
    assert calls == ["gpt-5.6-luna"]
    assert "Автомат 16А" in attachments.text_for_llm(two)
    assert attachments.get_attachment(one.id, "session-b") is None
    assert attachments.get_attachment(one.id, "session-a") is one
    cached = attachment_cache.get(one.sha256, "image", "media-v1:openai:gpt-5.6-luna")
    assert cached is not None and cached["raw"] == raw and cached["text"] == "Автомат 16А"
    with sqlite3.connect(config.ATTACHMENT_DB_PATH) as db:
        assert db.execute("SELECT count(*) FROM parsed_files_v2").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_audio_transcript_is_agent_text(monkeypatch):
    calls = []

    async def fake_asr(data, filename, api_url, api_key, model):
        calls.append(model)
        return "Нужен кабель ВВГ три на два с половиной"

    monkeypatch.setattr(openai_media, "transcribe_audio", fake_asr)
    one = await attachments.save_and_parse("request.wav", b"sample audio", "audio/wav", "session-a")
    two = await attachments.save_and_parse("repeat.wav", b"sample audio", "audio/wav", "session-a")
    assert one.text == two.text
    assert one.summary.startswith("транскрипция")
    assert "Нужен кабель" in attachments.text_for_llm(one)
    assert calls == ["gpt-transcribe"]


@pytest.mark.asyncio
async def test_scanned_pdf_uses_ocr(monkeypatch):
    async def fake_ocr(data, api_url, api_key, model):
        return "Артикул 027228, 2 шт"

    monkeypatch.setattr(openai_media, "ocr_image", fake_ocr)
    pdf = fitz.open()
    pdf.new_page()
    raw = pdf.tobytes()
    pdf.close()
    att = await attachments.save_and_parse("scan.pdf", raw, "application/pdf", "session-a")
    assert "027228" in att.text
    assert att.boxes == []
    assert attachment_cache.get(att.sha256, "pdf", "media-v1:openai:gpt-5.6-luna")["text"] == att.text
