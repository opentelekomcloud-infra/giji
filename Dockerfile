FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /app

COPY --chown=1001:0 requirements.txt .
COPY --chown=1001:0 config/ ./config/
COPY --chown=1001:0 scripts/ ./scripts/

USER 1001

RUN pip install --no-cache-dir -r requirements.txt
