# Деплой на VM (Brev, GCP Mumbai)

1. На ноутбуке используйте настроенный SSH-алиас `distinctive-orange-mammal`. Перед синхронизацией проверьте VM: `ssh distinctive-orange-mammal 'hostname; nvidia-smi'` (подробности — [docs/VM.md](../docs/VM.md)). При смене VM укажите другой алиас через `VM_HOST`.
2. Скопировать выгрузку каталога и код, поставить зависимости, собрать индекс, запустить:
   `DUMP_DIR=/путь/к/выгрузке bash deploy/push_to_vm.sh`
   (повторные запуски без `DUMP_DIR` только обновляют код и перезапускают сервер).
3. В консоли Brev → Access → **HTTP port** → добавить порт `8000`, сделать доступ публичным. Полученный https-URL прописать в `backend/.env` как `PUBLIC_BASE_URL=https://...` (нужен для ссылок на корзину) и перезапустить (`bash deploy/push_to_vm.sh`).
4. Виджет подключается с этого URL: `<script src="https://.../widget/widget.js" data-api="https://..."></script>`; демо: `https://.../widget/demo/index.html`.

## Прямой SSH без Brev

VM доступна напрямую по публичному IP `34.93.3.248` (порт 22 открыт в Cloud Firewall Ports). Вход только по ключам: в `/etc/ssh/sshd_config.d/70-keys-only.conf` отключены парольный и интерактивный вход (`AuthenticationMethods publickey`). Чтобы дать доступ новому человеку, его публичный ключ добавляется в `~/.ssh/authorized_keys` пользователя `ubuntu`.

```
# ~/.ssh/config
Host ekt-vm
  HostName 34.93.3.248
  User ubuntu
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

Деплой напрямую: `VM_HOST=ekt-vm bash deploy/push_to_vm.sh`. Домен `ekt-assistant.orau.kz` указывает на этот же IP; HTTPS терминирует Caddy (`deploy/vm_caddy.sh`).
