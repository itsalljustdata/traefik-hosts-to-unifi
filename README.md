# traefik-hosts-to-unifi

Runtime usage for the published image.

This container reads Traefik HTTP Host rules and optionally syncs matching DNS records to UniFi.

## Image

- ghcr.io/itsalljustdata/traefik-hosts-to-unifi:latest

## Quick Start

1. Create `.env` from sample:

```bash
cp .env.sample .env
```

2. Set at least:

```bash
UNIFI_API_KEY=replace-with-your-unifi-api-key
```

3. Run runtime compose:

```bash
docker compose -f compose.yaml up -d
docker compose -f compose.yaml logs -f traefik-hosts-to-unifi
```

## Runtime Compose

Runtime compose file:

- compose.yaml

## Actions

`ACTION` controls behavior:

- `display`: print Traefik host table only (default)
- `sync`: display + sync DNS records into UniFi
- `remove-traefik-dns`: remove Traefik-managed DNS records from UniFi

`LOOP_SECONDS` controls repetition:

- `0`: run once and exit
- `>0`: run action repeatedly on interval

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| UNIFI_API_KEY | required | UniFi API key |
| UDM_HOST | 10.1.2.1 | UniFi controller address |
| TRAEFIK_HOST | 10.1.2.200 | Traefik API proxy host |
| TRAEFIK_PORT | 8080 | Traefik API proxy port |
| TRAEFIK_DNS | traefik.darter.au | Canonical Traefik DNS target |
| TRAEFIK_IP | 10.1.2.200 | Traefik IP used for A records |
| ACTION | display | `display`, `sync`, `remove-traefik-dns`, `markdown` |
| LOOP_SECONDS | 0 | Interval loop; 0 = run once |
| PUID | 1000 | Runtime UID for mapped user |
| PGID | 1000 | Runtime GID for mapped user |
| USER_SHELL | bash | Login shell for mapped runtime user |
| UDM_KEEP_FILE | /app/udm_keep.json | Optional keep-list JSON path |

## Repository Layout

- compose.yaml: runtime image usage
- src/: build sources (Dockerfile, script, entrypoint, build compose)
- PUBLISHING.md: GHCR publishing docs
