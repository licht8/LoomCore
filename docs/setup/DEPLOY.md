# Деплой LoomHub на VDS

## Требования

- Linux-сервер с Docker Engine и Docker Compose Plugin
- DNS-запись домена на IP сервера
- Открытые порты `80` и `443`
- Не менее 1 GB RAM; для сборки frontend желательно добавить 1-2 GB swap

## Первый запуск

```bash
git clone git@github.com:USER/REPOSITORY.git
cd LoomCore
cp .env.vds.example .env
chmod 600 .env
nano .env
docker compose up -d --build
```

Для установки в стандартный каталог можно использовать:

```bash
sudo mkdir -p /opt/loomhub
sudo git clone git@github.com:USER/REPOSITORY.git /opt/loomhub
cd /opt/loomhub
sudo bash deploy/install.sh
```

Проверка:

```bash
docker compose ps
curl http://127.0.0.1/health
docker compose logs -f hub
```

Сайт будет доступен на `http://SERVER_IP/`. WebSocket идет через тот же домен по
пути `/ws/{session_id}`, поэтому браузеру не нужен прямой доступ к порту `8000`.

## Управление сервером через плагины

Backend автоматически запускает каждый плагин, у которого есть файл
`plugins/<name>/main.py`. Инструменты всех таких плагинов передаются в LLM и
маршрутизируются обратно владельцу инструмента.

Self-hosted compose намеренно дает контейнеру `hub` доступ к host-интеграциям:
host PID namespace, `/etc/wireguard`, `/sys`, `/lib/modules`, Docker socket и
корень хоста в `/host`. Это нужно для плагинов управления инфраструктурой.
Добавляйте только доверенные плагины и оставляйте опасные операции за approval.

Read-only-инструменты и создание клиентов `loomwg` выполняются внутри
этого host-enabled контейнера. `wg_create_client` сохраняет client config и
защищённый QR-файл в `/etc/wireguard/clients` с правами `600`; private key не
возвращается в чат и не сохраняется в истории сообщений. Для QR-генерации в
образ включён `qrencode`.
Для создания клиента достаточно передать имя: адрес выбирается автоматически
из свободных адресов сети интерфейса, а публичный IP сервер определяется
автоматически. Если нужен домен или внешний IP нельзя определить из контейнера,
задайте в `.env` `LOOMWG_ENDPOINT` (например, `vpn.example.com`) или передайте
endpoint в отдельном вызове инструмента.

## Обновление

```bash
git pull
docker compose up -d --build
docker image prune -f
```

## HTTPS

Текущий Nginx-конфиг поднимает HTTP и корректно проксирует WebSocket. Для
production нужно добавить TLS-сертификат (например, Certbot) и перевести
серверный блок на `listen 443 ssl`, а HTTP перенаправить на HTTPS. После этого
frontend автоматически использует `wss://`.

## Резервное копирование

Данные PostgreSQL находятся в Docker volume `pgdata`:

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
```

Файл `.env` и `backup.sql` не должны попадать в Git.
