#!/usr/bin/env bash
#
# Validate the production and demo nginx configs with `nginx -t` in the real
# base image. CI runs this on every PR because no other job runs `nginx -t` on
# server/frontend/nginx.conf: the demo/e2e stack selects nginx.http.conf
# (MASCOPE_TLS=off) and the prod compose is built but never started, so a syntax
# or directive error in the production config would otherwise ship green and
# first surface as a crash-looping frontend on deploy.
#
# The production config is tested twice, with authenticated origin pulls off
# and on (MASCOPE_ORIGIN_PULL, server/frontend/origin-pull.sh), and each time
# nginx is also started and asked, over TLS, what it answers: with them on,
# :443 must refuse a client without our CA's certificate and admit one with it,
# and the loopback listener :8443 must admit anyone. No other job starts this
# config, so a refusal that silently stopped refusing would otherwise ship.
#
# The njs setup below (load_module + the /64 rate-limit key script) mirrors
# server/frontend/Dockerfile -- keep the two in step. Needs Docker; runnable
# locally the same way CI runs it: `bash tooling/validate-nginx-config.sh`.
set -euo pipefail

image="nginx:1.27-alpine"
frontend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../server/frontend" && pwd)"

# Two functions prepended to the container script in validate() below, which
# runs as one `sh -c` string: origin_pull checks the refusals of
# origin-pull.sh, makes the certificates and writes the include the entrypoint
# would; exercise starts nginx and asks it, over TLS, what it answers.
read -r -d '' container_functions <<'SH' || true
origin_pull() {
  # The refusals first: on with no CA, and a value that is neither on nor off.
  if MASCOPE_ORIGIN_PULL=on sh /origin-pull.sh >/dev/null 2>&1; then
    echo "origin-pull.sh started with origin pulls on and no CA" >&2
    exit 1
  fi
  if MASCOPE_ORIGIN_PULL=yes sh /origin-pull.sh >/dev/null 2>&1; then
    echo "origin-pull.sh accepted MASCOPE_ORIGIN_PULL=yes" >&2
    exit 1
  fi
  # Our CA, the certificate it signs for the CDN, and a stranger's.
  mkdir -p /tmp/op && cd /tmp/op
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=origin-pull-ca \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign \
    -keyout ca.key -out /run/secrets/origin_pull_ca >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -subj /CN=cdn -keyout cdn.key -out cdn.csr >/dev/null 2>&1
  openssl x509 -req -in cdn.csr -CA /run/secrets/origin_pull_ca -CAkey ca.key \
    -CAcreateserial -days 1 -out cdn.pem >/dev/null 2>&1
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=stranger \
    -keyout stranger.key -out stranger.pem >/dev/null 2>&1
  MASCOPE_ORIGIN_PULL="${ORIGIN_PULL}" sh /origin-pull.sh
}

exercise() {
  apk add --no-cache curl >/dev/null
  # Through the real entrypoint, as the image starts, so its call to
  # origin-pull.sh is under test too: the include written above goes first,
  # and an entrypoint that no longer writes it leaves nginx unable to start.
  rm -f /etc/nginx/mascope/origin-pull.conf /etc/nginx/conf.d/nginx.conf
  cp "/src/${CONF}" /etc/nginx/mascope/nginx.conf
  MASCOPE_ORIGIN_PULL="${ORIGIN_PULL}" sh /mascope-entrypoint.sh &
  tries=0
  until [ "$(curl -sk -o /dev/null -w "%{http_code}" https://127.0.0.1:8443/)" != 000 ]; do
    tries=$((tries + 1))
    if [ "${tries}" -ge 50 ]; then
      echo "origin pulls ${ORIGIN_PULL}: nginx did not start through the entrypoint" >&2
      exit 1
    fi
    sleep 0.2
  done
  # The frontend is not baked in here, so an admitted request for / is a 404;
  # a refused one is 403 (ours) or 400 (nginx, on a certificate that fails).
  expect() {
    want="$1"; shift
    got="$(curl -sk -o /dev/null -w "%{http_code}" "$@")"
    if [ "${got}" != "${want}" ]; then
      echo "origin pulls ${ORIGIN_PULL}: curl $* answered ${got}, not ${want}" >&2
      exit 1
    fi
  }
  if [ "${ORIGIN_PULL}" = on ]; then
    expect 403 https://127.0.0.1:443/
    expect 404 --cert /tmp/op/cdn.pem --key /tmp/op/cdn.key https://127.0.0.1:443/
    expect 400 --cert /tmp/op/stranger.pem --key /tmp/op/stranger.key https://127.0.0.1:443/
  else
    expect 404 https://127.0.0.1:443/
  fi
  expect 404 https://127.0.0.1:8443/
  nginx -s quit
  wait
  echo "origin pulls ${ORIGIN_PULL}: :443 and :8443 answer as expected"
}
SH

validate() {
  local conf="$1" need_certs="$2" origin_pull="${3:-off}"
  echo "== nginx -t: server/frontend/${conf} (origin pulls ${origin_pull}) =="
  # nginx resolves upstream server names at config-test time, so the "backend"
  # host in the upstream{} block must resolve or nginx -t fails; point it at
  # loopback (nothing listens -- resolution is all that is checked).
  docker run --rm \
    --add-host backend:127.0.0.1 \
    -e NEED_CERTS="${need_certs}" \
    -e ORIGIN_PULL="${origin_pull}" \
    -v "${frontend_dir}/${conf}:/src/${conf}:ro" \
    -v "${frontend_dir}/ratelimit_key.js:/etc/nginx/njs/ratelimit_key.js:ro" \
    -v "${frontend_dir}/realip-gateway.sh:/realip-gateway.sh:ro" \
    -v "${frontend_dir}/origin-pull.sh:/origin-pull.sh:ro" \
    -v "${frontend_dir}/docker-entrypoint.sh:/mascope-entrypoint.sh:ro" \
    -e CONF="${conf}" \
    "${image}" sh -eu -c "${container_functions}"'
      cp "/src/${CONF}" /etc/nginx/conf.d/nginx.conf
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
        # nginx.conf includes the gateway trust line the entrypoint writes at
        # start. Run the real detection: `docker run` puts this container on
        # the default bridge, so there is always a gateway to find, and an
        # empty include here means the detection broke - which at start-up
        # would fall back silently to one shared rate-limit bucket.
        mkdir -p /etc/nginx/mascope
        sh /realip-gateway.sh
        grep -qx "set_real_ip_from [0-9.]*;" /etc/nginx/mascope/realip-gateway.conf || {
          echo "the gateway detection wrote no trust line" >&2
          exit 1
        }
        origin_pull
      fi
      nginx -t
      if [ "${NEED_CERTS}" = yes ]; then
        exercise
      fi
    '
}

validate nginx.conf yes off
validate nginx.conf yes on
validate nginx.http.conf no

echo "OK: both nginx configs pass nginx -t, and origin pulls answer as they should, off and on."
