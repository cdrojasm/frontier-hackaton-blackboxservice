FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir "greenlet>=3.0" . \
    && python -c "import greenlet; print('greenlet installed:', greenlet.__version__)"

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn frontier_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

