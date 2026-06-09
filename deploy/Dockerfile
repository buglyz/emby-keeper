ARG EK_IMAGE=ghcr.io/buglyz/emby-keeper/api:main
FROM ${EK_IMAGE}

RUN useradd -m -u 1000 embykeeper \
    && chown embykeeper:embykeeper /app -R

ENV HOME="/app"
