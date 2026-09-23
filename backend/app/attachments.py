"""Uploaded attachments: store, recognize and extract specification lines.

A specification usually arrives as an Excel/Word/PDF table ("артикул | наименование | кол-во"), or as a
photo of a part. ``save_and_parse`` stores the file under ``config.UPLOAD_DIR``, extracts text, and turns
each row into a ``line`` (``{'raw', 'article', 'name', 'qty'}``) so the chat layer can look every
position up in the catalog. Images are validated with Pillow, re-encoded as JPEG (max side 1568 px) and
exposed as an Anthropic image content block for vision.

The per-session registry is in-memory; extracted results are cached in SQLite by SHA-256 and processor.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import attachment_cache, config, recognition

MAX_ROWS_PER_SHEET = 300
MAX_PDF_PAGES = 10
IMAGE_MAX_SIDE = 1568

_KIND_BY_EXT: dict[str, str] = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image", ".bmp": "image",
    ".tif": "image", ".tiff": "image",
    ".xlsx": "excel", ".xlsm": "excel", ".xls": "excel",
    ".docx": "word", ".doc": "document", ".pptx": "document", ".ppt": "document",
    ".odt": "document", ".ods": "document", ".odp": "document",
    ".txt": "document", ".md": "document", ".csv": "document", ".log": "document",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio", ".oga": "audio",
    ".flac": "audio", ".webm": "audio", ".weba": "audio", ".mp4": "audio", ".mpeg": "audio", ".mpga": "audio",
    ".pdf": "pdf",
}
_KIND_BY_MIME: dict[str, str] = {
    "image/jpeg": "image", "image/png": "image", "image/webp": "image", "image/gif": "image",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "application/vnd.ms-excel": "excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/pdf": "pdf",
    "audio/mpeg": "audio", "audio/wav": "audio", "audio/webm": "audio", "audio/ogg": "audio",
}

_HDR_ARTICLE = ("артикул", "арт.", "арт ", "код", "sku", "article", "part", "каталожн", "номер")
_HDR_NAME = ("наименован", "название", "товар", "описание", "позиция", "name", "product", "материал")
_HDR_QTY = ("кол-во", "кол.", "колич", "кол ", "количество", "qty", "quantity", "шт", "объем", "объём", "ед.")
_HDR_PRICE = ("цена", "стоимост", "сумма", "price", "total", "итого")

# article-like token: >= 4 chars, at least one digit, letters/digits/_-./ (Cyrillic allowed: "ярп4520")
_ARTICLE_TOKEN = re.compile(r"(?<![\w\-./])[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_\-./]{3,}(?![\w\-./])")
# spec-ish tokens that look like articles but are not: "160А", "18kA", "30W", "IP20", "3x2,5", "98x180", "0,66"
_UNIT_TOKEN = re.compile(
    r"^(?:\d+(?:[.,]\d+)?(?:а|a|в|v|w|вт|k|к|ка|ka|lm|лм|мм|mm|м|m|кв|kv|гц|hz|мa|ma|ah|ач|л|l|кг|kg|°c|c|p|р|ф|f)"
    r"|ip\d+|\d+(?:[.,]\d+)?[xх×]\d+(?:[.,]\d+)?(?:[xх×]\d+(?:[.,]\d+)?)?|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?"
    r"|\d{4}-\d{2}-\d{2})$",
    re.I,
)
_NUMBER = re.compile(r"^-?\d{1,9}(?:[.,]\d{1,3})?$")
_QTY_IN_TEXT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:шт|pcs|pc|ед|упак|уп|м\b|метр|компл|комп\b)", re.I)


@dataclass
class Attachment:
    """A stored upload plus everything extracted from it."""

    id: str
    filename: str
    kind: str  # image | excel | word | pdf | document | audio
    path: Path
    mime: str
    text: str = ""
    lines: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    width: int = 0
    height: int = 0
    sha256: str = ""
    boxes: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Shape returned by ``POST /api/upload``."""
        return {"attachment_id": self.id, "filename": self.filename, "kind": self.kind, "summary": self.summary}


_registry: dict[str, Attachment] = {}
_hash_locks: dict[str, asyncio.Lock] = {}


def detect_kind(filename: str, mime: str | None = None) -> str | None:
    """Attachment kind from extension (preferred) or MIME type; None if unsupported."""
    ext = Path(filename or "").suffix.lower()
    return _KIND_BY_EXT.get(ext) or _KIND_BY_MIME.get((mime or "").split(";")[0].strip().lower())


