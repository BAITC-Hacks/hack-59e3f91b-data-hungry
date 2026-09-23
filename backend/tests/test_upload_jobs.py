"""File recognition jobs expose stage and require the same session for polling."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main, upload_jobs


def test_upload_job_stages_and_session_isolation(monkeypatch):
    async def fake_parse(filename, content, mime, session_id, on_progress):
        on_progress("checking_cache")
        await asyncio.sleep(0.02)
        on_progress("parsing")
        await asyncio.sleep(0.02)
        on_progress("recognizing")
        await asyncio.sleep(0.02)
        return SimpleNamespace(id="att_ready", kind="word", summary="2 позиции", text="Артикул 027228")

    monkeypatch.setattr(upload_jobs.attachments, "save_and_parse", fake_parse)
    with TestClient(main.app) as client:
        start = client.post("/api/upload/jobs", data={"session_id": "upload-owner"},
                            files={"file": ("request.docx", b"PK-data", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        assert start.status_code == 202
        body = start.json()
        assert body["status"] == "processing" and body["attachment_id"] is None
        path = f"/api/upload/jobs/{body['job_id']}"
        assert client.get(path, params={"session_id": "another-session"}).status_code == 404
        stages = {body["stage"]}
        for _ in range(30):
            current = client.get(path, params={"session_id": "upload-owner"}).json()
            stages.add(current["stage"])
            if current["status"] == "ready":
                break
            time.sleep(0.01)
        assert current["status"] == "ready" and current["attachment_id"] == "att_ready"
        assert "recognizing" in stages or "parsing" in stages


def test_upload_job_with_no_text_fails(monkeypatch):
    async def empty_parse(filename, content, mime, session_id, on_progress):
        on_progress("recognizing")
        return SimpleNamespace(id="att_empty", kind="image", summary="OCR не нашёл текст", text="")

    monkeypatch.setattr(upload_jobs.attachments, "save_and_parse", empty_parse)
    with TestClient(main.app) as client:
        body = client.post("/api/upload/jobs", data={"session_id": "empty-owner"},
                           files={"file": ("blank.png", b"bytes", "image/png")}).json()
        path = f"/api/upload/jobs/{body['job_id']}"
        for _ in range(20):
            result = client.get(path, params={"session_id": "empty-owner"}).json()
            if result["status"] == "failed":
                break
            time.sleep(0.01)
        assert result["status"] == "failed"
        assert result["attachment_id"] is None
        assert "текст" in result["error"].lower()
