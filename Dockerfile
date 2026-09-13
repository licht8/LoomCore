FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        util-linux \
        wireguard-tools \
        qrencode \
        iptables \
        nftables \
        systemd \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src ./src
COPY plugins ./plugins
# Alembic config + migrations are needed for `docker compose exec hub alembic upgrade head`
COPY alembic.ini .
COPY alembic ./alembic
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
