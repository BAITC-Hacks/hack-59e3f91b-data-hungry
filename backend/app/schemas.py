"""Pydantic models for the HTTP API (shapes per docs/API_CONTRACT.md)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

Number = int | float


def normalize_lang(value: str | None) -> str:
    """Map any language hint to 'ru' or 'kk' (default 'ru')."""
    v = (value or "").strip().lower()
    return "kk" if v in ("kk", "kz", "kaz", "kk-kz") else "ru"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(default="", max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list)
    page_url: str | None = None
    lang: str = "ru"

    @field_validator("lang", mode="before")
    @classmethod
    def _lang(cls, v: Any) -> str:
        return normalize_lang(v if isinstance(v, str) else None)


class ConfirmRequest(BaseModel):
    session_id: str
    action_id: str
    confirm: bool = True


class CartAddRequest(BaseModel):
    session_id: str | None = None
    product_id: int = Field(gt=0, lt=2**53)
    qty: int = Field(default=1, gt=0, le=100000)
    request_id: str = Field(min_length=16, max_length=64)


class Certificate(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    url: str | None = None


class StoreStock(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    quantity: int = 0


class ProductCard(BaseModel):
    """Product card as rendered by the widget. Extra keys from catalog.product_card are passed through."""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    article: str | None = None
    price: Number | None = None
    quantity: int | None = None
    in_stock: bool = False
    stock_status: str = "unknown"
    url: str | None = None
    image: str | None = None
    brand: str | None = None
    stores: list[StoreStock] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)
    kratnost: int = 1
    reason: str | None = None
    certificates: list[Certificate] = Field(default_factory=list)


class ProductDetailResponse(ProductCard):
    detail: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    products: list[ProductCard]


class PendingItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    product_id: int
    name: str
    qty: int
    max_qty: int
    price: Number | None = None
    kratnost: int = 1
    note: str | None = None


class PendingAction(BaseModel):
    action_id: str
    type: str = "add_to_cart"
    items: list[PendingItem]
    expires_at: str


class CartItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    product_id: int
    name: str
    article: str | None = None
    qty: int
    price: Number | None = None
    sum: Number = 0
    url: str | None = None


class Cart(BaseModel):
    items: list[CartItem] = Field(default_factory=list)
    count: int = 0
    total: Number = 0
    url: str


class Contacts(BaseModel):
    phone: str
    whatsapp: str
    email: str


class Escalation(BaseModel):
    reason: str
    contacts: Contacts


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    products: list[ProductCard] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    cart: Cart
    cart_updated: bool = False
    cart_applied: list[dict[str, Any]] = Field(default_factory=list)
    escalation: Escalation | None = None
    latency_ms: int = 0


class UploadResponse(BaseModel):
    attachment_id: str
    filename: str
    kind: str
    summary: str = ""
    session_id: str | None = None


class HealthResponse(BaseModel):
    ok: bool = True
    products: int = 0
    model: str
    provider: str | None = None
    llm_configured: bool = False
