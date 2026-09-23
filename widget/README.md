# EKT AI assistant — chat widget

One file (`widget.js`), vanilla JS, Shadow DOM, no build step, no dependencies. Talks to the backend described in
`docs/API_CONTRACT.md`. It is styled to look like a native part of ekt.kz: the tokens come from `docs/DESIGN.md`.

## Embedding

```html
<script src="https://HOST/widget/widget.js" data-api="https://HOST" data-lang="ru"></script>
```

| attribute    | default              | meaning                                                        |
|--------------|----------------------|----------------------------------------------------------------|
| `data-api`   | origin of the script | backend base URL (`POST /api/chat`, `/api/chat/confirm`, `/api/cart/items`, `/api/upload`, `GET /api/cart/{id}`) |
| `data-lang`  | `ru`                 | `ru` or `kk`; the user can switch in the header (RU / KZ)      |
| `data-open`  | –                    | `1` opens the panel on the first visit                          |
| `data-title` | `Ассистент EKT`      | header title (shown under the logo)                             |

Public API: `window.EktAssistant.open()`, `.close()`, `.send("Есть ли 027228?")`.

State: `session_id` in `localStorage['ekt_ai_session']`; the conversation, pending action and cart in
`sessionStorage['ekt_ai_conv']` (survives navigation between pages of the site). Each message carries
`page_url: location.href` and `lang`.

## Design (see `docs/DESIGN.md`)

- Tokens are CSS custom properties on the shadow host: `--ekt-font`, `--ekt-text #212529`, `--ekt-heading #0b4366`,
  `--ekt-blue #2c7294`, `--ekt-yellow #f4b301` (+ `--ekt-yellow-hover #e0a500`), `--ekt-bg #f2f2f2`,
  `--ekt-border #ddd`, `--ekt-muted #888`, `--ekt-success #198754`, `--ekt-danger #dc3545`, radii 3 / 5 / 8 px.
  To retune, edit the `:host{...}` rule at the top of section 3 of `widget.js`.
- Font: PT Sans (the site font), fallback Helvetica/Arial. `@import` inside a shadow root is unreliable, so the widget
  appends a Google Fonts `<link>` to `document.head` once (skipped if the page already loads PT Sans, as ekt.kz does).
- Header = the site's blue top bar with the real logo (`docs/design/logo.svg`, inlined and recoloured white) and the
  title under it; RU/KZ, «Менеджер» and ✕ in white. Launcher = yellow circle with a white chat icon, bottom-right
  (the site's WhatsApp button sits mid-right).
- Buttons: primary («В корзину», «Подтвердить», send) yellow with white text, 3 px radius; secondary («Отмена») white
  with a #ddd border; «На сайте» blue outline like the site's «Каталог».
- Assistant messages are white cards on the #f2f2f2 background, user messages #e8f1f6. Product cards mimic the catalog
  tiles: 72×72 photo, name in #0b4366 bold, article muted 13 px, price 18 px bold with ₸, availability as a coloured dot
  + text (green / red / orange «Под заказ»). Pending block: 2 px yellow border on #fff8e1. Cart bar: #0b4366 with white
  text and an underlined link. Quick-reply chips: white, blue border and text, 20 px radius. Panel radius 8 px.
- Mobile (<= 640 px): full-screen panel, launcher hidden while open, «Менеджер» collapses to an icon, 16 px input.

Clicking **В корзину** on a product card is the customer's explicit request to add that product. The widget sends
`POST /api/cart/items` and updates the prototype cart immediately after the backend checks live stock. The chat agent
has a separate two-step flow: it proposes items, then waits for **Подтвердить** or an unambiguous confirmation message
before changing the cart.

## Native cart mode (on ekt.kz)

The external demo uses its own prototype cart. It cannot update a visitor's ekt.kz cart across domains because the
site's session cookie and basket endpoint are first-party. When the widget runs on ekt.kz itself, it attempts to sync
newly added quantities to the site's Bitrix basket via same-origin
`POST /local/templates/template/ajax/ajax.php` (`action=add2basket`). Only a successful sync can lead to the EKT cart
link; on failure, the prototype cart link remains available. This integration needs end-to-end verification with EKT
before production use.

## Local demo

The backend serves this folder, so with `uvicorn` running on :8000 just open
http://localhost:8000/widget/demo/index.html. Standalone:

```bash
cd widget && python3 -m http.server 8765
# open http://localhost:8765/demo/index.html  (backend expected on http://localhost:8000)
```

`demo/index.html` imitates the EKT home page with catalog categories, example products linked to real `ekt.kz` cards,
and the assistant launcher at bottom right. Its search bar sends a query to the assistant. The former product-page
demo remains at `demo/product.html`. Both pages are clearly labelled as demos; loading this widget on the actual
`ekt.kz` site requires adding the script tag there (or using the bookmarklet for a personal preview). With the backend
down the widget shows a red "Не удалось отправить — Повторить" banner.

## Bookmarklet (run the widget on the live ekt.kz)

1. Deploy the backend with a public **https** URL (the widget is served from `/widget/widget.js`).
2. Open `bookmarklet.js`, replace `https://HOST` with that URL and copy the `javascript:(...)()` line.
3. In the browser create a new bookmark and paste the line into its URL field.
4. Open any product page on https://ekt.kz and click the bookmark: the launcher appears bottom-right.
   Test cart syncing with a disposable session and verify the resulting basket on ekt.kz (see native cart mode above).

Notes: the backend must answer CORS from `https://ekt.kz` (it sends `*`). If the site ever adds a strict
`Content-Security-Policy` for scripts, the bookmarklet is blocked — use the `<script>` tag embedding instead.

## Layout of `widget.js`

1. config (script tag `data-*`) · 2. i18n (RU/KZ) · 3. styles & icons (tokens on `:host`, CSS string, SVG icons,
inlined logo) · 4. state & storage · 5. api client (60 s timeout, 5 min for uploads) · 6. safe mini-markdown (escape
first; `**bold**`, `*italic*`, lists, `[text](url)`, bare URLs) · 7. render · 8. events & actions · 9. native cart ·
10. boot.
