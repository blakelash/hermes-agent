#!/bin/sh
# Wire the Hermes brain (default profile at /opt/data) to the self-hosted Hindsight
# backend. Idempotent. Run as root on hermes-collab; chowns back to uid 10000.
set -e

mkdir -p /opt/data/hindsight
cat > /opt/data/hindsight/config.json <<'JSON'
{
  "mode": "local_external",
  "api_url": "http://hindsight-mem.internal:8888",
  "bank_id": "hermes"
}
JSON

/opt/hermes/.venv/bin/python - <<'PY'
from ruamel.yaml import YAML
y = YAML()
p = "/opt/data/config.yaml"
with open(p, encoding="utf-8") as f:
    d = y.load(f)
mem = d.get("memory")
if mem is None:
    d["memory"] = {"provider": "hindsight"}
else:
    mem["provider"] = "hindsight"
with open(p, "w", encoding="utf-8") as f:
    y.dump(d, f)
print("OK memory.provider = hindsight")
PY

chown -R 10000:10000 /opt/data/hindsight
chown 10000:10000 /opt/data/config.yaml
echo PROVISION_DONE
