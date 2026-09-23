# ИИ-ассистент для ekt.kz — команда Data Hungry (Hackalem AI, кейс №1 ТОО «Электрокомплект»)

Чат-виджет на сайте ekt.kz, который отвечает покупателю как живой менеджер: находит товар по артикулу или описанию, показывает
цену и остаток по складам из API ekt.kz, характеристики и сертификат, подбирает аналог с обоснованием, если позиции нет на складе,
отвечает на вопросы об оплате, доставке и минимальной партии из базы знаний сайта, разбирает присланную спецификацию (Excel/Word/PDF)
или фото маркировки — и кладёт товар в корзину **только после явного подтверждения** («да, добавь» или кнопка «Подтвердить»),
после чего даёт прямую ссылку на корзину. Языки: русский и казахский.

![Виджет на карточке товара ekt.kz](docs/screenshots/widget.png)
*(скриншот: `widget/demo/index.html` — карточка 027228 Legrand, ответ с остатками по складам и блок подтверждения)*

## 1. Архитектура

```text
 браузер покупателя (ekt.kz или demo-страница)
 ┌──────────────────────────────────────────────┐
 │ widget/widget.js  (1 файл, Shadow DOM, RU/KZ) │
 └───────────────┬──────────────────────────────┘
                 │ POST /api/chat · /api/chat/confirm · /api/upload · GET /cart/{sid}
                 ▼
 ┌──────────────────────────── backend/app (FastAPI, Python 3.11+) ────────────────────────────┐
 │ main.py → agent.py: шлюз подтверждения (детерминированный, без LLM) → цикл tool use          │
 │                                                                                             │
 │   catalog.py ──── data/catalog.sqlite (SQLite FTS5: слова + триграммы, 15 035 SKU из списка)│
 │   ekt_api.py ──── https://ekt.kz/api/products/detail?id= (живые остатки по складам, свойства)│
 │   analogs.py ──── RECOMMEND + FTS + категория → скоринг по ключевым параметрам               │
 │   knowledge.py ── data/knowledge/*.md|txt|json (страницы ekt.kz: доставка, оплата, возврат…) │
 │   certificates.py data/certificates.json (ДЕМО-реестр, API сертификатов не отдаёт)          │
 │   attachments.py  xlsx/docx/pdf/фото → строки спецификации; OCR/ASR через llm.nitec.kz (опц.)│
 │   cart.py ─────── корзина сессии: propose → confirm (clamp к остатку и кратности)            │
 └───────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                         │ anthropic SDK: messages + 7 strict tools, effort=low
                                         ▼
                                 Claude (LLM_MODEL, по умолчанию claude-opus-5)
```

**Инструменты модели** (`agent.py`, все со `strict` JSON-схемами): `search_products` (FTS по каталогу с фильтром бренд/категория),
`get_product` (карточка по id или артикулу: живой остаток по складам, цена, характеристики, кратность, сертификаты),
`find_analogs` (аналоги в наличии с полем `reason`), `get_purchase_terms` (база знаний по темам delivery/payment/returns/
minimum_order/contacts/company/howto/faq/certificates), `propose_add_to_cart` (только предложение), `get_cart`, `escalate_to_manager`
(контакты менеджера). Цены и остатки модель берёт только из результатов инструментов — это зашито в системный промпт.

**Поток «добавить в корзину» с подтверждением:**
1. Пользователь пишет «добавь 2 шт» (или жмёт «Добавить в корзину» на карточке) → модель вызывает `propose_add_to_cart` →
   `cart_store.propose()` запрашивает живой detail, считает `max_qty` и кратность и сохраняет `pending_action` (TTL 10 мин).
   Корзина **не изменена**; виджет рисует жёлтый блок с количеством и кнопками «Подтвердить / Отмена».
2. Подтверждение: кнопка → `POST /api/chat/confirm {action_id, confirm:true}`, либо короткое сообщение («да, добавь»,
   «подтверждаю», «ok» — до 8 слов; слово отказа побеждает; число допускается только равное предложенному).
   `classify_confirmation()` разбирает его без LLM.
