FROM python:3.11-slim AS builder

WORKDIR /src
COPY . .

RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir .

FROM python:3.11-slim
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src/scripts/docker-entrypoint.sh /entrypoint.sh

ENV TZ="Asia/Shanghai"
ENV EK_IN_DOCKER="1"
ENV EK_MODE="api"

WORKDIR /app
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app \
    && touch config.toml
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 1818

ENTRYPOINT ["/entrypoint.sh"]