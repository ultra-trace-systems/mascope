#!/bin/sh
# Select the nginx config at container start based on MASCOPE_TLS.
#
#   MASCOPE_TLS=on  (default) -> HTTPS on :443 (needs ssl_* secrets)  [nginx.conf]
#   MASCOPE_TLS=off           -> HTTP on :80 for localhost            [nginx.http.conf]
#
# Defaulting to HTTPS keeps existing prod deployments unchanged.
set -e

# Drop the base image's default server so it cannot clash on :80.
rm -f /etc/nginx/conf.d/default.conf

# Publish the runtime config the app should read, overwriting the placeholder
# baked into the bundle (server/frontend/public/runtime-config.js).
#
# The frontend used to know the `[meta]` config only as a JSON blob compiled in
# at image build time. That is the wrong moment: a deployment whose config
# layers are its own (`mascope init`, no source checkout) runs registry images
# built against the repo's layers, so the bundle's copy and the backend's copy
# could be different revisions - a UI offering features the backend refuses.
# Writing it here means both halves read the value this stack was started with,
# and flipping a flag is a restart rather than an image rebuild.
#
# Left alone when MASCOPE_RUNTIME is unset, so a stack that does not pass it
# (the demo compose) keeps the baked value and behaves exactly as before.
if [ -n "${MASCOPE_RUNTIME:-}" ]; then
  # printf %s, not echo: the value is JSON and must land verbatim, with no
  # backslash interpretation and no trailing newline inside the assignment.
  printf 'window.__MASCOPE_RUNTIME__ = %s;\n' "${MASCOPE_RUNTIME}" \
    > /app/frontend/runtime-config.js
  echo "Published /runtime-config.js from MASCOPE_RUNTIME"
else
  echo "MASCOPE_RUNTIME unset -> serving the runtime config baked into the bundle"
fi

if [ "${MASCOPE_TLS:-on}" = "off" ]; then
  echo "MASCOPE_TLS=off -> serving over HTTP (localhost only)"
  cp /etc/nginx/mascope/nginx.http.conf /etc/nginx/conf.d/nginx.conf
else
  if [ ! -s /run/secrets/ssl_certificate ] || [ ! -s /run/secrets/ssl_secret_key ]; then
    echo "ERROR: MASCOPE_TLS is on but the SSL certificate/key secret is missing or empty." >&2
    echo "       Generate one with 'mascope cert gen', or set MASCOPE_TLS=off for a localhost HTTP trial." >&2
    exit 1
  fi
  cp /etc/nginx/mascope/nginx.conf /etc/nginx/conf.d/nginx.conf
fi

exec nginx -g 'daemon off;'
