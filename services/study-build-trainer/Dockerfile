# ─── Stage 1: Builder ────────────────────────────────────────────
# Install all dependencies including build tools needed to compile
# native extensions (cryptography, lxml, pillow, pdfplumber, etc.)
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build dependencies — needed to compile native Python packages.
# Stripped from the final runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        git \
        libffi-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a dedicated venv so we can copy it cleanly
# to the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install requirements. The --extra-index-url for CPU-only torch is
# already in requirements.txt.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Strip unnecessary files from the venv to slim it down further.
# These artifacts are not needed at runtime.
RUN find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type d -name "test" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type f -name "*.pyc" -delete 2>/dev/null || true && \
    find /opt/venv -type f -name "*.pyo" -delete 2>/dev/null || true


# ─── Stage 2: Runtime ────────────────────────────────────────────
# Slim runtime image — only what's needed to actually run the service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/data/huggingface

# Runtime libraries only — no build tools. Needed for libxml2, libssl
# at runtime since lxml and cryptography link to them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy the pre-built venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY ../../skills /skills
COPY . /app

# Create the HuggingFace cache directory on the volume mount path so
# downloaded models persist across container restarts.
RUN mkdir -p /data/huggingface

EXPOSE 8080

# Use the same start command railway.toml expects.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
