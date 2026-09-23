# EKT AI assistant — chat widget

One file (`widget.js`), vanilla JS, Shadow DOM, no build step, no dependencies. Talks to the backend described in
`docs/API_CONTRACT.md`.

## Embedding

```html
<script src="https://HOST/widget/widget.js" data-api="https://HOST" data-lang="ru"></script>
```

| attribute    | default              | meaning                                                        |
|--------------|----------------------|----------------------------------------------------------------|
| `data-api`   | origin of the script | backend base URL (`POST /api/chat`, `/api/chat/confirm`, `/api/upload`, `GET /api/cart/{id}`) |
| `data-lang`  | `ru`                 | `ru` or `kk`; the user can switch in the header (RU / KZ)      |
| `data-open`  | –                    | `1` opens the panel on the first visit                          |
| `data-title` | `Ассистент EKT`      | header title                                                   |

Public API: `window.EktAssistant.open()`, `.close()`, `.send("Есть ли 027228?")`.

State: `session_id` in `localStorage['ekt_ai_session']`; the conversation, pending action and cart in
`sessionStorage['ekt_ai_conv']` (survives navigation between pages of the site). Each message carries
`page_url: location.href` and `lang`.

## Native cart mode (on ekt.kz)

When `location.hostname` ends with `ekt.kz`, a confirmed *add to cart* is also replayed into the site's own
Bitrix basket (`POST /local/templates/template/ajax/basket.php`, `action=add2basket`) and the cart links point to
`https://ekt.kz/personal/cart/` instead of the prototype cart page.

## Local demo

```bash
cd widget && python3 -m http.server 8765
# open http://localhost:8765/demo/index.html  (backend expected on http://localhost:8000)
```

`demo/index.html` imitates an ekt.kz product page (product 027228 Legrand). Check it on desktop and in the
DevTools mobile emulator (the panel goes full screen at <= 640px). With the backend down the widget shows a red
"Не удалось отправить — Повторить" banner.

## Bookmarklet (run the widget on the live ekt.kz)

1. Deploy the backend with a public **https** URL (the widget is served from `/widget/widget.js`).
2. Open `bookmarklet.js`, replace `https://HOST` with that URL and copy the `javascript:(...)()` line.
3. In the browser create a new bookmark and paste the line into its URL field.
4. Open any product page on https://ekt.kz and click the bookmark: the launcher appears bottom-right.
   Confirmed items go into the real site cart (see native cart mode above).

Notes: the backend must answer CORS from `https://ekt.kz` (it sends `*`). If the site ever adds a strict
`Content-Security-Policy` for scripts, the bookmarklet is blocked — use the `<script>` tag embedding instead.

## Layout of `widget.js`

1. config (script tag `data-*`) · 2. i18n (RU/KZ) · 3. styles · 4. state & storage · 5. api client (60 s timeout) ·
6. safe mini-markdown (escape first; `**bold**`, `*italic*`, lists, `[text](url)`, bare URLs) · 7. render ·
8. events & actions · 9. native cart · 10. boot.
