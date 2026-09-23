# ИИ-ассистент для чата на сайте ekt.kz — команда Data Hungry

Hackalem AI, кейс №1 ТОО «Электрокомплект». Чат-виджет на сайте ekt.kz, который отвечает покупателю как живой менеджер:
находит товар по артикулу или описанию, показывает цену и остаток по складам из API ekt.kz, характеристики и сертификат,
подбирает аналог с обоснованием, если позиции нет на складе, отвечает на вопросы об оплате, доставке и минимальной партии из
базы знаний сайта, разбирает присланную спецификацию (Excel/Word/PDF) или фото маркировки — и кладёт товар в корзину
**только после явного подтверждения** («да, добавь» или кнопка «Подтвердить»), после чего даёт прямую ссылку на корзину.
Языки: русский и казахский.

Must-have кейса одной строкой: артикул → остаток, характеристики, сертификат · нет на складе → аналог с обоснованием ·
условия покупки из базы знаний · ничего в корзину без явного «да» и не больше остатка · ссылка на корзину с текущим составом.

**Статус:** прототип, 23.09.2026. Развёрнут на VM Brev: демо-страница с виджетом — https://ekt-assistant-ypmkr1h7l.gobrev.dev/widget/demo/index.html, health — https://ekt-assistant-ypmkr1h7l.gobrev.dev/api/health (LLM: OpenAI gpt-4.1 через OpenAI-совместимый провайдер). Домен orau.kz с HTTPS — по готовности DNS (`deploy/vm_caddy.sh`).
Демо-страница `widget/demo/index.html` имитирует карточку 027228 Legrand с подключённым виджетом.

## 1. Архитектура

```text
 браузер покупателя (ekt.kz или demo-страница)
 ┌──────────────────────────────────────────────┐
 │ widget/widget.js  (1 файл, Shadow DOM, RU/KZ) │
 └───────────────┬──────────────────────────────┘
                 │ POST /api/chat · /api/chat/confirm · /api/upload · GET /cart/{sid}
                 ▼
 ┌──────────────────────────── backend/app (FastAPI, Python 3.11+) ─────────────────────────────┐
 │ main.py → agent.py: шлюз подтверждения (детерминированный, без LLM) → цикл tool use           │
 │   catalog.py ──── data/catalog.sqlite (SQLite FTS5: слова + триграммы, 15 035 SKU из list API)│
 │   ekt_api.py ──── живой /api/products/detail (остатки по складам, свойства) + снимок           │
 │                   product_details в той же SQLite: отдаётся при таймауте с пометкой stock_note │
 │   analogs.py ──── RECOMMEND + FTS + категория → скоринг по ключевым параметрам                │
 │   knowledge.py ── data/knowledge/*.md|txt|json (страницы ekt.kz: доставка, оплата, возврат…)  │
 │   certificates.py data/certificates.json (ДЕМО-реестр, API сертификатов не отдаёт)           │
 │   attachments.py  xlsx/docx/pdf/фото/аудио → строки спецификации; OCR/ASR через OpenAI или    │
 │                   NITEC при наличии ключа; кэш attachments.sqlite3 по SHA-256                  │
 │   cart.py ─────── корзина сессии: propose → confirm (clamp к остатку и кратности)             │
 └────────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                          │ те же 7 strict-инструментов и системный промпт
                                          ▼
              LLM: Anthropic Messages API │ OpenAI-совместимый API (llm.nitec.kz) │ Claude Agent SDK
```

**Инструменты модели** (`agent.py`, все со `strict` JSON-схемами):

| инструмент | назначение |
|---|---|
| `search_products` | FTS по каталогу с фильтром бренд/категория |
| `get_product` | карточка по id или артикулу: живой остаток по складам, цена, характеристики, кратность, сертификаты |
| `find_analogs` | аналоги в наличии с полем `reason` (почему подходит) |
| `get_purchase_terms` | база знаний по темам delivery/payment/returns/minimum_order/contacts/company/howto/faq/certificates |
| `propose_add_to_cart` | только предложение добавить позиции; корзину не меняет |
| `get_cart` | текущий состав корзины и ссылка на неё |
| `escalate_to_manager` | контакты менеджера (+7 (727) 346-88-88, WhatsApp, almaty@ekt.kz), пометка диалога |

