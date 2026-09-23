"""FastAPI application: chat, confirmation, uploads, cart (JSON + HTML page), product lookup, widget static files."""
from __future__ import annotations

import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Path as PathParam, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import agent, attachments, catalog, certificates, config, ekt_api, recognition
from .cart import PendingActionError, cart_store
from .schemas import (
    Cart,
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    HealthResponse,
    ProductDetailResponse,
    SearchResponse,
    UploadResponse,
    normalize_lang,
)
from .sessions import SESSION_ID_RE, session_store

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ekt.api")

ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "pdf",
    "xlsx", "xlsm", "xls", "docx", "doc", "pptx", "ppt", "odt", "ods", "odp",
    "txt", "md", "csv", "log",
    "mp3", "wav", "m4a", "ogg", "oga", "flac", "webm", "weba", "mp4", "mpeg", "mpga",
}
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_MB * 1024 * 1024
WIDGET_DIR = config.BACKEND_DIR.parent / "widget"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_DIGIT_RUN_RE = re.compile(r"\d{8,}")  # card/account-like digit runs are masked before anything reaches the log
ProductId = PathParam(gt=0, lt=2**53)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        catalog.ensure_db()
        log.info("catalog ready: %s products", catalog.count_products())
    except RuntimeError as exc:
        log.warning("catalog DB not available: %s", exc)
    log.info("LLM provider=%s model=%s effort=%s configured=%s", agent.llm_provider(), agent.effective_model(), config.LLM_EFFORT, agent.llm_configured())
    yield


app = FastAPI(title="ekt.kz AI assistant", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["money"] = agent.fmt_money

if WIDGET_DIR.is_dir():
    app.mount("/widget", StaticFiles(directory=str(WIDGET_DIR)), name="widget")


def _check_session_id(session_id: str) -> str:
    if not SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="bad session_id")
    return session_id


def _loggable(message: str) -> str:
    """Short preview of a user message for the log with long digit runs masked (no payment data is ever stored)."""
    return _DIGIT_RUN_RE.sub("***", message[:80])


# ---- chat -------------------------------------------------------------------------------------------------------
@app.post("/api/chat", response_model=ChatResponse)
async def post_chat(req: ChatRequest) -> Any:
    session = session_store.get_or_create(req.session_id)
    atts = [a for a in (attachments.get_attachment(i, session.id) for i in req.attachment_ids) if a is not None]
    if len(atts) != len(req.attachment_ids):
        raise HTTPException(status_code=404, detail="Вложение не найдено в этой сессии")
    t0 = time.perf_counter()
    async with session.lock:  # one turn at a time per session (two tabs / direct API calls)
        result = await agent.chat(session, req.message, atts, req.page_url, req.lang)
    log.info(
        "chat sid=%s lang=%s atts=%d len=%d msg=%r -> %d products, pending=%s, %d ms",
        session.id,
        req.lang,
        len(atts),
        len(req.message),
        _loggable(req.message),
        len(result["products"]),
        bool(result["pending_action"]),
        int((time.perf_counter() - t0) * 1000),
    )
    return result