async def save_and_parse(filename: str, content: bytes, mime: str, session_id: str = "",
                         on_progress: Callable[[str], None] | None = None) -> Attachment:
    """Store an upload and parse it (runs the parser in a worker thread).

    Args:
        filename: original file name (its extension decides the parser).
        content: raw bytes (the caller enforces ``config.MAX_UPLOAD_MB``).
        mime: content type sent by the browser (fallback for kind detection).

    Returns:
        The registered ``Attachment``. Parse problems never raise: they are reported in ``summary`` and the
        attachment still exists (with empty text) so the assistant can tell the user what went wrong.

    Raises:
        ValueError: unsupported file type or empty content.
    """
    kind = detect_kind(filename, mime)
    if not kind:
        raise ValueError("Неподдерживаемый тип файла.")
    if not content:
        raise ValueError("Пустой файл.")
    att_id = "att_" + uuid.uuid4().hex[:12]
    safe_name = re.sub(r"[^\w.\-]+", "_", Path(filename.replace("\\", "/")).name, flags=re.UNICODE)[:120] or "file"
    digest = hashlib.sha256(content).hexdigest()
    processor_signature = recognition.cache_signature(kind)
    upload_dir = Path(config.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{digest}{Path(safe_name).suffix.lower()}"
    att = Attachment(id=att_id, filename=Path(safe_name).name, kind=kind, path=path, mime=mime or "",
                     sha256=digest, session_id=session_id)
    lock = _hash_locks.setdefault(f"{digest}:{kind}:{processor_signature}", asyncio.Lock())
    if on_progress:
        on_progress("checking_cache")
    async with lock:
        cached = await asyncio.to_thread(attachment_cache.get, digest, kind, processor_signature)
        if cached:
            att.path = upload_dir / f"{digest}{cached['stored_suffix']}"
            if not att.path.exists():
                await asyncio.to_thread(att.path.write_bytes, cached["stored_bytes"])
            att.text = cached["text"]
            att.lines = cached["lines"]
            att.boxes = cached["boxes"]
            att.summary = cached["summary"]
            att.mime = cached["mime"]
            att.width, att.height = cached["width"], cached["height"]
        else:
            if on_progress:
                on_progress("parsing")
            await asyncio.to_thread(_store_and_parse, att, content)
            if on_progress:
                on_progress("recognizing")
            await _recognize(att, content)
            if att.text.strip():
                stored_bytes = await asyncio.to_thread(att.path.read_bytes)
                await asyncio.to_thread(
                    attachment_cache.put, digest, kind, processor_signature, content, stored_bytes, att.path.suffix,
                    att.text, att.lines, att.boxes, att.summary, att.mime, att.width, att.height,
                )
    _registry[att_id] = att
    return att


def get_attachment(att_id: str, session_id: str | None = None) -> Attachment | None:
    """Registered attachment by id (None if unknown)."""
    att = _registry.get(att_id)
    if att is not None and session_id is not None and att.session_id != session_id:
        return None
    return att


def _store_and_parse(att: Attachment, content: bytes) -> None:
    try:
        if att.kind == "image":
            _parse_image(att, content)  # stores the re-encoded JPEG itself
        else:
            att.path.write_bytes(content)
            parser = {"excel": _parse_excel, "word": _parse_word, "pdf": _parse_pdf}.get(att.kind)
            if parser and att.path.suffix.lower() != ".xls":
                parser(att)
    except Exception as exc:  # noqa: BLE001 - a broken upload must not break the chat
        if not att.path.exists():
            att.path.write_bytes(content)
        att.summary = f"не удалось прочитать файл ({type(exc).__name__})"
        att.text, att.lines = "", []
        return
    if att.kind not in ("image", "audio"):
        att.summary = _summary(att.lines)


async def _recognize(att: Attachment, content: bytes) -> None:
    """Augment the specification parser with OCR, ASR and broader Office extraction."""
    ext = Path(att.filename).suffix.lower()
    if ext not in recognition.SUPPORTED_EXTS:
        return
    if att.summary.startswith("не удалось прочитать") and ext not in (".xls", ".doc", ".ppt"):
        return
    try:
        text, boxes = await recognition.extract_file(content, att.filename)
    except Exception as exc:  # never expose provider response bodies or uploaded text
        if att.kind == "image":
            att.summary += f"; OCR недоступен ({type(exc).__name__})"
        elif att.kind == "audio" or not att.text.strip():
            att.summary = f"распознавание недоступно ({type(exc).__name__})"
        return
    if text.strip():
        att.text = text
        att.boxes = boxes
        if not att.lines and att.kind in ("pdf", "document", "excel"):
            rows = [_split_text_row(line) for line in text.splitlines() if line.strip()]
            att.lines = extract_lines(rows)
        if att.kind == "image":
            att.summary += f"; OCR: {len(text)} символов"
        elif att.kind == "audio":
            att.summary = f"транскрипция: {len(text)} символов"
        elif att.lines:
            att.summary = _summary(att.lines)
        else:
            att.summary = f"текст: {len(text)} символов"


# --- parsers ----------------------------------------------------------------------------------


def _parse_image(att: Attachment, content: bytes) -> None:
    from PIL import Image, ImageOps

    Image.open(io.BytesIO(content)).verify()  # raises on corrupt data
    img = Image.open(io.BytesIO(content))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = IMAGE_MAX_SIDE / max(w, h)
    if scale < 1:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    att.path = att.path.with_suffix(".jpg")
    img.save(att.path, "JPEG", quality=85, optimize=True)
    att.mime = "image/jpeg"
    att.width, att.height = img.size
    att.summary = f"изображение {att.width}x{att.height}"


def _parse_excel(att: Attachment) -> None:
    import openpyxl

    if att.path.suffix.lower() == ".xls":
        raise ValueError("формат .xls (Excel 97-2003) не поддерживается, сохраните как .xlsx")
    wb = openpyxl.load_workbook(att.path, read_only=True, data_only=True)
    lines: list[dict[str, Any]] = []
    text_parts: list[str] = []
    try:
        for ws in wb.worksheets:
            sheet_rows: list[list[Any]] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS_PER_SHEET:
                    break
                cells = list(row)
                if any(c is not None and str(c).strip() for c in cells):
                    sheet_rows.append(cells)
            if sheet_rows:
                text_parts.append(f"[лист {ws.title}]")
                text_parts.extend(" | ".join(_cell_str(c) for c in r if _cell_str(c)) for r in sheet_rows)
                lines.extend(extract_lines(sheet_rows))  # header detection is per sheet
    finally:
        wb.close()
    att.text = "\n".join(text_parts)
    att.lines = lines


def _parse_word(att: Attachment) -> None:
    import docx

    doc = docx.Document(str(att.path))
    parts: list[str] = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    rows: list[list[Any]] = [[p] for p in parts]
    for table in doc.tables:
        for row in table.rows[:MAX_ROWS_PER_SHEET]:
            cells = [c.text.strip() for c in row.cells]
            # merged cells repeat the same text; collapse consecutive duplicates
            dedup = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
            if any(dedup):
                parts.append(" | ".join(c for c in dedup if c))
                rows.append(dedup)
    att.text = "\n".join(parts)
    att.lines = extract_lines(rows)


def _parse_pdf(att: Attachment) -> None:
    from pypdf import PdfReader

    reader = PdfReader(str(att.path))
    pages: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        pages.append(page.extract_text() or "")
    att.text = "\n".join(pages).strip()
    rows = [_split_text_row(ln) for ln in att.text.splitlines() if ln.strip()]
    att.lines = extract_lines(rows)


# --- line extraction --------------------------------------------------------------------------


def _cell_str(c: Any) -> str:
    if c is None:
        return ""
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c).strip()


