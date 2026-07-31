FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY jobs ./jobs

# Cloud Run injects PORT. Two workers is ample at 10-50 requests/day; threads
# cover the APNs/Wallet HTTP calls, which are IO-bound.
ENV PORT=8080
CMD exec gunicorn --bind :$PORT --workers 2 --threads 8 --timeout 120 app.main:app
