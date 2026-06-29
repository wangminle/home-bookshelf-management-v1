FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini alembic/ app/ ./
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/bookshelf.db \
    PORT=8000

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