def _split_text_row(line: str) -> list[str]:
    """A plain text line -> pseudo cells (split on ' | ', tabs, ';' or 2+ spaces)."""
    parts = re.split(r"\s*\|\s*|\t+|;\s*|\s{2,}", line.strip())
    return [p for p in parts if p]


def _to_number(s: str) -> float | None:
    s = s.strip().replace(" ", "").replace(" ", "")
    if _NUMBER.match(s):
        try:
            return float(s.replace(",", "."))
        except ValueError:
            return None
    return None


def _is_article_token(tok: str) -> bool:
    if len(tok) < 4 or not re.search(r"\d", tok) or _UNIT_TOKEN.match(tok):
        return False
    if re.fullmatch(r"[\d.,]+", tok):  # pure number: an article only when long (>= 5 digits) - "027228", "310100080"
        return tok.isdigit() and len(tok) >= 5
    return True


def _best_article(text: str) -> str | None:
    """Most article-looking token: many digits, separators and pure-digit codes win over series names.

    ``"АВ DRX250 MT 3ф 160А 18ka Legrand 027228"`` -> ``027228`` (not the series ``DRX250``); ties keep
    the first token.
    """
    best: tuple[int, str] | None = None
    for tok in _ARTICLE_TOKEN.findall(text):
        if not _is_article_token(tok):
            continue
        score = sum(ch.isdigit() for ch in tok) + (2 if re.search(r"[-_]", tok) else 0) + (2 if tok.isdigit() else 0)
        if best is None or score > best[0]:
            best = (score, tok)
    return best[1] if best else None


