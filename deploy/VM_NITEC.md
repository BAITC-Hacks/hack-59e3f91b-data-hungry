# VM без GPU: приложение + NITEC + OpenAI

Адрес: `ubuntu@34.93.3.248`, SSH-ключ `~/.ssh/id_ed25519`.
Публичный сайт: https://ekt-assistant.orau.kz . Caddy принимает HTTPS на 443
и проксирует запросы на приложение `127.0.0.1:8000`.

**Проверка 23.09.2026:** SGR, OCR и распознавание речи через OpenAI работают.
NITEC настроен, но TCP-соединение к `llm.nitec.kz:443` с этой VM и резервной
Brev VM завершается тайм-аутом; с ноутбука тот же API доступен. DNS одинаковый,
исходящие соединения VM разрешены, OpenAI доступен. Причина на стороне
маршрута или фильтрации пока не установлена. До восстановления доступа поиск
автоматически использует локальный FTS5. Работа эмбеддингов и реранкера на
этой VM **пока не подтверждена**, хотя обе модели и коллекция проверены с Mac.

## Провайдеры

| Задача | Провайдер | Модель |
| --- | --- | --- |
| SGR-агент | OpenAI | `gpt-4.1` |
| OCR изображений и страниц PDF | OpenAI | `gpt-5.6-luna` |
| Распознавание речи | OpenAI | `gpt-transcribe` |
| Эмбеддинги | NITEC | `intfloat/multilingual-e5-large-instruct`, 1024 измерения |
| Реранкер | NITEC | `Qwen/Qwen3-Reranker-8B` |

На VM модели не загружаются. Локально работают приложение, каталог SQLite,
FTS5 и векторная коллекция из 15 035 товаров. Один реранк применяется к первым
30 результатам после объединения векторного поиска и FTS5.

## Активный релиз и настройки

`/home/ubuntu/ekt-current` указывает на активный каталог в
`/home/ubuntu/ekt-releases/`. Systemd-сервис `ekt-assistant` запускает Python
из виртуального окружения этого релиза. Новый релиз сначала проверяется на
локальном порту 8001, затем переключается сервис на 8000.
Шаблон сервиса: [ekt-assistant-nitec.service](ekt-assistant-nitec.service).

Секреты хранятся только в `backend/.env` активного релиза с правами `600`:
`OPENAI_API_KEY`, `NITEC_API_KEY`, `EKT_API_USER`, `EKT_API_PASS`.
Не копируйте их в команды диагностики, Git или логи.

Основные настройки без секретов:

```dotenv
LLM_PROVIDER=sgr
SGR_CHAT_MODEL=gpt-4.1
OPENAI_API_BASE_URL=https://api.openai.com/v1
OPENAI_OCR_MODEL=gpt-5.6-luna
OPENAI_ASR_MODEL=gpt-transcribe
PUBLIC_BASE_URL=https://ekt-assistant.orau.kz
NITEC_API_BASE_URL=https://llm.nitec.kz/v1
EKT_EMBEDDING_BASE_URL=https://llm.nitec.kz/v1
EKT_RERANK_BASE_URL=https://llm.nitec.kz/v1
NITEC_RERANK_MODEL=Qwen/Qwen3-Reranker-8B
EKT_RERANK_ENABLED=1
ENABLE_FILE_LAB=0
```

`EKT_DB_PATH` и `EKT_SEMANTIC_DB_PATH` указывают на базы внутри активного
релиза. Файлы вложений и база их метаданных сохранены в прежнем
`/home/ubuntu/ekt-assistant/backend/data/`; пути явно заданы через
`EKT_UPLOAD_DIR` и `EKT_ATTACHMENT_DB_PATH`.

Снимок релиза включает проверенные текущие файлы рабочей копии, в том числе
переход OCR/ASR только на OpenAI. Он не является чистым архивом одного коммита.
`deployment-manifest.json` в релизе фиксирует базовый коммит и SHA-256 файлов.

## Проверки и перезапуск

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@34.93.3.248
systemctl status ekt-assistant caddy --no-pager
curl -fsS http://127.0.0.1:8000/api/health
cd /home/ubuntu/ekt-current/backend
.venv/bin/python scripts/hybrid_search.py search 'автомат 16А' --rerank --limit 3
sudo journalctl -u ekt-assistant -n 50 --no-pager
sudo systemctl restart ekt-assistant
```

Проверяйте не только HTTP-ответ поиска: при сбое NITEC приложение сохраняет
лексический/гибридный резервный поиск. Прямая CLI-проверка выше должна вернуть
`rerank_score` и выполниться без исключений. `/api/health` должен сообщать
`provider: sgr`, `llm_configured: true`, `products: 15035`.

При замене эмбеддера размерность сама по себе не гарантирует совместимость:
проверяйте векторы одинаковых текстов либо перестраивайте коллекцию. При
текущем переходе обратно на NITEC пять проверенных векторов совпали с
коллекцией (cosine ≈ 1). На прежних 23 контрольных запросах NITEC дал
Hit@1 82,6%, Hit@5 100%, MRR@10 0,913; это небольшой ранее использованный
набор, а не независимая оценка.

## Резервная копия до переключения

Исходное приложение остаётся в `/home/ubuntu/ekt-assistant`.
Перед развёртыванием сохранены исходники, `.env`, каталожная база, метаданные
вложений и конфигурации systemd/Caddy в
`/home/ubuntu/ekt-backups/20260923T121213Z` (каталог доступен только владельцу).
Для возврата к исходной версии восстановите оттуда `ekt-assistant.service`
в `/etc/systemd/system/`, выполните `sudo systemctl daemon-reload`, затем
`sudo systemctl restart ekt-assistant`. Caddy при переключении не менялся.
