# syntax=docker/dockerfile:1

# ---- builder: install into an isolated venv (no build tools in final image) ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Optional extras, e.g. EXTRAS="[ldap,passkey]" to include those backends.
ARG EXTRAS=""
WORKDIR /src
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir ".${EXTRAS}"

# ---- runtime: minimal, non-root, no shell login ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root account. APP_GID can be set to the host 'freeswitch'/'www-data' gid at
# build time so bind-mounted recordings are writable under least privilege.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd -g "${APP_GID}" app \
 && useradd -u "${APP_UID}" -g app -M -s /usr/sbin/nologin -d /app app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
# Writable state dir (SQLite local-auth DB) owned by the app user only.
RUN mkdir -p /app/data && chown -R app:app /app/data && chmod 700 /app/data

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)"]

# Bind to all interfaces *inside the container*; publish only to 127.0.0.1 on the
# host (see compose) and terminate TLS at the reverse proxy.
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
