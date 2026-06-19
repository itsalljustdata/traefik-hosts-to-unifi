# Publishing

This repository is configured to publish images to GHCR.

## Image

- ghcr.io/itsalljustdata/traefik-hosts-to-unifi

## GitHub Actions Workflow

- .github/workflows/publish-ghcr.yml

Triggers:

- Push to `main` (publishes `latest`, branch and sha tags)
- Tag push `v*` (publishes tag/semver tags)
- Manual dispatch

## Manual Publish

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u itsalljustdata --password-stdin

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/itsalljustdata/traefik-hosts-to-unifi:latest \
  -f src/Dockerfile src \
  --push
```

`GHCR_TOKEN` must include `write:packages`.
