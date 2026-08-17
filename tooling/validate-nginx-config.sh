#!/usr/bin/env bash
#
# Validate the production and demo nginx configs with `nginx -t` in the real
# base image. CI runs this on every PR because no other job runs `nginx -t` on
# server/frontend/nginx.conf: the demo/e2e stack selects nginx.http.conf
# (MASCOPE_TLS=off) and the prod compose is built but never started, so a syntax
# or directive error in the production config would otherwise ship green and
# first surface as a crash-looping frontend on deploy.
#
# The njs setup below (load_module + the /64 rate-limit key script) mirrors
# server/frontend/Dockerfile -- keep the two in step. Needs Docker; runnable
# locally the same way CI runs it: `bash tooling/validate-nginx-config.sh`.
set -euo pipefail

image="nginx:1.27-alpine"
frontend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../server/frontend" && pwd)"

validate() {
  local conf="$1" need_certs="$2"
  echo "== nginx -t: server/frontend/${conf} =="
  # nginx resolves upstream server names at config-test time, so the "backend"
  # host in the upstream{} block must resolve or nginx -t fails; point it at
  # loopback (nothing listens -- resolution is all that is checked).
  docker run --rm \
    --add-host backend:127.0.0.1 \
    -e NEED_CERTS="${need_certs}" \
    -v "${frontend_dir}/${conf}:/etc/nginx/conf.d/nginx.conf:ro" \
    -v "${frontend_dir}/ratelimit_key.js:/etc/nginx/njs/ratelimit_key.js:ro" \
    "${image}" sh -eu -c '
      # Load njs in the main context, exactly as the Dockerfile does.
      sed -i "1i load_module /etc/nginx/modules/ngx_http_js_module.so;" /etc/nginx/nginx.conf
      # The entrypoint drops the base image default server; do the same so its
      # :80 block cannot clash with the config under test.
      rm -f /etc/nginx/conf.d/default.conf
      # nginx.conf references the ssl_* secrets; give it throwaway ones.
      if [ "${NEED_CERTS}" = yes ]; then
        apk add --no-cache openssl >/dev/null
        mkdir -p /run/secrets
        openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=ci \
          -keyout /run/secrets/ssl_secret_key \
          -out /run/secrets/ssl_certificate >/dev/null 2>&1
      fi
      nginx -t
    '
}

validate nginx.conf yes
validate nginx.http.conf no

echo "OK: both nginx configs pass nginx -t."
