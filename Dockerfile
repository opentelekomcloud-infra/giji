FROM registry.access.redhat.com/ubi9/python-312:latest

LABEL maintainer="OpenTelekomCloud Infrastructure Team"
LABEL description="GitHub to Jira issue importing tool"
LABEL version="0.1.0"

WORKDIR /app

# Copy requirements first for better layer caching
COPY --chown=1001:0 requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY --chown=1001:0 src/ ./src/
COPY --chown=1001:0 config/ ./config/

# Install the package in development mode
RUN pip install --no-cache-dir -e .

USER 1001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import giji; print('OK')" || exit 1

# Use specific entry point instead of find command
ENTRYPOINT ["python", "-m", "giji.cli.main"]
CMD ["--help"]
