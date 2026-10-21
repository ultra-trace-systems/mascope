#!/bin/sh
# Write the nginx include that trusts the Docker bridge gateway as a relay for
# CF-Connecting-IP (see the cloudflare-realip block in nginx.conf for why).
#
# docker-entrypoint.sh runs this at container start, and
# tooling/validate-nginx-config.sh runs it in CI and fails the build if it
# writes no trust line. The fallback below is silent at start-up by design, so
# CI is the only place a broken detection (a base image without `ip`, say)
# would show.
#
# The gateway is the container's IPv4 default route. Anything that is not a
# plain dotted quad leaves the include empty, which keeps nginx valid and the
# trust list at Cloudflare's ranges alone.
set -e

realip_conf=/etc/nginx/mascope/realip-gateway.conf
gateway="$(ip -4 route show default 2>/dev/null | awk '{ print $3; exit }')"
case "${gateway}" in
  '' | *[!0-9.]*)
    : > "${realip_conf}"
    echo "No Docker gateway found -> trusting CF-Connecting-IP from Cloudflare's ranges only"
    ;;
  *)
    printf 'set_real_ip_from %s;\n' "${gateway}" > "${realip_conf}"
    echo "Trusting CF-Connecting-IP relayed through the Docker gateway ${gateway}"
    ;;
esac
