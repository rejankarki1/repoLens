# Production image for the API + its RQ worker (Render). Local development does not use this file.
# Runs `python -m apps.api.supervisor`: one uvicorn process and one worker, no database or Redis inside.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 app
WORKDIR /srv

# Runtime dependencies only (requirements-dev.txt is not installed).
COPY apps/api/requirements.txt apps/api/requirements.txt
RUN pip install -r apps/api/requirements.txt

COPY alembic.ini ./
COPY database database
COPY apps/api apps/api

USER app
# The parser never downloads grammars while it runs, so bake them into the image (fails the build if any is missing).
RUN python -c "from apps.api.services.code_parser import ensure_grammars, missing_grammars; ensure_grammars(); left = missing_grammars(); assert not left, left"

ENV ENVIRONMENT=production PORT=10000
EXPOSE 10000
CMD ["python", "-m", "apps.api.supervisor"]
