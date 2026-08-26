FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml uv.lock ./

# Install dependencies with uv
RUN uv sync --no-dev --no-cache

# Copy application
COPY . .

# Remove stale bytecode cache to ensure volume-mounted code is used
RUN find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

ENV PYTHONDONTWRITEBYTECODE=1

# 开发热重载由 docker-compose 的 command 覆盖提供，镜像默认不带 --reload
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
