# Build context: repository root (needs frontend/ and web/).
FROM caddy:2-alpine

ARG APP_VERSION=dev
ARG GIT_SHA=local

COPY web/Caddyfile /etc/caddy/Caddyfile
COPY frontend/ /srv/

# Fail the build on a broken Caddyfile (both for local and a real domain).
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
    && DOMAIN=finance.example.com caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

LABEL org.opencontainers.image.title="finance-tracker-web" \
      org.opencontainers.image.description="Multi-currency expense tracker — Caddy + static frontend" \
      org.opencontainers.image.source="https://github.com/z1ec/VAV-finance-tracker" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

EXPOSE 80 443 8443
