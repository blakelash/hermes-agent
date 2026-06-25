#!/usr/bin/env node
// Placeholder entry point. The real server launch is defined by `mcp_config` in
// manifest.json, which runs `npx mcp-remote <url> --allow-http` to bridge Claude
// Desktop (stdio) to the self-hosted Hindsight streamable-HTTP endpoint over the
// WireGuard tunnel. This file exists only to satisfy the MCPB manifest schema,
// which requires server.entry_point. If for some reason it is executed directly,
// hand off to mcp-remote with the same arguments.
const { spawn } = require("node:child_process");
const url = "http://hindsight-mem.internal:8888/mcp/hermes/";
const child = spawn(
  "npx",
  ["-y", "mcp-remote", url, "--allow-http", "--transport", "http-only"],
  { stdio: "inherit" }
);
child.on("exit", (code) => process.exit(code ?? 0));
