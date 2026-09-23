# Деплой на VM (Brev, GCP Mumbai)

1. На ноутбуке используйте настроенный SSH-алиас `distinctive-orange-mammal`. Перед синхронизацией проверьте VM: `ssh distinctive-orange-mammal 'hostname; nvidia-smi'` (подробности — [docs/VM.md](../docs/VM.md)). При смене VM укажите другой алиас через `VM_HOST`.
2. Для текущей VM, где внешний HTTPS-маршрут `https://jupyter-uct8xo9mo.gobrev.dev` ведёт на порт **8881**, из корня локального репозитория запустить:

   ```bash
   APP_PORT=8881 PUBLIC_BASE_URL=https://jupyter-uct8xo9mo.gobrev.dev bash deploy/push_to_vm.sh
   ```

   Скрипт переносит только файлы текущего коммита, локальную базу аудита `data/ekt_catalog.sqlite3` (если есть) и `backend/.env` с правами `600`; существующие файлы VM не удаляет. `vm_setup.sh` ставит зависимости, строит индекс и импортирует детали как датированный резервный снимок. `vm_run.sh` запускает бэкенд в одном процессе, отключает `/lab` по умолчанию и хранит PID в `server.pid`. Jupyter на 8888 не трогать.
3. Проверить `https://jupyter-uct8xo9mo.gobrev.dev/api/health` и `https://jupyter-uct8xo9mo.gobrev.dev/widget/demo/index.html`. Без входа в NVIDIA внешний адрес перенаправляет на страницу авторизации; для локальной проверки VM используйте `ssh distinctive-orange-mammal 'curl -fsS http://127.0.0.1:8881/api/health'`.
4. Виджет подключается с этого URL: `<script src="https://.../widget/widget.js" data-api="https://..."></script>`; демо: `https://.../widget/demo/index.html`. Если доступ к Brev ограничен авторизацией, судье понадобится разрешённая учётная запись или другой публичный маршрут.
