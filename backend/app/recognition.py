"""Extract text and OCR boxes; adapted from sdu-ai-services."""
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

from . import extractor, ocr, transcription

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUPPORTED_EXTS = extractor.SUPPORTED_EXTS | transcription.AUDIO_EXTS | IMAGE_EXTS
MAX_OCR_PAGES = 15


def _nitec() -> tuple[str, str]:
    base = os.getenv("NITEC_API_BASE_URL", "https://llm.nitec.kz/v1").rstrip("/")
    key = os.getenv("NITEC_API_KEY", "")
    if not key:
        raise RuntimeError("NITEC_API_KEY is required for OCR or transcription")
    return base, key


def _image_for_ocr(data: bytes) -> bytes:
    """Normalize BMP/TIFF and EXIF rotation before sending to Chandra."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90)
        return output.getvalue()


def _pdf_native_and_scans(data: bytes) -> tuple[dict[int, str], list[dict], list[tuple[int, bytes]]]:
    """Use embedded PDF text first, render only scanned pages for OCR."""
    import fitz

    texts: dict[int, str] = {}
    boxes: list[dict] = []
    scans: list[tuple[int, bytes]] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for index, page in enumerate(doc):
            native = page.get_text("text").strip()
            if len(native.split()) >= 8 and len("".join(native.split())) >= 30:
                texts[index + 1] = f"Страница {index + 1}:\n{native}"
                for word in page.get_text("words"):
                    x0, y0, x1, y1, word_text = word[:5]
                    boxes.append({
                        "page": index + 1,
                        "text": word_text,
                        "box": [round(100 * x0 / page.rect.width, 2), round(100 * y0 / page.rect.height, 2),
                                round(100 * x1 / page.rect.width, 2), round(100 * y1 / page.rect.height, 2)],
                    })
            elif len(scans) < MAX_OCR_PAGES:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                scans.append((index + 1, pix.tobytes("jpeg")))
            else:
                texts[index + 1] = f"Страница {index + 1}: [OCR пропущен: лимит {MAX_OCR_PAGES} страниц]"
    return texts, boxes, scans


async def extract_file(data: bytes, filename: str) -> tuple[str, list[dict]]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        texts, boxes, scans = await asyncio.to_thread(_pdf_native_and_scans, data)
        if scans:
            base, key = _nitec()
            for page, image in scans:
                page_boxes = await ocr.ocr_image(image, base, key, os.getenv("NITEC_OCR_MODEL", ocr._MODEL))
                if not page_boxes:
                    raise ValueError(f"OCR returned no text for PDF page {page}")
                texts[page] = f"Страница {page}:\n" + "\n".join(b["text"] for b in page_boxes)
                boxes.extend({**box, "page": page} for box in page_boxes)
        return "\n\n".join(texts[page] for page in sorted(texts)).strip(), boxes

    if ext in IMAGE_EXTS:
        base, key = _nitec()
        image = await asyncio.to_thread(_image_for_ocr, data)
        boxes = await ocr.ocr_image(image, base, key, os.getenv("NITEC_OCR_MODEL", ocr._MODEL))
        return "\n".join(box["text"] for box in boxes), boxes

    if ext in transcription.AUDIO_EXTS:
        base, key = _nitec()
        text = await transcription.transcribe_audio(
            data, filename, base, key, os.getenv("NITEC_ASR_MODEL", transcription._MODEL)
        )
        return text, []

    if ext in extractor.SUPPORTED_EXTS:
        text = await asyncio.to_thread(extractor.extract, data, filename)
        return text, []

    raise ValueError("Unsupported file type")
