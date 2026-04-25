# --- Build stage ---
FROM python:3.13-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Runtime stage ---
FROM python:3.13-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p output logs

EXPOSE 8000 8901 8902 8903 8904

# Default: start all servers + REPL via the launcher
ENTRYPOINT ["python", "main.py"]