3. `cart_store.confirm()` заново запрашивает остаток (без кэша), ограничивает количество остатком минус уже лежащее в корзине,
   округляет вниз до `KRATNOST_MIN`, пропускает позиции без остатка и объясняет каждое изменение в ответе вместе со ссылкой на корзину.
4. «Нет» / «Отмена» → предложение удаляется, корзина не тронута. Устаревший `action_id` → HTTP 409.

**Почему безопасность корзины на сервере.** У модели нет инструмента, меняющего корзину, — только `propose`. Шаг 2–3 выполняет
детерминированный код: даже галлюцинация или подмена промпта не добавит товар, не превысит остаток и не обойдёт кратность.
Платёжные данные не запрашиваются и не хранятся; оплата — на сайте или через менеджера.

## 2. Запуск прототипа

Нужны Python 3.11+ и [uv](https://docs.astral.sh/uv/). Всё выполняется из `backend/`.

```bash
cd backend
uv sync --extra dev                                   # зависимости (+pytest)
cp .env.example .env                                  # впишите ANTHROPIC_API_KEY; LLM_MODEL/LLM_EFFORT — по желанию
uv run python scripts/dump_catalog.py --pages 1       # быстрый старт: 1 страница = 5000 SKU (~1,5 мин); без --pages — весь список
uv run python scripts/build_index.py                  # data/catalog.sqlite из data/dump/ (секунды)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Дальше: http://localhost:8000/widget/demo/index.html (имитация карточки товара с виджетом, панель открыта сразу) — или откройте
файл `widget/demo/index.html` напрямую, он ходит на `http://localhost:8000`. Проверка: http://localhost:8000/api/health →
`{ok, products, model, llm_configured}`; OpenAPI — http://localhost:8000/docs. Тесты: `uv run pytest -q` (69 тестов, помеченные
`network` ходят в живой API ekt.kz).

`scripts/dump_catalog.py` — возобновляемая выгрузка `GET /api/products` в `data/dump/list_NNNN.json` (`--pages N`, `--per-page`,
`--workers`, `--out`); останавливается на короткой странице или когда API зацикливается на первую страницу.
`build_index.py --dump DIR` принимает и другой каталог с дампом.

**Без ключа Anthropic** работает всё, кроме свободного диалога: поиск и карточки (`/api/products/search`, `/api/products/{id}`),
загрузка файлов, кнопка «Добавить в корзину» → предложение → «Подтвердить»/«да, добавь» с ограничением по остатку, страница корзины.
`/api/chat` в этом режиме отвечает, что ключ не задан.

| переменная | по умолчанию | смысл |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | ключ Anthropic (профиль `ant auth login` тоже подхватывается) |
| `LLM_MODEL` / `LLM_EFFORT` | `claude-opus-5` / `low` | модель и `output_config.effort` (чат должен отвечать за секунды) |
| `LLM_FALLBACKS` | `1` | серверный fallback при отказе классификатора безопасности (beta); `0` — выключить |
| `NITEC_API_KEY` | пусто | OCR фото/сканов и транскрибация аудио через llm.nitec.kz; без ключа фото идут в Claude vision |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | база для ссылок на корзину и сертификаты |
| `DETAIL_CACHE_TTL` | `300` | кэш detail-ответов ekt.kz, с; подтверждение всегда читает остаток заново |

Деплой на VM: `deploy/README.md` (`deploy/push_to_vm.sh` синхронизирует код и дамп, собирает индекс, перезапускает сервер).

## 3. Данные

**Тестовый API партнёра** (Basic Auth, учётные данные в `.env.example`):
- `GET /api/products?page=N&per_page=M` → `{page, per_page, count, items[{id, name, article, price, image, url, url_api_detail, offers}]}`.
  Поиска в API нет, поэтому список выгружается в SQLite FTS5 (`build_index.py`: слова + триграммы, бренд из названия, категории из URL).
  По кейсу в каталоге >200 тыс. SKU; тестовый список на 23.09.2026 отдаёт 15 035 позиций (после 4-й страницы по 5000 идёт по кругу).