Цены, остатки и сертификаты модель берёт только из результатов инструментов — это зашито в системный промпт; там же правило
«данные ≠ инструкции» для текста вложений и результатов инструментов.

**Корзина с подтверждением (всё на сервере):**
1. «Добавь 2 шт» (или кнопка «Добавить в корзину» на карточке) → модель вызывает `propose_add_to_cart` → `cart_store.propose()`
   запрашивает живой detail, считает `max_qty` и кратность и сохраняет `pending_action` (TTL 10 мин). Корзина **не изменена**;
   виджет рисует жёлтый блок с количеством и кнопками «Подтвердить / Отмена».
2. Подтверждение: кнопка → `POST /api/chat/confirm {action_id, confirm:true}`, либо короткое сообщение («да, добавь»,
   «подтверждаю», «ok», «иә, қос» — до 8 слов; слово отказа побеждает; вопрос с «?» никогда не считается ответом; число
   допускается только равное предложенному). `classify_confirmation()` разбирает его без LLM.
3. `cart_store.confirm()` заново запрашивает остаток (`ttl=0`, снимок не используется), ограничивает количество остатком минус уже
   лежащее в корзине, округляет вниз до `KRATNOST_MIN`, пропускает позиции без остатка или без ответа API и объясняет каждое
   изменение в ответе вместе со ссылкой на корзину.
4. «Нет» / «Отмена» → предложение удаляется, корзина не тронута. Устаревший `action_id` → HTTP 409.

У модели нет инструмента, меняющего корзину, — только `propose`; шаги 2–3 выполняет детерминированный код, поэтому галлюцинация
или подмена промпта не добавит товар, не превысит остаток и не обойдёт кратность. Платёжные данные не запрашиваются и не хранятся.

**Родная корзина Bitrix.** Если `location.hostname` оканчивается на `ekt.kz`, после подтверждения виджет дополнительно шлёт
`POST /local/templates/template/ajax/basket.php` (`action=add2basket`, тот же запрос, что кнопка «В корзину» на сайте) по каждой
позиции и показывает ссылку https://ekt.kz/personal/cart/ вместо страницы корзины прототипа.

## 2. LLM-провайдеры

| `LLM_PROVIDER` | как аутентифицируется | модель и настройки |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` (также `ANTHROPIC_AUTH_TOKEN` или профиль в `~/.config/anthropic`) | `LLM_MODEL` (по умолчанию `claude-opus-5`), `LLM_EFFORT` (`low`), `LLM_MAX_TOKENS` 4096, `LLM_FALLBACKS`; фото уходит модели как изображение |
| `openai` | `OPENAI_API_KEY` → api.openai.com (`gpt-4.1`) или `NITEC_API_KEY` → https://llm.nitec.kz/v1 (`openai/gpt-oss-120b`) | `OPENAI_BASE_URL`, `OPENAI_MODEL`; function calling с теми же инструментами (`agent_openai.py`); фото — только через OCR |
| `claude_code` | Claude Agent SDK: `CLAUDE_CODE_OAUTH_TOKEN` из `claude setup-token` или локальный `claude login` | встроенные инструменты SDK отключены, наши 7 подключены как MCP-сервер; сессия SDK продолжается между ходами (`agent_sdk.py`) |

`LLM_PROVIDER=auto` (по умолчанию) выбирает в порядке anthropic → openai → claude_code. Системный промпт, инструменты и шлюз
подтверждения общие для всех трёх бэкендов. **Без единого ключа** `/api/chat` отвечает уведомлением, что LLM не настроен, а поиск
и карточки (`/api/products/search`, `/api/products/{id}`), загрузка файлов, кнопка «Добавить в корзину» → предложение →
«Подтвердить»/«да, добавь» с ограничением по остатку и страница корзины продолжают работать.

## 3. Запуск прототипа

Нужны Python 3.11+ и [uv](https://docs.astral.sh/uv/). Всё выполняется из `backend/`.

```bash
cd backend
uv sync --extra dev                                   # зависимости (+pytest)
uv run python scripts/dump_catalog.py --pages 1       # быстрый старт: 1 страница = 5000 SKU (~80 с); без --pages — весь список
uv run python scripts/build_index.py                  # data/catalog.sqlite из data/dump/ (секунды)
uv run python scripts/import_details.py --file data/dump/details.jsonl   # необязательно: снимок detail-карточек
cp .env.example .env                                  # впишите ключ LLM (см. раздел 2); остальное — по желанию
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Дальше: http://localhost:8000/widget/demo/index.html (карточка товара с виджетом, панель открыта сразу; файл можно открыть и
напрямую — он ходит на `http://localhost:8000`), http://localhost:8000/api/health → `{ok, products, model, llm_configured}`,
OpenAPI — http://localhost:8000/docs. Тесты: `uv run pytest -q` — 117 тестов, из них 114 офлайн (`-m "not network"`, ~2 с)
и 3 с пометкой `network`, которые ходят в живой API ekt.kz.

