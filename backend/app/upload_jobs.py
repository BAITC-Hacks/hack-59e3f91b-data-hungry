"""Session-bound, in-memory upload jobs for visible recognition progress."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from . import attachments

log = logging.getLogger("ekt.upload_jobs")
TTL_SECONDS = 3600


@dataclass
class UploadJob:
    id: str
    session_id: str
    filename: str
    status: str = "processing"
    stage: str = "queued"
    attachment_id: str | None = None
    kind: str | None = None
    summary: str = ""
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {"job_id": self.id, "session_id": self.session_id, "filename": self.filename,
                "status": self.status, "stage": self.stage, "attachment_id": self.attachment_id,
                "kind": self.kind, "summary": self.summary, "error": self.error}


_jobs: dict[str, UploadJob] = {}


def start(filename: str, content: bytes, mime: str, session_id: str) -> UploadJob:
    cutoff = time.time() - TTL_SECONDS
    for old_id in [key for key, job in _jobs.items() if job.created_at < cutoff and (not job.task or job.task.done())]:
        del _jobs[old_id]
    job = UploadJob(id="job_" + uuid.uuid4().hex, session_id=session_id, filename=filename)
    _jobs[job.id] = job
    job.task = asyncio.create_task(_run(job, content, mime))
    return job


def get(job_id: str, session_id: str) -> UploadJob | None:
    job = _jobs.get(job_id)
    return job if job and job.session_id == session_id and time.time() - job.created_at < TTL_SECONDS else None


async def _run(job: UploadJob, content: bytes, mime: str) -> None:
    try:
        att = await attachments.save_and_parse(job.filename, content, mime, job.session_id,
                                               on_progress=lambda stage: setattr(job, "stage", stage))
        job.kind = att.kind
        job.summary = att.summary
        if not att.text.strip():
            job.status = "failed"
            job.stage = "failed"
            job.error = att.summary or "Текст в файле не найден; попробуйте другой файл."
            return
        job.attachment_id = att.id
        job.status = "ready"
        job.stage = "ready"
    except Exception as exc:
        log.warning("upload job failed: %s", type(exc).__name__)
        job.status = "failed"
        job.stage = "failed"
        job.error = "Не удалось распознать файл. Попробуйте другой формат или файл."
