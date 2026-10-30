FROM python:3.12-slim AS backend-base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY apps ./apps
COPY data ./data
COPY migrations ./migrations
COPY packages ./packages
COPY evaluation/results ./evaluation/results
COPY alembic.ini ./

FROM node:24-alpine AS web-build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM backend-base AS runtime
COPY --from=web-build /web/dist ./apps/web/dist
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