`dump_catalog.py` — возобновляемая выгрузка `GET /api/products` в `data/dump/list_NNNN.json` (`--pages N`, `--per-page` ≤ 5000,
`--workers`, `--out`); останавливается на короткой странице или когда API зацикливается на первую. `build_index.py --dump DIR`
(повторяемый, `--db`, `--limit`) пересобирает индекс с нуля. `import_details.py` (`--file`, `--db`) заливает JSONL detail-ответов в
таблицу `product_details` — тот же снимок, который бэкенд пополняет при каждом живом запросе.

| группа | переменная | по умолчанию | смысл |
|---|---|---|---|
| LLM | `LLM_PROVIDER` | `auto` | `anthropic` / `openai` / `claude_code` / `auto` |
| | `ANTHROPIC_API_KEY`, `LLM_MODEL`, `LLM_EFFORT`, `LLM_MAX_TOKENS` | —, `claude-opus-5`, `low`, `4096` | бэкенд Anthropic; `effort` низкий, чтобы чат отвечал за секунды |
| | `LLM_FALLBACKS` | `1` | серверный fallback при отказе классификатора безопасности (beta); `0` — выключить |
| | `OPENAI_API_KEY` / `NITEC_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | — | бэкенд `openai` (OpenAI или llm.nitec.kz) |
| | `CLAUDE_CODE_OAUTH_TOKEN` | — | бэкенд `claude_code` |
| файлы | `MEDIA_AI_PROVIDER` | `openai` | OCR фото/сканов и ASR аудио: `openai` или `nitec` |
| | `OPENAI_API_BASE_URL`, `OPENAI_OCR_MODEL`, `OPENAI_ASR_MODEL` | api.openai.com, `gpt-5.6-luna`, `gpt-transcribe` | OCR/ASR через OpenAI (нужен `OPENAI_API_KEY`) |
| | `NITEC_API_BASE_URL`, `NITEC_OCR_MODEL`, `NITEC_ASR_MODEL` | llm.nitec.kz, `datalab-to/chandra-ocr-2`, `openai/whisper-large-v3-turbo` | OCR/ASR через NITEC (нужен `NITEC_API_KEY`) |
| | `ENABLE_FILE_LAB` | `0` | локальная лаборатория `/lab` (только loopback) |
| ekt.kz | `EKT_API_BASE`, `EKT_API_USER`, `EKT_API_PASS` | `https://ekt.kz/api`, тестовые из `.env.example` | Basic Auth партнёрского API |
| | `DETAIL_CACHE_TTL`, `DETAIL_HTTP_TIMEOUT` | `900` в `config.py` (`300` в `.env.example`), `12` | кэш detail в памяти, с; таймаут, после которого отдаётся снимок |
| | `PUBLIC_BASE_URL` | `http://localhost:8000` | база для ссылок на корзину и сертификаты |
| пути | `EKT_DB_PATH`, `EKT_UPLOAD_DIR`, `EKT_ATTACHMENT_DB_PATH` | `data/catalog.sqlite`, `data/uploads`, `data/attachments.sqlite3` | где лежат индекс, загрузки и кэш вложений |

