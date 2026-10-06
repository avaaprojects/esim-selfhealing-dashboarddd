# The dashboard as one container: the frontend is built, then served by the
# backend, so there is a single port and no separate dev server.
#
#   docker build -t esim-dashboard .
#   docker run -p 8000:8000 -v esim-data:/app/backend/data -v esim-uploads:/app/uploads esim-dashboard
#
# The two volumes keep the SQLite stores (accounts, reports, input selections)
# and operator uploads across restarts. Without them the container starts empty
# every time, which is fine for a demo and wrong for anything else.

# ---- build the frontend -----------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- run ---------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN adduser --system --group --no-create-home app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY esim_selfhealing/ ./esim_selfhealing/
COPY data/ ./data/
COPY tools/ ./tools/
COPY tests/ ./tests/
COPY README.md README_DASHBOARD.md CHANGES.md ./
COPY --from=frontend /build/dist ./frontend/dist

# Writable at runtime: SQLite stores and operator uploads.
RUN mkdir -p backend/data uploads/screenshots uploads/csv uploads/logs \
 && chown -R app:app backend/data uploads
USER app

# Hosts (Render, Cloud Run, Fly, ...) assign the port through $PORT; 8000 is the
# local default. Shell form so the variable is expanded at run time.
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/api/health\", timeout=2).status==200 else 1)"

CMD ["sh", "-c", "python -m uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
