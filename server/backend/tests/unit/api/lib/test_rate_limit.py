"""Tests: the rate-limit key masks IPv6 to its /64 network (IPv4 kept whole).

A single IPv6 client is routinely assigned a whole /64, so keying the per-IP
limit on the full /128 would let it evade the budget by rotating through its
own addresses. The access log keeps the full address; only the limit key is
masked.
"""

from mascope_backend.api.lib.rate_limit import _limit_key_ip


def test_ipv4_is_unchanged():
    assert _limit_key_ip("203.0.113.9") == "203.0.113.9"


def test_ipv6_is_masked_to_the_64_network():
    assert _limit_key_ip("2001:db8:aa:bb:1:2:3:4") == "2001:db8:aa:bb::/64"
    assert _limit_key_ip("2001:db8:aa:bb::1") == "2001:db8:aa:bb::/64"


def test_every_address_in_one_64_yields_the_same_key():
    # Every host in a /64 must map to one key - including the compression
    # case a text regex would miss (2001:db8::5 vs 2001:db8:0:0::9).
    assert _limit_key_ip("2001:db8:aa:bb::1") == _limit_key_ip(
        "2001:db8:aa:bb:ffff:ffff:ffff:ffff"
    )
    assert _limit_key_ip("2001:db8::5") == _limit_key_ip("2001:db8:0:0::9")


def test_distinct_64s_get_distinct_keys():
    assert _limit_key_ip("2001:db8:aa:bb::1") != _limit_key_ip("2001:db8:aa:cc::1")


def test_ipv4_mapped_and_non_addresses_are_unchanged():
    assert _limit_key_ip("::ffff:203.0.113.9") == "::ffff:203.0.113.9"
    assert _limit_key_ip("unknown") == "unknown"
    assert _limit_key_ip("") == ""


def test_scoped_link_local_address_is_masked_not_raised():
    # request.client.host can surface a zone id for a link-local peer. The
    # scope is stripped and the address masked to its /64; the ip_network call
    # is inside the guard, so a mask failure keys on the raw value rather than
    # raising past it.
    assert _limit_key_ip("fe80::1%eth0") == "fe80::/64"
    assert _limit_key_ip("fe80::1%eth0") == _limit_key_ip("fe80::1")