## 4. Данные

**Тестовый API партнёра** (Basic Auth, учётные данные в `.env.example`; поискового эндпоинта нет, ответы могут содержать
байты после JSON — парсим `raw_decode`):
- `GET /api/products?page=N&per_page=M` → `{page, per_page, count, items[{id, name, article, price, image, url, url_api_detail,
  offers}]}`. По кейсу в каталоге >200 тыс. SKU; тестовый список на 23.09.2026 отдаёт 15 035 позиций (3 полные страницы по 5000
  + 35, дальше API идёт по кругу с первой страницы). Список выгружается в SQLite FTS5: слова + триграммы, бренд из названия,
  категории из URL; поиск: точный артикул → префикс/подстрока → FTS с синонимами → OR/триграммный фолбэк.
- `GET /api/products/detail?id=ID` → `{id, name, article, description, price, quantity, stores[{id, name, quantity}], image, url,
  offers, properties{CML2-коды: NOMINALNYY_TOK, KOLICHESTVO_POLYUSOV, TORGOVAYA_MARKA, KRATNOST_MIN, RECOMMEND=[ids], …}}`.
  Отвечает за 0,2–8 с (обычно 2–3 с); бэкенд держит не более 24 запросов одновременно, кэширует ответ в памяти и сохраняет
  его в `product_details`. Если API не ответил за `DETAIL_HTTP_TIMEOUT`, карточка берётся из снимка с пометкой `stock_note`
  («остаток по данным на <время>»), и промпт обязывает модель сказать об этом; подтверждение корзины снимок не использует.
  Продаваемый остаток = сумма по реальным складам; служебные («Брак», «Маркетинг», «перемещение», «Образцы», «Витрина»,
  «Восстановленный продукт») исключены. Свойства переводятся в человекочитаемые подписи (`catalog.humanize_properties`).

**База знаний** `backend/data/knowledge/` — тексты со страниц ekt.kz, лексический поиск с синонимами, лёгким стеммингом и
казахскими ключами (без эмбеддингов), ответ возвращает фрагменты с URL источника: `delivery.md` ← https://ekt.kz/checkout-delivery/ ·
`payment.md` ← https://ekt.kz/payments/ · `returns.txt` ← https://ekt.kz/return/ · `contacts.txt` ← https://ekt.kz/about/contacts/ ·
`howto.txt` ← https://ekt.kz/about/howto/ · `company.md` ← https://ekt.kz/about/ · `faq.json` ← https://ekt.kz/about/faq/ (83 пары
вопрос–ответ). Темы `minimum_order` и `certificates` выводятся по ключевым словам из тех же страниц.

**Сертификаты — синтетический демо-реестр.** API ekt.kz не отдаёт сертификаты, поэтому `backend/data/certificates.json` создан
для демонстрации сценария «сертификат по запросу»: `"demo": true` у реестра и у каждой из 62 записей, поля `id, brand, family,
title, type, number, issuer, issued, valid_until, scope`, 7 семейств (кабель, автоматы, светотехника, щиты, кабеленесущие
системы, ЭУИ, клеммы), 19 брендов. Подбор — по бренду и семейству товара; `GET /api/certificates/{id}` печатается с водяным
знаком DEMO. Структура готова к замене на реальный реестр партнёра.

