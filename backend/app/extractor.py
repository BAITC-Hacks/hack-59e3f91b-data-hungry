"""Extract Office and plain text; adapted from sdu-ai-services."""
from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

logger = logging.getLogger("ekt_attachments.extractor")

# Formats handled by liteparse (PDF native; Office via LibreOffice)
LITEPARSE_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp"}

# Formats we read directly as UTF-8 text
PLAINTEXT_EXTS = {".txt", ".md", ".csv", ".log"}

# All supported extensions
SUPPORTED_EXTS = LITEPARSE_EXTS | PLAINTEXT_EXTS


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTS


def extract(data: bytes, filename: str) -> str:
    """Extract text from file bytes. Returns empty string on failure."""
    ext = Path(filename).suffix.lower()

    if ext in PLAINTEXT_EXTS:
        return _read_plain(data)

    if ext == ".docx":
        return _liteparse(data, filename) or _read_docx(data, filename)

    if ext == ".xlsx":
        return _read_xlsx(data, filename) or _liteparse(data, filename)

    if ext == ".pptx":
        return _liteparse(data, filename) or _read_pptx(data, filename)

    if ext in LITEPARSE_EXTS:
        return _liteparse(data, filename)

    return ""


def _read_plain(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "windows-1251", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _liteparse(data: bytes, filename: str) -> str:
    import tempfile, os

    try:
        from liteparse import LiteParse
    except ImportError:
        logger.warning("liteparse not installed; cannot parse %s", filename)
        return ""

    ext = Path(filename).suffix.lower()
    parser = LiteParse(ocr_enabled=False, quiet=True)

    # liteparse needs a real file with the correct extension to detect format
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        result = parser.parse(tmp_path)
        return result.text or ""
    except Exception as exc:
        logger.warning("liteparse extraction failed for '%s': %s", filename, type(exc).__name__)
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _read_docx(data: bytes, filename: str) -> str:
    """Fallback DOCX text extraction that does not depend on LibreOffice."""
    try:
        from docx import Document

        doc = Document(BytesIO(data))
        parts: list[str] = []

        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                parts.append(text)

        for table in doc.tables:
            for row in table.rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                line = " | ".join(cell for cell in cells if cell)
                if line:
                    parts.append(line)

        footnotes = _read_docx_footnotes(data)
        if footnotes:
            parts.append("Сноски:")
            parts.extend(footnotes)

        return "\n".join(parts)
    except Exception as exc:
        logger.warning("python-docx fallback failed for '%s': %s", filename, type(exc).__name__)
        return ""


def _read_xlsx(data: bytes, filename: str) -> str:
    """Fallback without LibreOffice; preserves sheet names and table rows."""
    try:
        from openpyxl import load_workbook

        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
        parts: list[str] = []
        try:
            for sheet in book:
                parts.append(f"[лист {sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    cells = [" ".join(str(value).split()) if value is not None else "" for value in row]
                    line = " | ".join(cells).strip(" |")
                    if line:
                        parts.append(line)
        finally:
            book.close()
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("openpyxl fallback failed for '%s': %s", filename, type(exc).__name__)
        return ""


def _read_pptx(data: bytes, filename: str) -> str:
    """Fallback PPTX text extraction when LibreOffice is unavailable."""
    try:
        from pptx import Presentation

        presentation = Presentation(BytesIO(data))
        parts: list[str] = []
        for number, slide in enumerate(presentation.slides, start=1):
            parts.append(f"Слайд {number}:")
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text.strip():
                    parts.append(shape.text.strip())
                if shape.has_table:
                    for row in shape.table.rows:
                        line = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                        if line:
                            parts.append(line)
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("python-pptx fallback failed for '%s': %s", filename, type(exc).__name__)
        return ""


def _read_docx_footnotes(data: bytes) -> list[str]:
    try:
        with ZipFile(BytesIO(data)) as archive:
            if "word/footnotes.xml" not in archive.namelist():
                return []
            root = ET.fromstring(archive.read("word/footnotes.xml"))
    except Exception:
        return []

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    notes: list[str] = []
    for footnote in root.findall("w:footnote", ns):
        note_id = footnote.attrib.get(f"{{{ns['w']}}}id", "")
        if note_id.startswith("-"):
            continue
        text = " ".join(
            (node.text or "").strip()
            for node in footnote.findall(".//w:t", ns)
            if (node.text or "").strip()
        )
        if text:
            notes.append(f"{note_id}. {text}")
    return notes
