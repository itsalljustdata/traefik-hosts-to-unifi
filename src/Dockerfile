FROM python:3.12-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime

ARG PUID=1000
ARG PGID=1000

WORKDIR /app

ENV PUID=${PUID} \
    PGID=${PGID} \
    ACTION=display \
    LOOP_SECONDS=3600 \
    TRAEFIK_PORT=8080 \
    LOG_LEVEL=ERROR \
    TRAEFIK_PORT=8080 \
    TRAEFIK_PATH=/api/http/routers

COPY --from=builder /usr/sbin/gosu /usr/sbin/gosu
COPY --from=builder /install /usr/local
COPY traefik_hosts.py entrypoint.sh /app/

RUN chmod +x /app/traefik_hosts.py /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
