# Connect local AI tools to the shared Hindsight bank

**Goal:** local tools (Claude Code first; Codex CLI / Claude Desktop next) read+write the
*same* `hermes` memory bank the Fly brain uses → bidirectional context between Hermes and
the tools you already use. No third-party server; the data stays on your self-hosted box.

## How it fits together

- `hindsight-mem` (Fly) has **no public IP**, binds IPv6 on the 6PN, and its `/mcp`
  endpoint is **unauthenticated by design**. That is safe *only* because nothing outside
  the Fly private network can reach it.
- We bridge the laptop onto that private network with a **personal WireGuard peer**
  (`fly wireguard`). Only your enrolled device(s) can reach the box; nothing is exposed
  publicly, and the Hindsight app is not modified at all.
- Claude Code's MCP client then points at
  `http://hindsight-mem.internal:8888/mcp/hermes/` over the tunnel. The `/hermes` path
  segment selects the shared bank the brain already uses.

> Keep `hindsight-mem` private forever — never give it a public IP. The tunnel is the
> only ingress. (Tailscale becomes the better tool once the mesh grows — SLURM cluster +
> file server + multiple devices; WireGuard is the single-laptop version for now.)

## Step 1 — Create the WireGuard tunnel  *(you run; it handles your private key)*

```sh
fly wireguard create personal iad laptop-hindsight
```

- Writes a `laptop-hindsight.conf`. Import it into the WireGuard app
  (macOS: **WireGuard.app → Import tunnel(s) from file…**), then toggle it **ON**.
- This grants the laptop Fly 6PN access **and** `*.internal` DNS resolution. Zero changes
  to any deployed app.

## Step 2 — Verify reachability + that the 0.6.1 server actually serves MCP

(Run after the tunnel is up. `.internal` is IPv6-only, hence `-6`.)

```sh
# 2a. health
curl -6 http://hindsight-mem.internal:8888/health

# 2b. MCP tools/list against the shared bank (streamable-http JSON-RPC)
curl -sN http://hindsight-mem.internal:8888/mcp/hermes/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expect: `2a` → `{"status":"healthy",...}`; `2b` → a list including `retain`, `recall`,
`reflect`, ….

**Version-skew fallback:** we run the server pinned at **0.6.1** (to match Hermes'
bundled `hindsight-client==0.6.1`). If URL-path bank routing (`/mcp/hermes/`) 404s on
that version, use the header form instead — URL `…/mcp/` + `X-Bank-Id: hermes`:

```sh
curl -sN http://hindsight-mem.internal:8888/mcp/ \
  -H 'X-Bank-Id: hermes' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Step 3 — Wire Claude Code  *(user scope = available in every project)*

```sh
claude mcp add -s user --transport http hindsight-hermes \
  http://hindsight-mem.internal:8888/mcp/hermes/
```

If you needed the header fallback in Step 2, use this form instead:

```sh
claude mcp add -s user --transport http hindsight-hermes \
  http://hindsight-mem.internal:8888/mcp/ \
  --header "X-Bank-Id: hermes"
```

Restart Claude Code. You now have `retain` / `recall` / `reflect` + the rest of the
~30 knowledge tools, all against the shared `hermes` bank — so anything you save in
Claude Code is recallable by the Hermes brain next session, and vice versa.

- Added at **user scope** (`~/.claude.json`), NOT into this repo's `.mcp.json` — this is
  personal infra pointing at a private host; it must not be committed.

## Troubleshooting: unreachable behind a corporate VPN (GlobalProtect / AnyConnect)

**Symptom:** Claude Code shows `hindsight-hermes` failing with `FailedToOpenSocket` or
`ConnectionRefused` at `http://hindsight-mem.internal:8888/mcp/hermes/`, even though the
WireGuard tunnel is toggled ON and the Fly machine is healthy.

