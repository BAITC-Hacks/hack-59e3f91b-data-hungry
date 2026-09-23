"""Chandra OCR-2 via LLM API."""
from __future__ import annotations

import base64
import logging
import re
import struct
from html.parser import HTMLParser

import httpx

logger = logging.getLogger("ekt_attachments.ocr")

_MODEL = "datalab-to/chandra-ocr-2"

# Real Chandra ocr_layout prompt (from github.com/datalab-to/chandra prompts.py).
# Outputs HTML divs with data-bbox="x0 y0 x1 y1" in normalized 0-1000 coordinates.
_PROMPT = (
    "OCR this image to HTML, arranged as layout blocks. "
    "Each layout block should be a div with the data-bbox attribute representing "
    "the bounding box of the block in x0 y0 x1 y1 format. Bboxes are normalized 0-1000. "
    "The data-label attribute is the label for the block.\n"
    "Use the following labels:\n"
    "- Caption\n- Footnote\n- Equation-Block\n- List-Group\n- Page-Header\n"
    "- Page-Footer\n- Image\n- Section-Header\n- Table\n- Text\n- Complex-Block\n"
    "- Code-Block\n- Form\n- Table-Of-Contents\n- Figure\n- Chemical-Block\n"
    "- Diagram\n- Bibliography\n- Blank-Page\n"
    "Only use these tags ['math', 'br', 'i', 'b', 'u', 'del', 'sup', 'sub', "
    "'table', 'tr', 'td', 'p', 'th', 'div', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', "
    "'ul', 'ol', 'li', 'input', 'a', 'span', 'img', 'hr', 'tbody', 'small', "
    "'caption', 'strong', 'thead', 'big', 'code', 'chem'], "
    "and these attributes ['class', 'colspan', 'rowspan', 'display', 'checked', "
    "'type', 'border', 'value', 'style', 'href', 'alt', 'align', 'data-bbox', 'data-label'].\n"
    "Guidelines:\n"
    "* Text: join lines together properly into paragraphs using <p>...</p> tags.\n"
    "* Tables: Use colspan and rowspan attributes to match table structure.\n"
    "* Lists: Preserve indents and proper list markers.\n"
    "* Make sure the text is accurate and easy for a human to read and interpret."
)

_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG",  "image/png"),
    (b"\xff\xd8", "image/jpeg"),
    (b"GIF8",     "image/gif"),
    (b"RIFF",     "image/webp"),
]


def _image_size(data: bytes) -> tuple[int, int] | None:
    if data[:4] == b"\x89PNG":
        try:
            w, h = struct.unpack(">II", data[16:24])
            return w, h
        except Exception:
            return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 8:
            if data[i] != 0xff:
                break
            marker = data[i + 1]
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + length
    return None


def _detect_mime(data: bytes) -> str:
    for magic, mime in _MAGIC:
        if data[:len(magic)] == magic:
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    return "image/jpeg"


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s).strip()


class _BBoxParser(HTMLParser):
    """Extract (bbox_str, inner_html) from <div data-bbox="...">...</div> blocks."""

    def __init__(self):
        super().__init__()
        self.blocks: list[tuple[str, str]] = []
        self._bbox: str | None = None
        self._buf: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs):
        d = dict(attrs)
        if tag == "div" and "data-bbox" in d:
            self._bbox = d["data-bbox"]
            self._buf = []
            self._depth = 1
        elif self._bbox is not None:
            if tag == "div":
                self._depth += 1
            # Reconstruct tag so we can split on <li>/<br> later
            attr_str = "".join(f' {k}="{v}"' for k, v in attrs)
            self._buf.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str):
        if self._bbox is not None:
            if tag == "div":
                self._depth -= 1
                if self._depth == 0:
                    inner = "".join(self._buf)
                    self.blocks.append((self._bbox, inner))
                    self._bbox = None
                    self._buf = []
                    return
            self._buf.append(f"</{tag}>")

    def handle_data(self, data: str):
        if self._bbox is not None:
            self._buf.append(data)


