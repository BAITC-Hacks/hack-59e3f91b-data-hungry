# API contract: backend <-> chat widget

Base URL: `PUBLIC_BASE_URL` (locally `http://localhost:8000`). All JSON, UTF-8. CORS is open (`*`).

## POST /api/chat
Request:
```json
{ "session_id": "uuid-or-null", "message": "Есть ли в наличии 027228 Legrand?", "attachment_ids": ["att_..."], "page_url": "https://ekt.kz/catalog/...", "lang": "ru" }
```
`page_url` is optional: the page the widget is embedded on (lets the assistant know which product the user is looking at). `lang` is `ru` (default) or `kk`; the assistant answers in that language.
`session_id`: send `null` on the first call and keep the id from the response. A client-chosen id must match `[A-Za-z0-9_-]{16,64}` (the id is the only key to the cart, so short/guessable ids are replaced by a server-minted one — check `session_id` in every response). `message` is capped at 4000 characters (HTTP 422 above that).

Response (`ChatResponse`, same shape for every chat-like endpoint):
```json
{
  "session_id": "uuid",
  "reply": "markdown text (bold, lists, links allowed)",
  "products": [ { "id": 515291, "name": "...", "article": "200300285_", "price": 64920, "quantity": 23, "in_stock": true,
                  "url": "https://ekt.kz/catalog/.../", "image": "https://...jpg", "brand": "Legrand", "reason": "почему предложен (для аналогов)",
                  "certificates": [] } ],
  "pending_action": { "action_id": "act_...", "type": "add_to_cart",
                      "items": [ { "product_id": 515291, "name": "...", "qty": 2, "max_qty": 23, "price": 64920 } ],
                      "expires_at": "ISO-8601" } ,
  "cart": { "items": [ { "product_id": 515291, "name": "...", "article": "...", "qty": 2, "price": 64920, "sum": 129840, "url": "..." } ],
            "count": 1, "total": 129840, "url": "http://host/cart/<session_id>" },
  "cart_updated": false,
  "cart_applied": [],
  "escalation": { "reason": "...", "contacts": { "phone": "+7 (727) 346-88-88", "whatsapp": "...", "email": "almaty@ekt.kz" } },
  "latency_ms": 2300
}
```
`pending_action` is non-null when the assistant proposed adding something and is waiting for the user's explicit confirmation.
The widget renders **Подтвердить / Отмена** buttons for it. Typing «да, добавь» in chat works too. `cart_applied` lists the quantities actually added by a successful cart request, which can differ from the requested quantities if stock or pack multiplicity limits them.
`certificates` остаётся пустым, пока партнёр не предоставит проверяемые документы по конкретным товарам. Демонстрационные сертификаты и маршрут `/api/certificates/{id}` удалены.

## POST /api/chat/confirm
```json
{ "session_id": "uuid", "action_id": "act_...", "confirm": true }
```
-> `ChatResponse`. On `confirm: true` the cart is updated (quantities are clamped to stock and rounded to pack multiplicity), `cart_updated: true`, and `reply` contains the cart link. On `false` nothing changes.

## POST /api/cart/items
```json
{ "session_id": "uuid", "product_id": 515291, "qty": 1, "request_id": "unique-id-for-this-click" }
```
-> `ChatResponse`. This endpoint is for an explicit customer click on a product card's **В корзину** button. It adds without a second confirmation, but still rechecks live stock and pack multiplicity. The widget generates a new `request_id` for each click and reuses it on a retry to avoid duplicate additions. `cart_applied` reports the quantity added; `cart_updated` is false when nothing could be added. The chat agent does not use this endpoint: its add flow remains a proposal followed by `/api/chat/confirm` or an unambiguous confirmation message.