**Вложения** (`attachments.py`, `recognition.py`, `extractor.py`, `ocr.py`, `openai_media.py`, `transcription.py`). Принимаются
изображения, xlsx/xls, docx/doc, pptx/ppt, odt/ods/odp, txt/md/csv, PDF и аудио (mp3/wav/m4a/ogg/flac/webm/mp4…), до 15 МБ.
Извлечение текста из Office-файлов перенесено из локального `sdu-ai-services` (LiteParse с запасными парсерами DOCX/XLSX/PPTX;
для старых `.doc/.xls/.ppt` нужен LibreOffice). Из текста выделяются строки спецификации (артикул, название, количество).
Фото и сканированные страницы PDF по умолчанию распознаёт OpenAI vision (`gpt-5.6-luna`), аудио — `gpt-transcribe`;
`MEDIA_AI_PROVIDER=nitec` переключает на Chandra OCR-2 и Whisper через llm.nitec.kz. У PDF с текстовым слоем координаты слов
извлекаются локально, Chandra возвращает координаты блоков для сканов, OpenAI OCR — только текст (таблицы построчно через `|`,
сложная вёрстка требует ручной проверки); OCR сканов ограничен 15 страницами. Оригинал и распознанный текст хранятся в
`backend/data/attachments.sqlite3` по SHA-256, типу и версии обработчика/модели — повторная загрузка тех же байт не вызывает
OCR/ASR (дедупликации по хешу в `sdu-ai-services` не было, добавлена здесь). Вложения передаются агенту как текст, фото в
бэкенде `anthropic` — ещё и как изображение; доступ к `attachment_id` ограничен сессией чата (иначе HTTP 404). Загрузка ждёт
распознавания синхронно, поэтому сканы и аудио отвечают дольше обычного чата.

**Локальная лаборатория файлов.** При `ENABLE_FILE_LAB=1` и сервере на `127.0.0.1` доступны `GET /lab` (страница загрузки) и
`POST /api/lab/parse` (возвращает `filename, kind, summary, text, lines, boxes, processor, sha256`); оба маршрута отвергают
не-loopback клиентов и по умолчанию выключены — публично включать нельзя. Проверка на фикстурах `backend/tests/fixtures/`:
`spec.docx` и `spec.xlsx` показывают найденные артикулы, `spec.pdf` — текст и координаты слов, `photo.png` — OCR через выбранный
провайдер; аудио — свой `.m4a`/`.wav`. Фото, сканы и аудио расходуют кредиты API.

**Этап 0 — аудит каталога партнёра** (`scripts/ekt_catalog_audit.py`, `scripts/analyze_ekt_catalog.py`; только стандартная
библиотека Python 3: `urllib`, `sqlite3`, `concurrent.futures`). Возобновляемый обход всех страниц списка и всех detail-карточек в
локальную SQLite (`data/ekt_catalog.sqlite3`, в `.gitignore`; учётные данные из `EKT_API_USER`/`EKT_API_PASSWORD` или
интерактивно; флаги `--list-only`, `--details-only`, `--workers` 1–8, `--max-pages`, `--max-details`, `--db`, `--output`) и отчёт
о полноте, покрытии полей и стратегии поиска: 15 035 товаров, 100 % деталей, описание у 99,5 %, склады у 97 %, `offers` пусты,
402 кода свойств, `KRATNOST_MIN` у 92 %, остаток > 0 у 8 717; рекомендованный маршрут запроса — «точный артикул → FTS/fuzzy →
фильтры цены и наличия → живые остатки» — реализован в `catalog.py`. Подробности: [reports/ekt_catalog_audit.md](reports/ekt_catalog_audit.md).

## 5. Критерии кейса и как их проверить

Сценарий демо (`docs/FRONTEND.md`): открыть `widget/demo/index.html`, дальше по таблице. Без ключа LLM строки 1 и 4–5
проверяются через REST и кнопки виджета.

