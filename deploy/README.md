# Деплой на VM (Brev, GCP Mumbai)

Текущая VM без GPU — `34.93.3.248`, сайт `https://ekt-assistant.orau.kz`.
Настройка SGR/OCR/ASR через OpenAI и эмбеддингов/реранкера через NITEC:
[VM_NITEC.md](VM_NITEC.md). Ниже сохранены инструкции для резервной NVIDIA VM.

## Локальные embedding и rerank модели на H200

`deploy/vm_models.sh` поднимает два Docker-контейнера vLLM с автозапуском после перезагрузки VM: `intfloat/multilingual-e5-large-instruct` на `127.0.0.1:8891` и `Qwen/Qwen3-Reranker-8B` на `127.0.0.1:8892`. Порты доступны только самой VM. Публичный endpoint виджета и Jupyter не меняются. Модели кэшируются в `~/ekt-model-cache` на диске VM.

Для оригинальной Qwen3 скрипт использует [шаблон пары Query/Document из vLLM v0.22.1](https://github.com/vllm-project/vllm/blob/v0.22.1/examples/pooling/score/template/qwen3_reranker.jinja). Без него endpoint отвечает, но возвращает неверный порядок товаров.

```bash
ssh distinctive-orange-mammal 'bash ~/ekt-assistant/deploy/vm_models.sh'
ssh distinctive-orange-mammal 'curl -fsS http://127.0.0.1:8891/v1/models && curl -fsS http://127.0.0.1:8892/v1/models'
```

В `~/ekt-assistant/backend/.env` задайте без секретов:

```dotenv
EKT_EMBEDDING_BASE_URL=http://127.0.0.1:8891/v1
EKT_RERANK_BASE_URL=http://127.0.0.1:8892/v1
```

Приложению нужны `backend/data/catalog.sqlite` и `backend/data/semantic_catalog.sqlite` на VM. Последний файл не хранится в Git; его нужно перенести отдельно. Проверяйте одинаковую модель и совместимость эмбеддингов после замены сервера; при необходимости пересоберите коллекцию локальным сервером. Если отдельный контейнер не отвечает, поиск автоматически возвращается к гибридному или лексическому порядку. Для диагностики: `docker logs --tail 100 ekt-embed-e5` и `docker logs --tail 100 ekt-rerank-qwen`.

Для запуска бэкенда на ноутбуке через те же сервисы держите SSH-туннель `ssh -N -L 8891:127.0.0.1:8891 -L 8892:127.0.0.1:8892 distinctive-orange-mammal` и задайте такие же два URL в локальном `backend/.env`. Ключ NITEC для локальных loopback-серверов не нужен.

1. На ноутбуке используйте настроенный SSH-алиас `distinctive-orange-mammal`. Перед синхронизацией проверьте VM: `ssh distinctive-orange-mammal 'hostname; nvidia-smi'` (подробности — [docs/VM.md](../docs/VM.md)). При смене VM укажите другой алиас через `VM_HOST`.
2. Для текущей VM, где публичный HTTPS-маршрут `https://web-uct8xo9mo.gobrev.dev` ведёт на порт **8881**, из корня локального репозитория запустить:

   ```bash
   APP_PORT=8881 PUBLIC_BASE_URL=https://web-uct8xo9mo.gobrev.dev bash deploy/push_to_vm.sh
   ```

   Скрипт переносит только файлы текущего коммита, локальную базу аудита `data/ekt_catalog.sqlite3` (если есть) и `backend/.env` с правами `600`; существующие файлы VM не удаляет. `vm_setup.sh` ставит зависимости, строит индекс и импортирует детали как датированный резервный снимок. `vm_run.sh` запускает бэкенд в одном процессе, отключает `/lab` по умолчанию и хранит PID в `server.pid`. Jupyter на 8888 не трогать.
3. Проверить `https://web-uct8xo9mo.gobrev.dev/api/health` и `https://web-uct8xo9mo.gobrev.dev/widget/demo/index.html?v=20260923-no-demo-certificates` из внешнего браузера без входа в NVIDIA. Для локальной проверки VM: `ssh distinctive-orange-mammal 'curl -fsS http://127.0.0.1:8881/api/health'`.
4. Виджет: `<script src="https://web-uct8xo9mo.gobrev.dev/widget/widget.js?v=20260923-no-demo-certificates" data-api="https://web-uct8xo9mo.gobrev.dev"></script>`; [демо](https://web-uct8xo9mo.gobrev.dev/widget/demo/index.html?v=20260923-no-demo-certificates). Версия в URL обходит кэш Cloudflare после удаления синтетических сертификатов. Старый `jupyter-uct8xo9mo.gobrev.dev` с NVIDIA Authorization не использовать для ссылки жюри.

## Переход на ekt.orau.kz

Не менять `PUBLIC_BASE_URL` до успешной внешней проверки HTTPS. Публичный адрес Brev выше остаётся резервным.

1. В DNS зоны `orau.kz` создать запись `A` для `ekt` на **66.201.7.234** — текущий публичный IP VM (проверить заново перед изменением: `ssh distinctive-orange-mammal 'curl -fsS https://ifconfig.me/ip'`). IP может измениться после перезапуска VM. Простая CNAME-запись на `web-uct8xo9mo.gobrev.dev` не подходит: прокси Brev вернул 403 при запросе с `Host: ekt.orau.kz`.
2. В настройках сетевого доступа VM открыть входящие TCP-порты **80 и 443**. Порт 8881 для прямого доступа открывать не нужно. Дождаться, пока `dig +short ekt.orau.kz A` извне вернёт IP VM.
3. Установить и запустить HTTPS-прокси только для `ekt.orau.kz`:

   ```bash
   ssh distinctive-orange-mammal 'APP_PORT=8881 EXPECTED_IP=66.201.7.234 bash /home/ubuntu/ekt-assistant/deploy/vm_caddy.sh'
   ```

   Скрипт не меняет конфигурацию `orau.kz`, `www.orau.kz` и `ai.orau.kz`. Он проверяет локальный бэкенд и DNS перед установкой Caddy.
4. Проверить **извне VM**: `curl -I https://ekt.orau.kz/` (перенаправление на демо), `curl -fsS https://ekt.orau.kz/api/health`, загрузку демо и сценарий корзины в браузере. Только затем переключить ссылки, возвращаемые бэкендом:

   ```bash
   APP_PORT=8881 PUBLIC_BASE_URL=https://ekt.orau.kz bash deploy/push_to_vm.sh
   ```

Если DNS или HTTPS не работают, не переключать `PUBLIC_BASE_URL`: используйте адрес `web-uct8xo9mo.gobrev.dev` для демонстрации.
