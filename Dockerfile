FROM python:3.8 AS builder

WORKDIR /src
COPY . .

RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir .

# Download frontend vendor JS (build-time only, no CDN needed at runtime)
RUN mkdir -p /src/embykeeperapi/static/vendor \
    && curl -fsSL -o /src/embykeeperapi/static/vendor/vue.global.prod.js \
       "https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.js" \
    && curl -fsSL -o /src/embykeeperapi/static/vendor/naive-ui.prod.js \
       "https://cdn.jsdelivr.net/npm/naive-ui@2/dist/index.prod.js" \
    && curl -fsSL -o /src/embykeeperapi/static/vendor/vue-router.global.prod.js \
       "https://cdn.jsdelivr.net/npm/vue-router@4/dist/vue-router.global.prod.js"

FROM python:3.8-slim
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src/scripts/docker-entrypoint.sh /entrypoint.sh
COPY --from=builder /src/embykeeperapi/static/vendor /opt/venv/lib/python3.8/site-packages/embykeeperapi/static/vendor

ENV TZ="Asia/Shanghai"
ENV EK_IN_DOCKER="1"

WORKDIR /app
RUN chmod +x /entrypoint.sh \
    && touch config.toml
ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
