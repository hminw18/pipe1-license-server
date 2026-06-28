FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md setup.py ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[server]"

RUN addgroup --system pipe1 \
    && adduser --system --ingroup pipe1 pipe1

USER pipe1

EXPOSE 8000

CMD ["uvicorn", "pipe1_license_server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
