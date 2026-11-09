FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8787

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# SlideGuard's LangChain 0.3 / Python <3.13 requirements stay isolated from OSA.
COPY requirements-slideguard.txt .
RUN python -m venv /opt/slideguard-venv \
    && /opt/slideguard-venv/bin/pip install --no-cache-dir -r requirements-slideguard.txt
ENV SLIDEGUARD_PYTHON=/opt/slideguard-venv/bin/python

COPY backend ./backend
COPY config ./config
COPY rules-data ./rules-data

EXPOSE 8787

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8787"]
