"""Whisper transcription via llm.nitec.kz/v1/audio/transcriptions."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("ekt_attachments.transcription")

_MODEL = "openai/whisper-large-v3-turbo"

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mpeg", ".mpga", ".oga", ".weba"}

_CONTENT_TYPES = {
    ".mp3":  "audio/mpeg",
    ".wav":  "audio/wav",
    ".m4a":  "audio/m4a",
    ".ogg":  "audio/ogg",
    ".oga":  "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".weba": "audio/webm",
    ".mp4":  "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
}


async def transcribe_audio(data: bytes, filename: str, api_url: str, api_key: str, model: str = _MODEL) -> str:
    """Call Whisper API and return transcribed text."""
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    content_type = _CONTENT_TYPES.get(ext, "audio/mpeg")

    # Build the base URL (remove /chat/completions suffix if present)
    base = api_url
    for suffix in ("/chat/completions", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.rstrip("/")

    endpoint = f"{base}/audio/transcriptions"

    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, data, content_type)},
            data={"model": model, "response_format": "verbose_json"},
        )
        r.raise_for_status()
        result = r.json()
        text = result.get("text", "").strip() if isinstance(result, dict) else r.text.strip()

    logger.info("Transcribed %s: %d chars", filename, len(text))
    return text