- `GET /api/products/detail?id=ID` (~2–3 с) → `{id, name, article, description, price, quantity, stores[{id, name, quantity}], image,
  url, offers, properties{CML2-коды: NOMINALNYY_TOK, KOLICHESTVO_POLYUSOV, TORGOVAYA_MARKA, KRATNOST_MIN, RECOMMEND=[ids], …}}`.
  Запрашивается в момент ответа (кэш 5 мин) и заново при подтверждении. Продаваемый остаток = сумма по реальным складам;
  служебные склады («Брак», «Маркетинг», «перемещение», «Образцы», «Витрина», «Восстановленный продукт») исключены (`ekt_api.py`).
  Свойства переводятся в человекочитаемые подписи (`catalog.humanize_properties`).

**База знаний** `backend/data/knowledge/` — тексты со страниц ekt.kz, лексический поиск с синонимами и казахскими ключами (без эмбеддингов):
`delivery.md` ← https://ekt.kz/checkout-delivery/ · `payment.md` ← https://ekt.kz/payments/ и https://ekt.kz/checkout-delivery/ ·
`returns.txt` ← https://ekt.kz/return/ · `contacts.txt` ← https://ekt.kz/about/contacts/ · `howto.txt` ← https://ekt.kz/about/howto/ ·
`company.md` ← https://ekt.kz/about/ · `faq.json` ← https://ekt.kz/about/faq/. Ответ возвращает фрагменты с URL источника.

**Сертификаты — ДЕМО-реестр.** API ekt.kz не отдаёт сертификаты, поэтому `backend/data/certificates.json` синтетический
(`"demo": true` у реестра и у каждой из 62 записей; поля `id, brand, family, title, type, number, issuer, issued, valid_until, scope`;
семейства: кабель, автоматы, светотехника, щиты, кабеленесущие системы, ЭУИ, клеммы). Подбор — по бренду и семейству товара.
Страница `GET /api/certificates/{id}` печатается с водяным знаком DEMO. Структура готова к замене на реальный реестр партнёра.

Этап подготовки данных (аудит полноты и полей каталога): `scripts/ekt_catalog_audit.py`, отчёт `reports/ekt_catalog_audit.md`.

## 4. Критерии кейса и как их проверить

| # | Критерий | Как реализовано | Фраза для проверки |
|---|---|---|---|
| 1 | Артикул → остаток, характеристики, сертификат | `get_product(article)` → живой detail: остаток по складам, свойства, демо-сертификат в карточке | «Есть ли в наличии 027228 Legrand?» (id 515291: остаток по 7 складам, 160 А, 3P, 18 kA, сертификат). Без ключа: `GET /api/products/search?q=027228` |
| 2 | Нулевой остаток → ≥1 аналог с обоснованием | промпт обязывает вызвать `find_analogs`; скоринг по току/полюсам/сечению/мощности/… даёт `reason` | «Есть ли LED STARK 30W MEGALIGHT (ярп4520)?» (id 45357, остаток 0 → 2–3 светильника 30 Вт в наличии с причиной) |
| 3 | Условия покупки из базы знаний | `get_purchase_terms(question, topic)` → фрагменты страниц ekt.kz | «Какие условия доставки в Астану?», «Как оплатить юр. лицу?», «Какая минимальная партия / кратность?» |
| 4 | Ничего в корзину без «да, добавь»; после — не больше остатка | `propose` → `pending_action`; `confirm` только по кнопке/явному «да»; clamp к остатку и кратности | «Добавь 2 шт» → корзина пуста, блок подтверждения → «да, добавь» → 2 шт. «Добавь 9999 шт» → «да» → добавлено 23 (остаток), объяснение. «Нет» → отменено |
| 5 | Ссылка на корзину с текущим составом | ответ подтверждения содержит `PUBLIC_BASE_URL/cart/{session_id}`; `GET /cart/{sid}` рендерит корзину | открыть ссылку из ответа или cart bar в виджете; на ekt.kz — https://ekt.kz/personal/cart/ |

