FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLUS_STORAGE_DIR=/data/storage

# PDF 导出：LibreOffice + 思源同款开源字体（Noto CJK 全字重）+ 模板字体名映射
RUN apt-get update  && apt-get install -y --no-install-recommends libreoffice-impress python3-uno fonts-noto-cjk fonts-noto-cjk-extra fontconfig  && rm -rf /var/lib/apt/lists/*
COPY docker/fonts.conf /etc/fonts/conf.d/60-plus-template-fonts.conf
RUN fc-cache -f

WORKDIR /srv

# 先装依赖（利用镜像层缓存）
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install -e . && rm -rf app

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY templates ./templates

RUN useradd --uid 1000 --create-home plus && mkdir -p /data/storage && chown -R plus /data
USER plus

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# 启动前自动执行数据库迁移
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
