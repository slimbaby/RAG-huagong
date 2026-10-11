# ---- base：运行时镜像（生产依赖）----
FROM python:3.10-slim AS base

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

# Install runtime dependencies with uv (production: 不含 dev/test 依赖)
RUN uv sync --no-dev --no-cache

# Copy application
COPY . .

# Remove stale bytecode cache to ensure volume-mounted code is used
RUN find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

ENV PYTHONDONTWRITEBYTECODE=1

# 生产/默认启动：不带 --reload（热重载由 docker-compose 的 command 覆盖提供）
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ---- dev：额外安装 pytest 等测试依赖 ----
# 此前容器 venv 用 --no-dev 从不装 pytest，测试无法在容器内自动运行。
# dev 继承 base（与运行镜像共享全部系统依赖，环境一致），只增量装 dev extra。
# 用法：docker build --target dev -t rag-backend-dev backend/
#       docker run --rm rag-backend-dev            # 默认跑 pytest
FROM base AS dev

# --extra dev 安装 [project.optional-dependencies].dev（pytest/pytest-asyncio/pytest-cov）
RUN uv sync --extra dev --no-cache

CMD ["/app/.venv/bin/pytest", "tests/", "-q"]


# ---- runtime：最后的 stage = docker build 默认目标，保持生产语义不变 ----
FROM base AS runtime
