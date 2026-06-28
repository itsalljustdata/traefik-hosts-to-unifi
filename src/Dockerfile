FROM python:3-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu procps curl wget net-tools \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /toolroot /toolroot/lib /toolroot/lib64 /toolroot/usr; \
    bins="/usr/sbin/gosu /usr/bin/curl /usr/bin/wget /usr/bin/ps /usr/bin/netstat"; \
    for b in $bins; do \
      cp --parents "$b" /toolroot; \
            ldd "$b" | awk '/=> \// {print $3} /^\// {print $1}' | xargs -r -I '{}' cp --parents '{}' /toolroot; \
    done; \
    cp --parents /lib64/ld-linux-x86-64.so.2 /toolroot 2>/dev/null || true

WORKDIR /src
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/toolroot/usr/local -r requirements.txt

FROM python:3-slim AS runtime

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

COPY --from=builder /toolroot/usr/ /usr/
COPY --from=builder /toolroot/lib/ /lib/
COPY --from=builder /toolroot/lib64/ /lib64/
COPY traefik_hosts.py entrypoint.sh /app/

RUN chmod +x /app/traefik_hosts.py /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