| # | Критерий | Как реализовано | Фраза для проверки и ожидаемый результат |
|---|---|---|---|
| 1 | Артикул → остаток, характеристики, сертификат | `get_product(article)` → живой detail: остаток по складам, свойства, демо-сертификат в карточке | «Есть ли в наличии 027228 Legrand?» → id 515291: остаток по складам, 160 А, 3P, 18 kA, ссылка на сертификат. Без ключа: `GET /api/products/search?q=027228` |
| 2 | Нулевой остаток → ≥1 аналог с обоснованием | промпт обязывает вызвать `find_analogs`; скоринг по току/полюсам/сечению/мощности/… даёт `reason` | «Есть ли LED STARK 30W MEGALIGHT (ярп4520)?» → id 45357, остаток 0 → 2–3 светильника 30 Вт в наличии с причиной |
| 3 | Условия покупки из базы знаний | `get_purchase_terms(question, topic)` → фрагменты страниц ekt.kz с URL | «Какие условия доставки в Астану?», «Как оплатить юр. лицу?», «Какая минимальная партия / кратность?» |
| 4 | Ничего в корзину без «да, добавь»; не больше остатка | `propose` → `pending_action`; `confirm` только по кнопке/явному «да»; clamp к остатку и кратности | «Добавь 2 шт» → корзина пуста, блок подтверждения → «да, добавь» → 2 шт. «Добавь 9999 шт» → «да» → добавлено столько, сколько на складе, с объяснением. «Нет» → отменено |
| 5 | Ссылка на корзину с текущим составом | ответ подтверждения содержит `PUBLIC_BASE_URL/cart/{session_id}`; `GET /cart/{sid}` рендерит корзину | открыть ссылку из ответа или cart bar в виджете; на ekt.kz — https://ekt.kz/personal/cart/ |

Дополнительно: загрузить Excel-спецификацию (скрепка) → таблица «позиция — товар — цена — остаток» и одно предложение добавить
всё найденное; переключить KZ → ответ на казахском; «позови менеджера» → контакты менеджера.

## 6. Встраивание на ekt.kz и деплой

```html
<script src="https://HOST/widget/widget.js" data-api="https://HOST" data-lang="ru"></script>
```

`data-open="1"` открывает панель сразу, `data-title` меняет заголовок; JS API — `window.EktAssistant.open() / close() / send(text)`.

- **Совместимость с сайтом (Bitrix, jQuery/Bootstrap):** один файл без сборки и зависимостей; весь UI в Shadow DOM — стили сайта
  и виджета не пересекаются. Бэкенд отдаёт CORS `*`, `session_id` живёт в `localStorage`, диалог и корзина — в `sessionStorage`
  (переживают переход между страницами); каждое сообщение несёт `page_url`, поэтому «есть ли в наличии?» на карточке товара
  понимается без артикула.
- **Дизайн** наследует токены живого сайта (`docs/DESIGN.md`, сняты 23.09.2026): шрифт PT Sans, синяя шапка `#2c7294`, жёлтые
  главные кнопки `#f4b301`, заголовки `#0b4366`, радиусы 3/5/8 px, логотип `docs/design/logo.svg`; цены в формате `62 040 ₸`
  с оговоркой «цена в магазине, на сайте действует скидка».
- **Букмарклет** `widget/bookmarklet.js`: подставить https-URL бэкенда, сохранить как закладку и нажать на любой странице
  ekt.kz — виджет появится без правки сайта, подтверждённые позиции уходят в настоящую корзину (блокируется только строгой CSP).
- **Мобильные:** при ширине ≤ 640 px панель разворачивается на весь экран (`100dvh`, `env(safe-area-inset-*)`), поле ввода
  16 px (iOS не зумит), лаунчер 56 px с бейджем корзины; проверено в мобильном эмуляторе DevTools.

