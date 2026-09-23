# API contract: backend <-> chat widget

Base URL: `PUBLIC_BASE_URL` (locally `http://localhost:8000`). All JSON, UTF-8. CORS is open (`*`).

## POST /api/chat
Request:
```json
{ "session_id": "uuid-or-null", "message": "Есть ли в наличии 027228 Legrand?", "attachment_ids": ["att_..."], "page_url": "https://ekt.kz/catalog/...", "lang": "ru" }
```
`page_url` is optional: the page the widget is embedded on (lets the assistant know which product the user is looking at). `lang` is `ru` (default) or `kk`; the assistant answers in that language.

Response (`ChatResponse`, same shape for every chat-like endpoint):
```json
{
  "session_id": "uuid",
  "reply": "markdown text (bold, lists, links allowed)",
  "products": [ { "id": 515291, "name": "...", "article": "200300285_", "price": 64920, "quantity": 23, "in_stock": true,
                  "url": "https://ekt.kz/catalog/.../", "image": "https://...jpg", "brand": "Legrand", "reason": "почему предложен (для аналогов)",
                  "certificates": [ {"title": "...", "url": "..."} ] } ],
  "pending_action": { "action_id": "act_...", "type": "add_to_cart",
                      "items": [ { "product_id": 515291, "name": "...", "qty": 2, "max_qty": 23, "price": 64920 } ],
                      "expires_at": "ISO-8601" } ,
  "cart": { "items": [ { "product_id": 515291, "name": "...", "article": "...", "qty": 2, "price": 64920, "sum": 129840, "url": "..." } ],
            "count": 1, "total": 129840, "url": "http://host/cart/<session_id>" },
  "cart_updated": false,
  "escalation": { "reason": "...", "contacts": { "phone": "+7 (727) 346-88-88", "whatsapp": "...", "email": "almaty@ekt.kz" } },
  "latency_ms": 2300
}
```
`pending_action` is non-null when the assistant proposed adding something and is waiting for the user's explicit confirmation.
The widget renders **Подтвердить / Отмена** buttons for it. Typing «да, добавь» in chat works too.

## POST /api/chat/confirm
```json
{ "session_id": "uuid", "action_id": "act_...", "confirm": true }
```
-> `ChatResponse`. On `confirm: true` the cart is updated (quantities are clamped to stock and rounded to pack multiplicity), `cart_updated: true`, and `reply` contains the cart link. On `false` nothing changes.

## POST /api/upload  (multipart/form-data, fields `file`, optional `session_id`)
Accepted: jpg/jpeg/png/webp/gif/bmp/tif/tiff; xlsx/xlsm/xls; docx/doc; pptx/ppt; odt/ods/odp; txt/md/csv/log; PDF; mp3/wav/m4a/ogg/oga/flac/webm/weba/mp4/mpeg/mpga. Max 15 MB.
```json
{ "attachment_id": "att_...", "filename": "spec.xlsx", "kind": "image|excel|word|pdf|document|audio", "summary": "12 строк, найдено 9 артикулов", "session_id": "uuid" }
```
Pass the returned `session_id` and `attachment_ids` in the next `/api/chat` call. Attachments from another session are rejected with HTTP 404. Original bytes and extracted text are cached in `backend/data/attachments.sqlite3` by SHA-256, file kind and processor/model signature; repeated uploads do not call OCR/ASR again. `MEDIA_AI_PROVIDER=openai` (default) uses `gpt-5.6-luna` for image/scanned-PDF text and `gpt-transcribe` for audio; it requires `OPENAI_API_KEY`. `MEDIA_AI_PROVIDER=nitec` uses Chandra OCR and Whisper through `llm.nitec.kz` and requires `NITEC_API_KEY`. OpenAI OCR does not return verified bounding boxes. Without the selected provider key, images remain available to the main vision-capable agent, while audio transcription and scanned-PDF OCR are unavailable.

For local testing only, set `ENABLE_FILE_LAB=1` and bind the backend to `127.0.0.1`. `GET /lab` serves a simple upload page. `POST /api/lab/parse` accepts the same `file` form field and returns `filename`, `kind`, `summary`, `text`, `lines`, `boxes`, `processor`, `sha256`. Both routes reject non-loopback clients and are disabled by default.

## GET /api/cart/{session_id}  -> `cart` object (see above)
## DELETE /api/cart/{session_id}/items/{product_id} -> `cart` object
## GET /cart/{session_id} -> HTML cart page (server-rendered), links to product pages on ekt.kz
## GET /api/products/search?q=...&limit=10 -> `{ "products": [...] }` (same product card shape)
## GET /api/products/{id} -> product card + `detail` (stores, properties, description)
## GET /api/health -> `{ "ok": true, "products": 213456, "model": "claude-opus-5" }`

## Widget embedding
```html
<script src="https://host/widget/widget.js" data-api="https://host" data-lang="ru"></script>
```
The widget creates a floating chat button (bottom-right), a chat panel (desktop: 400x640 panel, mobile: full screen),
persists `session_id` in `localStorage` (`ekt_ai_session`), sends `page_url: location.href`.

### Native cart mode (when embedded on ekt.kz itself)
If `location.hostname` ends with `ekt.kz`, after a confirmed `add_to_cart` the widget ALSO adds each item to the real Bitrix cart:
```js
fetch('/local/templates/template/ajax/basket.php', { method: 'POST', headers: {'X-Requested-With': 'XMLHttpRequest'},
  body: new URLSearchParams({ action: 'add2basket', id: String(product_id), quantity: String(qty), kratnost: '1' }) })
```
and shows the link `https://ekt.kz/personal/cart/` instead of the prototype cart page. (The site's own "в корзину" button does exactly this request.)

## Safety rules enforced server-side
- Cart changes happen ONLY through a confirmed `pending_action` (button or explicit "да/добавь/подтверждаю" message). The LLM cannot mutate the cart directly.
- Quantity is clamped to live stock (detail API, fetched at confirmation time) and to pack multiplicity (`KRATNOST_MIN`).
- Prices/stock in replies come from tool results, never from the model's memory.
- No payment data is requested or stored.
