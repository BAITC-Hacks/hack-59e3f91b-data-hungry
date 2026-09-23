# Деплой на VM (Brev, GCP Mumbai)

1. На ноутбуке: `brew install brevdev/homebrew-brev/brev && brev login` (появится ssh-алиас `shared-azure-python`).
2. Скопировать выгрузку каталога и код, поставить зависимости, собрать индекс, запустить:
   `DUMP_DIR=/Users/kydyrali/Documents/alemhack/probe/dump bash deploy/push_to_vm.sh`
   (повторные запуски без `DUMP_DIR` только обновляют код и перезапускают сервер).
3. В консоли Brev → Access → **HTTP port** → добавить порт `8000`, сделать доступ публичным. Полученный https-URL прописать в `backend/.env` как `PUBLIC_BASE_URL=https://...` (нужен для ссылок на корзину) и перезапустить (`bash deploy/push_to_vm.sh`).
4. Виджет подключается с этого URL: `<script src="https://.../widget/widget.js" data-api="https://..."></script>`; демо: `https://.../widget/demo/index.html`.
