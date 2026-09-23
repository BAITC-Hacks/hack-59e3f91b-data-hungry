# Деплой на VM (Brev, GCP Mumbai)

1. На ноутбуке используйте настроенный SSH-алиас `distinctive-orange-mammal`. Перед синхронизацией проверьте VM: `ssh distinctive-orange-mammal 'hostname; nvidia-smi'` (подробности — [docs/VM.md](../docs/VM.md)). При смене VM укажите другой алиас через `VM_HOST`.
2. Для текущей VM, где публичный HTTPS-маршрут `https://web-uct8xo9mo.gobrev.dev` ведёт на порт **8881**, из корня локального репозитория запустить:

   ```bash
   APP_PORT=8881 PUBLIC_BASE_URL=https://web-uct8xo9mo.gobrev.dev bash deploy/push_to_vm.sh
   ```

   Скрипт переносит только файлы текущего коммита, локальную базу аудита `data/ekt_catalog.sqlite3` (если есть) и `backend/.env` с правами `600`; существующие файлы VM не удаляет. `vm_setup.sh` ставит зависимости, строит индекс и импортирует детали как датированный резервный снимок. `vm_run.sh` запускает бэкенд в одном процессе, отключает `/lab` по умолчанию и хранит PID в `server.pid`. Jupyter на 8888 не трогать.
3. Проверить `https://web-uct8xo9mo.gobrev.dev/api/health` и `https://web-uct8xo9mo.gobrev.dev/widget/demo/index.html` из внешнего браузера без входа в NVIDIA. Для локальной проверки VM: `ssh distinctive-orange-mammal 'curl -fsS http://127.0.0.1:8881/api/health'`.
4. Виджет: `<script src="https://web-uct8xo9mo.gobrev.dev/widget/widget.js" data-api="https://web-uct8xo9mo.gobrev.dev"></script>`; [демо](https://web-uct8xo9mo.gobrev.dev/widget/demo/index.html). Старый `jupyter-uct8xo9mo.gobrev.dev` с NVIDIA Authorization не использовать для ссылки жюри.

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