**Деплой на VM Brev** (GCP Mumbai, подробности в `deploy/README.md` и `docs/VM.md`): `DUMP_DIR=/путь/к/выгрузке bash
deploy/push_to_vm.sh` синхронизирует код (и дамп) по SSH-алиасу `distinctive-orange-mammal` (`VM_HOST` для замены), ставит uv
и зависимости (`vm_setup.sh`), собирает индекс и перезапускает uvicorn на :8000 (`vm_run.sh`, лог `~/ekt-assistant/server.log`);
повторный запуск без `DUMP_DIR` только обновляет код. Публичный URL — либо Brev → Access → HTTP port 8000 (https-URL прописать
в `PUBLIC_BASE_URL`), либо `deploy/vm_caddy.sh` на VM: Caddy с автоматическим HTTPS для orau.kz / www / ai / ekt.orau.kz
(нужны A-запись на IP VM и открытые порты 80/443 в Cloud Firewall Brev).

## 7. Ограничения и следующие шаги

Ограничения прототипа: реестр сертификатов синтетический; detail API партнёра отвечает медленно и ограничен по параллельности,
при таймауте отдаётся снимок с пометкой; ответ приходит целиком (без стриминга, 2–10 с при `effort=low`); сессии, корзина и
предложения — в памяти процесса (24 ч, теряются при рестарте); на стенде используется ключ OpenAI (без ключа — режим
без диалога); индекс каталога — снимок list API (цена и остаток в ответах берутся из живого detail); аналоги и база знаний —
эвристики без эмбеддингов; интерфейс и ответы двуязычные, но тексты базы знаний и страница корзины на русском, казахский
обеспечивает модель; OCR/аудио требуют `OPENAI_API_KEY` или `NITEC_API_KEY`, старые `.doc/.xls` — LibreOffice; лаборатория
`/lab` только локальная; кэш вложений и загрузки не очищаются автоматически — не загружайте реальные персональные документы
в публичный стенд; локальная SQLite и анонимный `session_id` не заменяют авторизацию; режим родной корзины Bitrix на живом
ekt.kz не проверялся.

Следующие шаги: стриминг ответа (SSE) и потоковые карточки; авторизованные пользователи с историей и корзиной в БД
(Postgres/Redis); реальное хранилище сертификатов и паспортов, привязанное к 1С/ERP партнёра; RAG по datasheet и инструкциям
производителей; казахская база знаний и полностью казахский UI; передача диалога менеджеру (CRM/WhatsApp, уведомление с
контекстом); регулярная полная синхронизация 200 тыс. SKU и семантический поиск поверх FTS; фоновый воркер распознавания и
автоочистка вложений.

## 8. Структура репозитория и команда

```text
backend/app/     main.py (маршруты) · agent.py (tool use, шлюз подтверждения) · agent_openai.py · agent_sdk.py · cart.py ·
                 sessions.py · catalog.py (FTS5) · analogs.py · knowledge.py · certificates.py · ekt_api.py · attachments.py
                 (+ extractor/ocr/openai_media/transcription/recognition/attachment_cache) · schemas.py · config.py · templates/
backend/scripts/ dump_catalog.py (выгрузка списка) · build_index.py (SQLite FTS5) · import_details.py (снимок detail)
backend/data/    knowledge/ · certificates.json · catalog.sqlite, dump/, uploads/, attachments.sqlite3 (генерируются, в .gitignore)
backend/tests/   117 тестов: API, шлюз подтверждения, корзина, каталог, база знаний, вложения, кэш распознавания
widget/          widget.js · demo/index.html · bookmarklet.js · README.md
docs/            API_CONTRACT.md · FRONTEND.md · DESIGN.md · VM.md · design/logo.svg
deploy/          push_to_vm.sh · vm_setup.sh · vm_run.sh · vm_caddy.sh · README.md
scripts/, reports/  аудит каталога (этап 0) и отчёт ekt_catalog_audit.md
```

Команда **Data Hungry**, Hackalem AI. Репозиторий: https://github.com/BAITC-Hacks/hack-59e3f91b-data-hungry. Разделение работы:
бэкенд, агент, корзина и виджет; аудит каталога и слой вложений/OCR (этап 0, `scripts/`, `reports/`, `attachments.py`).
Состав команды и роли будут дополнены перед сдачей.