def _find_header(rows: list[list[Any]]) -> tuple[int, dict[str, int]] | None:
    """Locate a header row (within the first 15 rows) and map article/name/qty/price -> column index."""
    for i, row in enumerate(rows[:15]):
        cells = [_cell_str(c).lower() for c in row]
        cols: dict[str, int] = {}
        for j, c in enumerate(cells):
            if not c or len(c) > 40:
                continue
            if "article" not in cols and any(k in c for k in _HDR_ARTICLE):
                cols["article"] = j
            elif "name" not in cols and any(k in c for k in _HDR_NAME):
                cols["name"] = j
            elif "qty" not in cols and any(k in c for k in _HDR_QTY) and not any(k in c for k in _HDR_PRICE):
                cols["qty"] = j
            elif "price" not in cols and any(k in c for k in _HDR_PRICE):
                cols["price"] = j
        if len(cols) >= 2 and ("qty" in cols or "article" in cols):
            return i, cols
    return None


def _line_from_row(cells: list[str], cols: dict[str, int] | None) -> dict[str, Any]:
    raw = " | ".join(c for c in cells if c)
    article: str | None = None
    name: str | None = None
    qty: float | None = None
    if cols:
        j = cols.get("article")
        if j is not None and j < len(cells) and re.search(r"\d", cells[j]):
            article = cells[j]
        j = cols.get("name")
        if j is not None and j < len(cells) and cells[j] and _to_number(cells[j]) is None:
            name = cells[j]
        j = cols.get("qty")
        if j is not None and j < len(cells):
            qty = _to_number(cells[j])
            if qty is None:
                m = _QTY_IN_TEXT.search(cells[j])
                qty = float(m.group(1).replace(",", ".")) if m else None
    if article is None:
        article = _best_article(raw)
    if name is None:
        texts = [c for c in cells if _to_number(c) is None and len(c) > 3 and c != article]
        name = max(texts, key=len) if texts else None
    if qty is None:
        qty = _guess_qty(cells, article)
    return {"raw": raw, "article": article, "name": name, "qty": qty}


def _guess_qty(cells: list[str], article: str | None) -> float | None:
    """Qty without a header: a number next to a 'шт/кол' cell, else the last numeric cell (prefer < 1000)."""
    lowered = [c.lower() for c in cells]
    for j, c in enumerate(lowered):
        if any(k in c for k in ("шт", "кол", "qty", "pcs")):
            m = _QTY_IN_TEXT.search(cells[j])
            if m:
                return float(m.group(1).replace(",", "."))
            for k in (j - 1, j + 1):
                if 0 <= k < len(cells) and (v := _to_number(cells[k])) is not None:
                    return v
    nums = [v for c in cells if c != article and (v := _to_number(c)) is not None and v > 0]
    if not nums:
        m = _QTY_IN_TEXT.search(" ".join(cells))
        return float(m.group(1).replace(",", ".")) if m else None
    small = [v for v in nums if v < 1000 and v.is_integer()]
    return small[-1] if small else nums[-1]


def extract_lines(rows: list[list[Any]]) -> list[dict[str, Any]]:
    """Turn table rows (lists of cells) into spec lines ``{'raw','article','name','qty'}``.

    A header row (артикул / наименование / кол-во ...) is detected and used to pick columns; without one,
    the article is the first article-like token, the name the longest text cell and the quantity a number
    near a 'шт'/'кол' cell or the last small numeric cell (prices >= 1000 are skipped when a smaller
    integer exists).
    """
    header = _find_header(rows)
    start, cols = (header[0] + 1, header[1]) if header else (0, None)
    out: list[dict[str, Any]] = []
    for row in rows[start:]:
        cells = [_cell_str(c) for c in row]
        if not any(cells):
            continue
        out.append(_line_from_row(cells, cols))
    return out


def _summary(lines: list[dict[str, Any]]) -> str:
    n_art = sum(1 for ln in lines if ln.get("article"))
    return f"{len(lines)} строк, {n_art} с артикулами"


# --- LLM helpers ------------------------------------------------------------------------------


def image_block(att: Attachment) -> dict[str, Any]:
    """Anthropic ``image`` content block (base64 JPEG, already resized to <= 1568 px)."""
    data = base64.standard_b64encode(att.path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def text_for_llm(att: Attachment, max_chars: int = 6000) -> str:
    """Compact textual rendering of an attachment for the model prompt."""
    head = f"[Вложение: {att.filename} ({att.kind}), {att.summary}]"
    if att.kind == "image" and not att.text:
        return head
    parts = [head]
    if att.lines:
        parts.append("Строки спецификации (артикул | наименование | кол-во):")
        for i, ln in enumerate(att.lines, 1):
            qty = "" if ln.get("qty") is None else _fmt_qty(ln["qty"])
            parts.append(f"{i}. {ln.get('article') or '-'} | {(ln.get('name') or ln['raw'])[:120]} | {qty}")
    if att.text:
        parts.append("Текст файла:")
        parts.append(att.text)
    out = "\n".join(parts)
    return out if len(out) <= max_chars else out[: max_chars - 1] + "…"


def _fmt_qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else str(q)
