"""SHA-256 reuse and NITEC OCR/ASR integration without live provider calls."""
from __future__ import annotations

import io
import sqlite3

import fitz
import pytest
from PIL import Image

from app import attachment_cache, attachments, config, ocr, transcription


def test_chandra_table_layout_is_preserved():
    html = '<div data-bbox="100 100 900 500" data-label="Table"><table><tr><th>Артикул</th><th>Шт</th></tr><tr><td>027228</td><td>2</td></tr></table></div>'
    boxes = ocr._parse_html(html)
    assert [box["text"] for box in boxes] == ["Артикул", "Шт", "027228", "2"]
    assert boxes[2]["kind"] == "table_cell" and boxes[2]["row"] == 1


@pytest.fixture(autouse=True)
def local_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "ATTACHMENT_DB_PATH", tmp_path / "attachments.sqlite3")
    monkeypatch.setenv("NITEC_API_KEY", "test-only")
    monkeypatch.setenv("MEDIA_AI_PROVIDER", "nitec")


@pytest.mark.asyncio
async def test_same_image_recognized_once_and_persisted(monkeypatch):
    calls = []

    async def fake_ocr(data, api_url, api_key, model):
        calls.append(model)
        return [{"text": "Автомат 16А", "box": [1, 2, 3, 4]}]

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    image = Image.new("RGB", (25, 25), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    raw = output.getvalue()

    one = await attachments.save_and_parse("photo.png", raw, "image/png", "session-a")
    two = await attachments.save_and_parse("copy.png", raw, "image/png", "session-b")
    assert one.id != two.id and one.sha256 == two.sha256
    assert one.text == two.text == "Автомат 16А"
    assert one.boxes == two.boxes
    assert calls == ["datalab-to/chandra-ocr-2"]
    assert "Автомат 16А" in attachments.text_for_llm(two)
    assert attachments.get_attachment(one.id, "session-b") is None
    assert attachments.get_attachment(one.id, "session-a") is one
    cached = attachment_cache.get(one.sha256, "image", "media-v1:nitec:datalab-to/chandra-ocr-2")
    assert cached is not None and cached["raw"] == raw and cached["text"] == "Автомат 16А"
    with sqlite3.connect(config.ATTACHMENT_DB_PATH) as db:
        assert db.execute("SELECT count(*) FROM parsed_files_v2").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_audio_transcript_is_agent_text(monkeypatch):
    calls = []

    async def fake_asr(data, filename, api_url, api_key, model):
        calls.append(model)
        return "Нужен кабель ВВГ три на два с половиной"

    monkeypatch.setattr(transcription, "transcribe_audio", fake_asr)
    one = await attachments.save_and_parse("request.wav", b"sample audio", "audio/wav", "session-a")
    two = await attachments.save_and_parse("repeat.wav", b"sample audio", "audio/wav", "session-a")
    assert one.text == two.text
    assert one.summary.startswith("транскрипция")
    assert "Нужен кабель" in attachments.text_for_llm(one)
    assert calls == ["openai/whisper-large-v3-turbo"]


@pytest.mark.asyncio
async def test_scanned_pdf_uses_ocr(monkeypatch):
    async def fake_ocr(data, api_url, api_key, model):
        return [{"text": "Артикул 027228, 2 шт", "box": [10, 10, 80, 20]}]

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    pdf = fitz.open()
    pdf.new_page()
    raw = pdf.tobytes()
    pdf.close()
    att = await attachments.save_and_parse("scan.pdf", raw, "application/pdf", "session-a")
    assert "027228" in att.text
    assert att.boxes[0]["page"] == 1
    assert attachment_cache.get(att.sha256, "pdf", "media-v1:nitec:datalab-to/chandra-ocr-2")["text"] == att.text
