// Per-client key for nginx's rate/connection limits.
//
// Returns the full address for IPv4, but only the /64 network for IPv6. A
// single IPv6 client is routinely assigned a whole /64, so keying on the full
// /128 lets it evade every per-IP limit by rotating through its own addresses,
// while IPv4 clients (no spare addresses) bear the full restriction. Masking
// to /64 keys all of a client's addresses together.
//
// Wired via `js_set $limit_key ...` on $remote_addr, which nginx has already
// restored to the real client address (real_ip runs before the limits apply).
// Doing this correctly needs a real IPv6 parser: nginx prints IPv6 compressed
// ("::"), so a map/regex on the text cannot reliably find the /64.

function limitKey(r) {
    var addr = r.variables.remote_addr;
    if (!addr) {
        return '';
    }
    // IPv4, or an IPv4-mapped/embedded form (contains a dot): key on the whole
    // address - there is no /64 to mask, and collapsing these would over-group.
    if (addr.indexOf(':') < 0 || addr.indexOf('.') >= 0) {
        return addr;
    }
    return ipv6Net64(addr);
}

// Expand an IPv6 address to its eight hextets and return the first four (the
// /64 network) as a normalized, lowercase, no-leading-zero key.
function ipv6Net64(addr) {
    var zone = addr.indexOf('%');
    if (zone >= 0) {
        addr = addr.substring(0, zone); // drop a scope id (fe80::1%eth0)
    }

    var parts = addr.split('::');
    var head = parts[0] ? parts[0].split(':') : [];
    var groups;
    if (parts.length > 1) {
        // "::" stands for the run of all-zero groups that fills out to eight.
        var tail = parts[1] ? parts[1].split(':') : [];
        var missing = 8 - (head.length + tail.length);
        if (missing < 0) {
            missing = 0;
        }
        groups = head;
        for (var i = 0; i < missing; i++) {
            groups.push('0');
        }
        groups = groups.concat(tail);
    } else {
        groups = head;
    }

    var net = [];
    for (var j = 0; j < 4; j++) {
        var v = parseInt(groups[j] || '0', 16);
        net.push(isNaN(v) ? '0' : v.toString(16));
    }
    return net.join(':') + '::/64';
}

export default { limitKey };
