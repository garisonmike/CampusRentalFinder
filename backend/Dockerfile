# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# base -- shared runtime layer
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq5 only -- psycopg[binary] ships its own libpq, but keeping the client
# runtime available makes `pg_isready`/`psql` debugging possible. No compiler
# in the final image: gcc and -dev headers are build-time only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. Root in a container is root on the host kernel
# for anything that escapes the namespace.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

# ---------------------------------------------------------------------------
# dev -- source is bind-mounted by docker-compose; includes the test stack
# ---------------------------------------------------------------------------
FROM base AS dev

COPY requirements/ /app/requirements/
RUN pip install --upgrade pip && pip install -r requirements/dev.txt

COPY --chown=app:app . /app/

RUN mkdir -p /app/media /app/staticfiles && chown -R app:app /app
USER app

EXPOSE 8000

# No migrations here: docker-compose owns that step so a restarting container
# cannot race another instance into a half-applied migration.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---------------------------------------------------------------------------
# prod -- gunicorn, no dev tooling, no source mount
# ---------------------------------------------------------------------------
FROM base AS prod

ENV DJANGO_SETTINGS_MODULE=config.settings.prod

COPY requirements/ /app/requirements/
RUN pip install --upgrade pip && pip install -r requirements/prod.txt

COPY --chown=app:app . /app/

# collectstatic must not need a real secret or database. A throwaway key is
# supplied for this one build-time command and never reaches the image.
RUN SECRET_KEY="build-time-only-not-a-runtime-secret" \
    ALLOWED_HOSTS="localhost" \
    CORS_ALLOWED_ORIGINS="http://localhost" \
    python manage.py collectstatic --noinput

RUN mkdir -p /app/media && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live/').status==200 else 1)"

# Migrations are a deploy step, not a container start step.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
