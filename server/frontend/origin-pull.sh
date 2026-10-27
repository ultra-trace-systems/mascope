#!/bin/sh
# Write the nginx include that turns authenticated origin pulls on or off (see
# the origin-pull blocks in nginx.conf for what it does).
#
#   MASCOPE_ORIGIN_PULL=off (default) -> empty include: :443 answers any client
#   MASCOPE_ORIGIN_PULL=on            -> :443 answers only a connection whose
#                                        client certificate our CA signed
#
# The CA is the compose secret origin_pull_ca, which the operator supplies:
# the certificate of the CA that signed the origin-pull certificate uploaded
# to the CDN for this zone. The CLI leaves the file empty until then, so a
# site that has not set origin pulls up still starts. With the switch on, an
# empty or missing CA refuses to start rather than serve without the check
# the operator asked for, and so does any value but on or off: a typo would
# otherwise leave the check off.
#
# docker-compose.yaml passes the switch; docker-entrypoint.sh runs this at
# container start, and tooling/validate-nginx-config.sh runs it in CI in both
# states.
set -e

origin_pull_conf=/etc/nginx/mascope/origin-pull.conf
origin_pull_ca=/run/secrets/origin_pull_ca

case "${MASCOPE_ORIGIN_PULL:-off}" in
  off)
    : > "${origin_pull_conf}"
    echo "MASCOPE_ORIGIN_PULL=off -> :443 accepts connections without a client certificate"
    ;;
  on)
    if [ ! -s "${origin_pull_ca}" ]; then
      echo "ERROR: MASCOPE_ORIGIN_PULL is on but the origin_pull_ca secret is missing or empty." >&2
      echo "       Put the CA of the CDN's origin-pull certificate in .runtime/secrets/origin_pull_ca.pem (docs/maintaining.md)." >&2
      exit 1
    fi
    # `optional`, not `on`: the loopback listener shares this server and must
    # not be asked to present a certificate. A certificate that is presented
    # and fails verification is still refused by nginx itself, with a 400.
    cat > "${origin_pull_conf}" <<EOF
ssl_client_certificate ${origin_pull_ca};
ssl_verify_client optional;
if (\$origin_pull_refused) {
  return 403;
}
EOF
    echo "MASCOPE_ORIGIN_PULL=on -> :443 accepts only the CDN's origin-pull certificate"
    ;;
  *)
    echo "ERROR: MASCOPE_ORIGIN_PULL must be 'on' or 'off', not '${MASCOPE_ORIGIN_PULL}'." >&2
    exit 1
    ;;
esac