**Cause:** A corporate full-tunnel VPN (Palo Alto GlobalProtect, Cisco AnyConnect) installs
a `default` (::/0) IPv6 route that ties with — and beats — the one WireGuard installs, so
packets to Fly's `fdaa:` 6PN are blackholed. Leftover `default → fe80::%utunN` routes from
the VPN's tunnel interfaces can poison IPv6 route selection even after the VPN disconnects.

**Diagnose** (tunnel must be ON):

```sh
# Which interface holds the Fly 6PN address (this is the WireGuard tunnel):
ifconfig | awk '/^utun/{i=$1} /inet6 fdaa/{print i, $2}'
# Where does traffic to the machine actually go? If this is NOT the utun above, the VPN is winning:
route -n get -inet6 fdaa:88:fecd:a7b:1b7:98b9:9d9d:2 | grep interface
# Reachability straight to the machine over the tunnel:
curl -6 -sS --max-time 8 http://hindsight-mem.internal:8888/health   # want: {"status":"healthy",...}
```

**Fix (one-shot):** pin a more-specific /48 route to the WireGuard interface — /48 beats the
VPN's default, so both coexist:

```sh
sudo sh deploy/hindsight/fly-route-guard.sh   # auto-detects the current utun; idempotent
```

**Fix (permanent — recommended if you keep the VPN on all day):** install the route guard as
a root LaunchDaemon. It re-asserts the /48 within ~15s of a reboot, VPN reconnect, or
WireGuard interface renumber, so Hindsight stays reachable without babysitting.

```sh
sudo cp deploy/hindsight/fly-route-guard.sh /usr/local/bin/fly-route-guard.sh
sudo chmod 755 /usr/local/bin/fly-route-guard.sh
sudo cp deploy/hindsight/com.hermes.fly-route-guard.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.hermes.fly-route-guard.plist
sudo chmod 644 /Library/LaunchDaemons/com.hermes.fly-route-guard.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.hermes.fly-route-guard.plist
# verify it's running + watch it work:
sudo launchctl print system/com.hermes.fly-route-guard | grep -E 'state|pid'
tail -f /var/log/fly-route-guard.log
```

Uninstall: `sudo launchctl bootout system /Library/LaunchDaemons/com.hermes.fly-route-guard.plist`
then remove the two files.

> The route guard only pins the route; the WireGuard tunnel itself must still be ON (it does
> nothing when no `fdaa:` interface exists). It never touches non-Fly traffic, so your
> corporate VPN is unaffected.

## What this is (and isn't) yet

- This is the **explicit read/write/search** layer (the agent calls `recall`/`retain`
  when relevant). It's the safe first step.
- **Auto-retain-on-every-response** is a *separate* plugin (`claude plugin install
  hindsight-memory` + `~/.hindsight/claude-code.json` → `hindsightApiUrl`). Add it later,
  and when you do, scope it with `retain_tags` / `recall_tags` so Claude Code's
  project-level chatter doesn't flood the shared bank.

## Next tools (same pattern)

- **Claude Desktop**: install the prebuilt bundle `deploy/hindsight/hindsight-hermes.mcpb`
  (Settings → Extensions → drag it in). Source in `deploy/hindsight/claude-desktop-mcpb/`
  (`npx @anthropic-ai/mcpb pack .` to rebuild). It runs `npx mcp-remote <url> --allow-http
  --transport http-only` as a stdio bridge — so the WireGuard tunnel must be ON and Node/npx
  must be on PATH. The schema requires `server.entry_point`, hence the placeholder
  `server/index.js` (the real launch is `mcp_config`, which is authoritative).
- **Codex CLI**: point its MCP config at the identical URL
  (`http://hindsight-mem.internal:8888/mcp/hermes/`) over the same tunnel.
- **ChatGPT-web / Claude.ai-web**: cannot reach the private net (they call from the
  vendor's servers). Cover their history with a periodic data-export → `retain` backfill
  instead of live sync.
