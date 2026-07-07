#!/bin/sh
# fly-route-guard.sh — keep a split-tunnel /48 route to Fly's 6PN pinned to the
# active WireGuard interface, so traffic to hindsight-mem is never swallowed by a
# corporate VPN's default route (GlobalProtect / Cisco AnyConnect) or lost when
# WireGuard reconnects under a new utunN.
#
# Why this exists: both the corporate VPN and the Fly WireGuard tunnel install a
# `default` (::/0) IPv6 route. When they tie, the VPN wins and Fly-bound packets
# are blackholed. A more-specific /48 route always beats a default, so pinning
# fdaa:88:fecd::/48 to the WireGuard interface lets both VPNs coexist.
#
# Run by the LaunchDaemon com.hermes.fly-route-guard every few seconds. Idempotent
# and quiet: only touches the routing table when the route is missing or points at
# the wrong interface. Does nothing when the WireGuard tunnel is down.
#
# NOTE: the Fly org 6PN prefix is fdaa:88:fecd::/48 (derived from the peer/machine
# addresses). If you ever move orgs, update PREFIX below.

PREFIX="fdaa:88:fecd::/48"

# The utun interface currently holding a Fly 6PN (fdaa:) address. Corporate VPNs
# use IPv4 (10.x) + link-local (fe80:) only, so an fdaa: address unambiguously
# identifies the Fly WireGuard tunnel.
IFACE=$(ifconfig 2>/dev/null | awk '/^utun/{i=$1} /inet6 fdaa/{sub(/:$/,"",i); print i; exit}')

# WireGuard tunnel is down — nothing to do.
[ -z "$IFACE" ] && exit 0

# Which interface (if any) currently owns the /48 route.
CUR=$(route -n get -inet6 "${PREFIX%/*}" 2>/dev/null | awk '/interface:/{print $2}')

if [ "$CUR" = "$IFACE" ]; then
    exit 0   # already correct
fi

route -n delete -inet6 "$PREFIX" 2>/dev/null || true
if route -n add -inet6 "$PREFIX" -interface "$IFACE" >/dev/null 2>&1; then
    echo "$(date '+%Y-%m-%dT%H:%M:%S') pinned $PREFIX -> $IFACE (was: ${CUR:-none})"
fi
