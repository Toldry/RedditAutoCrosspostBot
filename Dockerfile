# Lightweight Python 3.11 image for RedditAutoCrosspostBot
FROM python:3.11-slim

# Set environment variables
# UV_PROJECT_ENVIRONMENT keeps the virtualenv outside /app, so `COPY . .` cannot overwrite it
# with a developer's local .venv. Putting it first on PATH makes `python` the venv's interpreter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Install system runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv itself (keep the version in sync with CI in .github/workflows/tests.yml)
RUN pip install --no-cache-dir uv==0.11.32

# Install the locked runtime dependencies first, so this layer is cached until uv.lock changes
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

# Copy application source and install the racb package itself (editable, from /app/src)
COPY . .
RUN uv sync --locked --no-dev --no-cache

# Compile the gettext catalogs (.po -> .mo), which are not committed. Babel runs in a throwaway
# environment (--isolated), so it never lands in /opt/venv
RUN uv run --locked --isolated --only-group i18n --no-cache \
    pybabel compile --statistics -d src/racb/locales

# Run bot module entrypoint
CMD ["python", "-m", "racb"]
