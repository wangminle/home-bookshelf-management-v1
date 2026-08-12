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
# BUG-158/CHK-048：在镜像构建阶段生成 dist/skills/ 产物（ZIP + SHA256SUMS + manifest.json）。
# dist/ 在 .gitignore 中，不能直接 COPY；build_skills_bundle.py 从 skills/ 确定性构建，
# 确保 skill_catalog.py 的 BUNDLE_DIR (/app/dist/skills/) 有可供下载的 ZIP 文件。
COPY scripts/build_skills_bundle.py ./scripts/
RUN python scripts/build_skills_bundle.py --output dist/skills
COPY backend/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/bookshelf.db \
    PORT=8000

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