## POST /api/upload  (multipart/form-data, fields `file`, optional `session_id`)
Accepted: jpg/jpeg/png/webp/gif/bmp/tif/tiff; xlsx/xlsm/xls; docx/doc; pptx/ppt; odt/ods/odp; txt/md/csv/log; PDF; mp3/wav/m4a/ogg/oga/flac/webm/weba/mp4/mpeg/mpga. Max 15 MB.
```json
{ "attachment_id": "att_...", "filename": "spec.xlsx", "kind": "image|excel|word|pdf|document|audio", "summary": "12 строк, найдено 9 артикулов", "session_id": "uuid" }
```
Pass the returned `session_id` and `attachment_ids` in the next `/api/chat` call. Attachments from another session are rejected with HTTP 404. Original bytes and extracted text are cached in `backend/data/attachments.sqlite3` by SHA-256, file kind and processor/model signature; repeated uploads do not call OCR/ASR again. `MEDIA_AI_PROVIDER=openai` (default) uses `gpt-5.6-luna` for image/scanned-PDF text and `gpt-transcribe` for audio; it requires `OPENAI_API_KEY`. `MEDIA_AI_PROVIDER=nitec` uses Chandra OCR and Whisper through `llm.nitec.kz` and requires `NITEC_API_KEY`. OpenAI OCR does not return verified bounding boxes. The default SGR agent accepts only successfully recognized text; the legacy Anthropic agent can additionally inspect image bytes.

## POST /api/upload/jobs and GET /api/upload/jobs/{job_id}?session_id=...

Asynchronous version of upload used by the widget. `POST` uses the same multipart fields and returns HTTP 202:

```json
{ "job_id": "job_...", "session_id": "uuid", "filename": "spec.xlsx", "status": "processing", "stage": "queued", "attachment_id": null, "kind": null, "summary": "", "error": null }
```

Poll `GET` with the returned `session_id`. Stages are `queued`, `checking_cache`, `parsing`, `recognizing`, then `ready` or `failed`. Only `ready` includes a usable `attachment_id`; `failed` includes `error`. A different session gets 404. Jobs live in process memory for one hour; a process restart loses job state.

For local testing only, set `ENABLE_FILE_LAB=1` and bind the backend to `127.0.0.1`. `GET /lab` serves a simple upload page. `POST /api/lab/parse` accepts the same `file` form field and returns `filename`, `kind`, `summary`, `text`, `lines`, `boxes`, `processor`, `sha256`. Both routes reject non-loopback clients and are disabled by default.

## GET /api/cart/{session_id}  -> `cart` object (see above)
## DELETE /api/cart/{session_id}/items/{product_id} -> `cart` object
## GET /cart/{session_id} -> HTML prototype cart page (server-rendered), links to product pages on ekt.kz
## GET /api/products/search?q=...&limit=10 -> `{ "products": [...] }` (same product card shape)
## GET /api/products/{id} -> product card + `detail` (stores, properties, description)
## GET /api/health -> `{ "ok": true, "products": 15035, "model": "gpt-4.1-mini", "llm_configured": true }`

## Widget embedding
```html
<script src="https://host/widget/widget.js" data-api="https://host" data-lang="ru"></script>
```
The widget creates a floating chat button (bottom-right), a chat panel (desktop: 400x640 panel, mobile: full screen),
persists `session_id` in `localStorage` (`ekt_ai_session`), sends `page_url: location.href`.

### Native cart mode (when embedded on ekt.kz itself)
The external demo's prototype cart is separate from the customer's cart on ekt.kz. Browser cookies and same-origin restrictions prevent the demo domain from adding products to the customer's EKT session. Only when the widget runs first-party on ekt.kz can it attempt to sync items to the site's Bitrix cart after a direct button click or confirmed chat action:
```js
fetch('/local/templates/template/ajax/ajax.php', { method: 'POST', credentials: 'same-origin',
  headers: {'X-Requested-With': 'XMLHttpRequest'},
  body: new URLSearchParams({ action: 'add2basket', id: String(product_id), quantity: String(added_qty), kratnost: '1' }) })
```
The request sends only the newly added quantity, not the accumulated prototype-cart quantity. A link to `https://ekt.kz/personal/cart/` must be shown only after a successful same-origin sync; otherwise the widget keeps the prototype-cart link and reports the sync error. This integration still needs end-to-end verification with EKT before it can be treated as a production checkout path.

## Safety rules enforced server-side
- The chat agent can only propose a `pending_action`; the cart changes after the customer's explicit confirmation. A direct product-card **В корзину** click is itself an explicit add request and needs no second confirmation.
- Quantity is clamped to live stock (detail API, fetched at confirmation time) and to pack multiplicity (`KRATNOST_MIN`).
- Prices/stock in replies come from tool results, never from the model's memory. If live detail is unavailable, the indexed list price is not presented as current and stock is `unknown`.
- No payment data is requested or stored.
