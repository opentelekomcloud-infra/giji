FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /app

COPY --chown=1001:0 . .

USER 1001

RUN pip install --no-cache-dir -r requirements.txt

CMD find . -name "*.py" -exec python {} \; && sleep 60
