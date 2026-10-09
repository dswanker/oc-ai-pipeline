FROM python:3.11-slim-bookworm

# Install system dependencies and Playwright browsers
# libreoffice-writer converts Word protocols (.docx/.doc) to PDF so they take the same path as PDF protocols,
# tables (Schedule of Activities) included; without it the text fallback was used and tables were lost.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    libreoffice-writer-nogui \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Accept GH_TOKEN as a build argument from Railway
ARG GH_TOKEN

# Configure git to use GH_TOKEN for private repos
RUN if [ -n "$GH_TOKEN" ]; then \
      git config --global url."https://$GH_TOKEN@github.com/".insteadOf "https://github.com/"; \
    fi

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Run the application - Railway sets PORT env var
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
