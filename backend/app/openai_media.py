"""OpenAI file transcription and image/PDF-page text extraction."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

OCR_MODEL = "gpt-5.6-luna"
ASR_MODEL = "gpt-transcribe"
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mpeg", ".mpga", ".oga", ".weba"}
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/m4a",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".flac": "audio/flac",
    ".webm": "audio/webm", ".weba": "audio/webm", ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg",
}


async def ocr_image(data: bytes, api_url: str, api_key: str, model: str = OCR_MODEL) -> str:
    """Extract text from a JPEG page. OpenAI vision does not provide verified layout boxes."""
    payload = {
        "model": model,
        "store": False,
        "max_output_tokens": 8192,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": (
                "Распознай весь читаемый текст на изображении. Сохрани исходный язык, "
                "артикулы, числа, единицы измерения, строки и порядок чтения. "
                "Таблицы передай построчно, разделяя ячейки символом |. "
                "Не добавляй пояснений и не угадывай нечитаемые символы."
            )},
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
             "detail": "original"},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "ocr_text", "strict": True,
                            "schema": {"type": "object", "properties": {"text": {"type": "string"}},
                                       "required": ["text"], "additionalProperties": False}}},
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(f"{api_url.rstrip('/')}/responses",
                                     headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        response.raise_for_status()
        result = response.json()
    parts = [part.get("text", "") for item in result.get("output", [])
             for part in item.get("content", []) if part.get("type") == "output_text"]
    raw = "".join(parts) or result.get("output_text", "")
    if not raw:
        raise ValueError("OpenAI OCR returned no output text")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str):
        raise ValueError("OpenAI OCR returned invalid structured output")
    return parsed["text"].strip()


async def transcribe_audio(data: bytes, filename: str, api_url: str, api_key: str,
                           model: str = ASR_MODEL) -> str:
    """Transcribe a file with the current OpenAI transcription API."""
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if ext in (".oga", ".weba"):
        name = str(Path(name).with_suffix(".ogg" if ext == ".oga" else ".webm"))
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{api_url.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (name, data, content_type)},
            data={"model": model},
        )
        response.raise_for_status()
        result = response.json()
    text = result.get("text") if isinstance(result, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("OpenAI transcription returned no text")
    return text.strip()
