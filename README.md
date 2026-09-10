# LoomHub

**AI-Driven Infrastructure Hub** — оркестратор серверной инфраструктуры с AI-ассистентом.

## Стек

- Python / FastAPI
- PostgreSQL (Docker)
- MCP (Model Context Protocol) для плагинов
- Hetzner Inference API (Qwen3.6-35B)

## Self-hosted запуск на Linux VDS

```bash
cp .env.vds.example .env
chmod 600 .env
# Заполнить HETZNER_API_KEY и POSTGRES_PASSWORD
nano .env
docker compose up -d --build
```

После запуска UI доступен по `http://IP_СЕРВЕРА/`. Backend и PostgreSQL наружу
не публикуются, а WebSocket проксируется через Nginx.

LoomHub автоматически загружает плагины из `plugins/<name>/main.py`. Плагин
выполняется на том же Linux-сервере в host-enabled контейнере и может
использовать предоставленные host-интеграции. `loomwg` пока содержит mock
реализацию инструментов WireGuard.

Подробная инструкция: [Деплой на VDS](docs/setup/DEPLOY.md).

## Документация

- [Правила проекта](RULES.md)
- [Деплой на VDS](docs/setup/DEPLOY.md)
