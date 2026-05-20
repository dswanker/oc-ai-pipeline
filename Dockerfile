# Microsoft's official Playwright Python image. Comes with Chromium pre-installed
# along with every system library Chromium needs (libnss3, libasound2, etc.).
# The "jammy" suffix = Ubuntu 22.04 base.
#
# We pin a specific version because the playwright-python wheel must match
# the Chromium revision baked into the image. Bumping this image version
# requires bumping the playwright pin in requirements.txt to match.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

# Accept GITHUB_TOKEN as a build argument from Railway
ARG GITHUB_TOKEN

# Configure git to use the token for GitHub URLs (needed for private repos in requirements.txt)
RUN if [ -n "\$GITHUB_TOKEN" ]; then \
      git config --global url."https://\$GITHUB_TOKEN@github.com/".insteadOf "https://github.com/"; \
    fi

# Install Python dependencies first so Docker can cache this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Railway sets PORT at runtime; uvicorn reads it via the shell.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port \${PORT:-8000}"]
