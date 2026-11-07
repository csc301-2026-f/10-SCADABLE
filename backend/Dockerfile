# Build with the repository root as context:
#   docker build -f backend/Dockerfile -t live-swot-api .
# Do NOT build with context backend/: sibling scraper and llm packages are required.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # Crawl4AI cache must be writable by appuser; the scraper's default
    # (/app/scraper/.uv-cache) is root-owned, which silently forces mock crawls.
    CRAWL4_AI_BASE_DIRECTORY=/home/appuser/.crawl4ai \
    PORT=8000

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
COPY pyproject.toml README.md ./
COPY scraper ./scraper
COPY llm ./llm
COPY backend/app ./backend/app

RUN pip install -r backend/requirements.txt ./scraper ./llm . \
    && python -m playwright install --with-deps chromium

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/backend/scraper/output/backend \
    && chown -R appuser:appuser /app/backend/scraper/output

USER appuser
WORKDIR /app/backend

EXPOSE 8000

CMD ["sh", "-c", "python -m app.db.init_db && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
