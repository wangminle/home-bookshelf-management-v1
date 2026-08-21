# ---- 前端（GitHub #1：原生 Docker 部署也要带 UI）----
FROM node:20-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_BASE=/
ENV VITE_BASE=$VITE_BASE
RUN npm run build

# ---- API + 托管 static ----
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/alembic.ini ./
COPY backend/alembic/ ./alembic/
COPY backend/app/ ./app/
# WBS-4/CHK-039：Skills 目录必须包含在镜像中，否则 Agent 分发能力不可用
COPY skills/ ./skills/
COPY scripts/build_skills_bundle.py ./scripts/
# GitHub #1：拷入前端产物，SPA fallback 才能工作
COPY --from=frontend /frontend/dist ./static
# GitHub #5：bundle 写到 static/skills，避开 lwa 生成的 **/dist ignore；官方镜像同样走此路径
RUN python scripts/build_skills_bundle.py --output static/skills
COPY backend/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/bookshelf.db \
    PORT=8000

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