@app.post("/api/chat/confirm", response_model=ChatResponse)
async def post_confirm(req: ConfirmRequest) -> Any:
    """Apply (confirm=true) or drop (confirm=false) the pending proposal. 409 when nothing matching is pending."""
    t0 = time.perf_counter()
    session = session_store.get_or_create(req.session_id)
    lang = normalize_lang(session.lang)
    async with session.lock:  # never interleave with a chat turn on the same session
        pending = cart_store.pending(session.id)
        if pending is None or pending["action_id"] != req.action_id:
            raise HTTPException(status_code=409, detail="Нет действия, ожидающего подтверждения (или оно устарело)")
        if req.confirm:
            try:
                result = await cart_store.confirm(session.id, req.action_id)
            except PendingActionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            reply = agent.render_confirmation(result, lang)
            agent.record_exchange(session, "[Кнопка «Подтвердить»]", reply)
            cart_updated = bool(result["applied"])
            log.info("confirm sid=%s applied=%d skipped=%d", session.id, len(result["applied"]), len(result["skipped"]))
        else:
            cart_store.reject(session.id, req.action_id)
            reply = agent.render_rejection(lang)
            agent.record_exchange(session, "[Кнопка «Отмена»]", reply)
            cart_updated = False
    return {
        "session_id": session.id,
        "reply": reply,
        "products": [],
        "pending_action": None,
        "cart": cart_store.get_json(session.id),
        "cart_updated": cart_updated,
        "escalation": None,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


# ---- uploads ----------------------------------------------------------------------------------------------------
async def _read_upload(file: UploadFile) -> tuple[str, bytes, str]:
    filename = Path(file.filename or "file").name
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Неподдерживаемый формат .{ext}. Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл больше {config.MAX_UPLOAD_MB} МБ")
    if not content:
        raise HTTPException(status_code=400, detail="Пустой файл")
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return filename, content, mime


@app.post("/api/upload", response_model=UploadResponse)
async def post_upload(file: UploadFile, session_id: str | None = Form(default=None)) -> Any:
    filename, content, mime = await _read_upload(file)
    session = session_store.get_or_create(session_id)
    att = await attachments.save_and_parse(filename, content, mime, session.id)
    log.info("upload %s kind=%s bytes=%d -> %s", filename, att.kind, len(content), att.id)
    return {"attachment_id": att.id, "filename": att.filename, "kind": att.kind,
            "summary": att.summary or "", "session_id": session.id}


def _require_local_file_lab(request: Request) -> None:
    host = request.client.host if request.client else ""
    url_host = request.url.hostname
    local_host = url_host in {"127.0.0.1", "localhost", "::1"}
    if host == "testclient" and url_host == "testserver":
        local_host = True
    origin = request.headers.get("origin")
    same_origin = not origin or origin.rstrip("/") == str(request.base_url).rstrip("/")
    if not config.ENABLE_FILE_LAB or host not in {"127.0.0.1", "::1", "testclient"} or not local_host or not same_origin:
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/lab", response_class=FileResponse)
async def file_lab(request: Request) -> Any:
    """Local-only upload playground; intentionally disabled in deployments."""
    _require_local_file_lab(request)
    return FileResponse(TEMPLATES_DIR / "file_lab.html", media_type="text/html")


@app.post("/api/lab/parse")
async def file_lab_parse(request: Request, file: UploadFile) -> Any:
    _require_local_file_lab(request)
    filename, content, mime = await _read_upload(file)
    session = session_store.get_or_create(None)
    att = await attachments.save_and_parse(filename, content, mime, session.id)
    return {"filename": att.filename, "kind": att.kind, "summary": att.summary,
            "text": att.text, "lines": att.lines, "boxes": att.boxes,
            "processor": recognition.cache_signature(att.kind), "sha256": att.sha256}


# ---- cart -------------------------------------------------------------------------------------------------------
@app.get("/api/cart/{session_id}", response_model=Cart)
async def get_cart(session_id: str) -> Any:
    return cart_store.get_json(_check_session_id(session_id))


@app.delete("/api/cart/{session_id}/items/{product_id}", response_model=Cart)
async def delete_cart_item(session_id: str, product_id: int = ProductId) -> Any:
    return cart_store.remove(_check_session_id(session_id), product_id)


@app.get("/cart/{session_id}", response_class=HTMLResponse)
async def cart_page(request: Request, session_id: str) -> Any:
    cart = cart_store.get_json(_check_session_id(session_id))
    return templates.TemplateResponse(
        request, "cart.html", {"cart": cart, "session_id": session_id, "site": config.EKT_SITE, "checkout_url": config.EKT_CART_URL}
    )


# ---- products ---------------------------------------------------------------------------------------------------
@app.get("/api/products/search", response_model=SearchResponse)
async def products_search(q: str = "", limit: int = 10) -> Any:
    q = q.strip()
    if not q:
        return {"products": []}
    limit = max(1, min(limit, 20))
    found = catalog.get_by_article(q)
    seen = {p["id"] for p in found}
    found += [p for p in catalog.search(q, limit=limit * 2) if p["id"] not in seen]  # over-fetch: in-stock first
    cards = [agent.with_certificates(c) for c in await catalog.product_cards(found[: limit * 2])]
    return {"products": agent.rank_in_stock_first(cards)[:limit]}


@app.get("/api/products/{product_id}", response_model=ProductDetailResponse)
async def product_get(product_id: int = ProductId) -> Any:
    product = catalog.get_product(product_id)
    detail = await ekt_api.fetch_detail(product_id)
    if product is None:
        if detail is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        product = {k: detail.get(k) for k in ("id", "name", "article", "price", "image", "url")}
        product["brand"] = None
    card = agent.with_certificates(await catalog.product_card(product, detail, with_detail=False), detail)
    card["detail"] = (
        {
            "stores": ekt_api.stores_in_stock(detail),
            "properties": catalog.humanize_properties(detail.get("properties") or {}),
            "description": detail.get("description"),
        }
        if detail
        else None
    )
    return card


# ---- certificates (demo registry) -------------------------------------------------------------------------------
@app.get("/api/certificates/{cert_id}", response_class=HTMLResponse)
async def certificate_page(cert_id: str) -> Any:
    """Printable card for a certificate from the demo registry (clearly watermarked as a demo document)."""
    status = 200 if certificates.get_certificate(cert_id) else 404
    return HTMLResponse(certificates.render_certificate_html(cert_id), status_code=status)


# ---- health -----------------------------------------------------------------------------------------------------
@app.get("/api/health", response_model=HealthResponse)
async def health() -> Any:
    try:
        products = catalog.count_products()
    except Exception:
        products = 0
    return {"ok": True, "products": products, "model": agent.effective_model(), "provider": agent.llm_provider(), "llm_configured": agent.llm_configured()}