Дополнительно: загрузить Excel-спецификацию (📎) → таблица «позиция — товар — цена — остаток» и одно предложение добавить всё найденное;
переключить KZ → ответ на казахском; «позови менеджера» → контакты (+7 (727) 346-88-88, WhatsApp, almaty@ekt.kz).

## 5. Встраивание на ekt.kz и мобильные устройства

```html
<script src="https://HOST/widget/widget.js" data-api="https://HOST" data-lang="ru"></script>
```
`data-open="1"` открывает панель сразу, `data-title` меняет заголовок; JS API — `window.EktAssistant.open() / close() / send(text)`.

- **Совместимость с сайтом (Bitrix, jQuery/Bootstrap):** один файл без сборки и зависимостей; весь UI в Shadow DOM — стили сайта
  и виджета не пересекаются. Бэкенд отдаёт CORS `*`, `session_id` живёт в `localStorage`, диалог и корзина — в `sessionStorage`
  (переживают переход между страницами); каждое сообщение несёт `page_url`, поэтому «есть ли в наличии?» на карточке товара понимается без артикула.
- **Родная корзина:** если `location.hostname` оканчивается на `ekt.kz`, после подтверждения виджет дополнительно шлёт
  `POST /local/templates/template/ajax/basket.php` (`action=add2basket`, тот же запрос, что кнопка «В корзину» на сайте) по каждой позиции
  и показывает ссылку https://ekt.kz/personal/cart/ вместо страницы прототипа.
- **Букмарклет** `widget/bookmarklet.js`: подставить https-URL бэкенда, сохранить как закладку и нажать на любой странице ekt.kz — виджет
  появится без правки сайта (блокируется только строгой CSP).
- **Мобильные:** при ширине ≤ 640 px панель разворачивается на весь экран (`100dvh`, `env(safe-area-inset-*)`), поле ввода 16 px
  (iOS не зумит), лаунчер 56 px с бейджем корзины; проверено в мобильном эмуляторе DevTools.

## 6. Ограничения и следующие шаги

Ограничения прототипа: ответ приходит целиком (без стриминга, 2–10 с при `effort=low`); сессии, корзина и предложения — в памяти
процесса (24 ч, теряются при рестарте); реестр сертификатов синтетический; индекс каталога — снимок списка API (цена и остаток в ответах
берутся из живого detail); подбор аналогов и поиск по базе знаний — эвристики без эмбеддингов; интерфейс и ответы двуязычные, но тексты
базы знаний и страница корзины на русском; OCR/аудио требуют `NITEC_API_KEY`, старые `.doc/.xls` — LibreOffice; режим родной
корзины Bitrix на живом ekt.kz не проверялся.

Следующие шаги: стриминг ответа (SSE) и потоковые карточки; авторизованные пользователи с историей диалогов и корзиной в БД
(Postgres/Redis); реальное хранилище сертификатов и паспортов, привязанное к 1С/ERP партнёра; RAG по datasheet и инструкциям
производителей (эмбеддинги, PDF); казахская база знаний и полностью казахский UI; передача диалога менеджеру (CRM/WhatsApp,
уведомление с контекстом); регулярная полная синхронизация 200 тыс. SKU и семантический поиск поверх FTS.

## Структура репозитория

```text
backend/app/   main.py (маршруты) · agent.py (tool use, шлюз подтверждения) · cart.py · sessions.py · catalog.py (FTS5) ·
               analogs.py · knowledge.py · certificates.py · ekt_api.py · attachments.py (+ extractor/ocr/transcription/
               recognition/attachment_cache) · schemas.py · config.py · templates/cart.html
backend/scripts/ dump_catalog.py (выгрузка списка) · build_index.py (SQLite FTS5)
backend/data/  knowledge/ · certificates.json · catalog.sqlite, dump/, uploads/ (генерируются, в .gitignore)
backend/tests/ 69 тестов: API, корзина, каталог, база знаний, вложения
widget/        widget.js · demo/index.html · bookmarklet.js · README.md
docs/          API_CONTRACT.md · FRONTEND.md · DESIGN.md · VM.md · design/
deploy/        скрипты запуска на VM;  scripts/, reports/ — аудит каталога (этап 0)
```
