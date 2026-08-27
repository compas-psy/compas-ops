# Control Plane. Версии пинуются (§33).
FROM python:3.12.8-slim-bookworm AS base

# Непривилегированный пользователь: Control Plane держит канонические
# approvals, и root внутри контейнера здесь ничего не даёт, кроме риска.
RUN useradd --system --create-home --uid 10001 helm

WORKDIR /opt/helm/control-plane

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "sqlalchemy==2.0.36" \
      "alembic==1.14.0" "psycopg[binary]==3.2.3" "pydantic==2.10.4" \
      "pydantic-settings==2.7.0" "pyyaml==6.0.2"

COPY helm_core ./helm_core
COPY migrations ./migrations
COPY alembic.ini ./

USER helm
EXPOSE 8080

# Один воркер: Control Plane держит атомарные claim'ы в БД, и горизонтальное
# масштабирование здесь не нужно — оно нужно было бы, если бы он делал
# reasoning, а он его не делает (§7.1).
CMD ["uvicorn", "helm_core.app:create_app", "--factory", \
     "--host", "127.0.0.1", "--port", "8080", "--workers", "1", \
     "--no-server-header"]
