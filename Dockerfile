FROM python:3.11-slim

# Install system dependencies and Playwright browsers
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Accept GITHUB_TOKEN as a build argument from Railway
ARG GITHUB_TOKEN

# Configure git to use GITHUB_TOKEN for private repos
RUN if [ -n "$GITHUB_TOKEN" ]; then \
      git config --global url."https://$GITHUB_TOKEN@github.com/".insteadOf "https://github.com/"; \
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

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