class _TableParser(HTMLParser):
    """Extract plain table rows/cells from OCR HTML."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            d = dict(attrs)
            self._cell = {
                "colSpan": max(1, _safe_int(d.get("colspan"), 1)),
                "rowSpan": max(1, _safe_int(d.get("rowspan"), 1)),
            }
            self._cell_text = []

    def handle_endtag(self, tag: str):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            text = re.sub(r"\s+", " ", " ".join(s.strip() for s in self._cell_text if s.strip())).strip()
            self._cell["text"] = text
            self._row.append(self._cell)
            self._cell = None
            self._cell_text = []
        elif tag == "tr" and self._row is not None:
            if any(str(c.get("text") or "").strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str):
        if self._cell is not None:
            self._cell_text.append(data)


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_table_rows(inner_html: str) -> list[list[dict]]:
    if "<table" not in inner_html.lower():
        return []
    parser = _TableParser()
    try:
        parser.feed(inner_html)
    except Exception:
        return []
    return parser.rows


def _block_to_boxes(bbox_str: str, inner_html: str) -> list[dict]:
    """Split one layout block into per-line boxes, dividing y-range proportionally."""
    parts = [float(p) for p in re.findall(r"[\d.]+", bbox_str)[:4]]
    if len(parts) < 4:
        return []
    x0, y0, x1, y1 = parts

    table_rows = _extract_table_rows(inner_html)
    if table_rows:
        row_count = len(table_rows)
        col_count = max(sum(max(1, int(c.get("colSpan") or 1)) for c in row) for row in table_rows)
        if row_count and col_count:
            table_id = f"ocr_table_{round(x0)}_{round(y0)}_{round(x1)}_{round(y1)}"
            row_h = (y1 - y0) / row_count
            col_w = (x1 - x0) / col_count
            boxes = []
            for row_idx, row in enumerate(table_rows):
                col_idx = 0
                for cell in row:
                    col_span = max(1, int(cell.get("colSpan") or 1))
                    row_span = max(1, int(cell.get("rowSpan") or 1))
                    cx0 = x0 + col_idx * col_w
                    cx1 = x0 + min(col_count, col_idx + col_span) * col_w
                    cy0 = y0 + row_idx * row_h
                    cy1 = y0 + min(row_count, row_idx + row_span) * row_h
                    text = str(cell.get("text") or "").strip()
                    if text:
                        boxes.append({
                            "text": text,
                            "box": [
                                round(cx0 / 10, 2),
                                round(cy0 / 10, 2),
                                round(cx1 / 10, 2),
                                round(cy1 / 10, 2),
                            ],
                            "kind": "table_cell",
                            "table_id": table_id,
                            "row": row_idx,
                            "col": col_idx,
                            "rowSpan": row_span,
                            "colSpan": col_span,
                            "rows": row_count,
                            "cols": col_count,
                        })
                    col_idx += col_span
            if boxes:
                return boxes

    # Try <li> items first, then <br>, then whole block as one item
    li_items = re.findall(r"<li[^>]*>(.*?)</li>", inner_html, re.DOTALL)
    if li_items:
        texts = [re.sub(r"\s+", " ", _strip_html(s)).strip() for s in li_items]
    else:
        br_parts = re.split(r"<br\s*/?>", inner_html, flags=re.DOTALL | re.IGNORECASE)
        texts = [re.sub(r"\s+", " ", _strip_html(s)).strip() for s in br_parts]

    texts = [t for t in texts if t]
    if not texts:
        return []

    n = len(texts)
    h = (y1 - y0) / n
    boxes = []
    for i, text in enumerate(texts):
        iy0 = y0 + i * h
        iy1 = y0 + (i + 1) * h
        boxes.append({"text": text, "box": [
            round(x0 / 10, 2),
            round(iy0 / 10, 2),
            round(x1 / 10, 2),
            round(iy1 / 10, 2),
        ]})
    return boxes


def _parse_html(raw: str) -> list[dict]:
    """Parse Chandra HTML output with data-bbox attributes (0-1000 normalized)."""
    parser = _BBoxParser()
    try:
        parser.feed(raw)
    except Exception:
        pass

    boxes = []
    for bbox_str, inner_html in parser.blocks:
        boxes.extend(_block_to_boxes(bbox_str, inner_html))
    return boxes


async def _call_chandra(client: httpx.AsyncClient, b64: str, mime: str, endpoint: str, api_key: str, model: str) -> str:
    r = await client.post(
        endpoint,
        json={
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            "max_tokens": 4096,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def ocr_image(data: bytes, api_url: str, api_key: str, llm_model: str = _MODEL) -> list[dict]:
    """Call Chandra OCR-2, return [{"text": str, "box": [x1%, y1%, x2%, y2%]}]."""
    mime = _detect_mime(data)
    b64 = base64.b64encode(data).decode()
    endpoint = api_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    img_size = _image_size(data)
    if img_size:
        logger.info("Image size: %dx%d", img_size[0], img_size[1])

    async with httpx.AsyncClient(timeout=90.0) as client:
        for attempt in range(3):
            raw = await _call_chandra(client, b64, mime, endpoint, api_key, llm_model)
            boxes = _parse_html(raw)
            logger.info("Parsed %d boxes (attempt %d)", len(boxes), attempt + 1)
            if boxes:
                return boxes
            logger.warning("No boxes on attempt %d, retrying...", attempt + 1)

    return []
