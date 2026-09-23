FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
RUN pip install . && useradd --create-home warehouse
COPY alembic.ini ./
COPY alembic ./alembic
USER warehouse
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
RUN pip install '.[dev]'
COPY tests ./tests
COPY docs ./docs
USER warehouse
CMD ["pytest", "-p", "no:cacheprovider"]
