"""In-process test of the Hermes Hindsight memory-provider glue against the live
server. Env-configured (no config.json, no persistent state). Read/exec only."""
import os, json

os.environ["HINDSIGHT_MODE"] = "local_external"
os.environ["HINDSIGHT_API_URL"] = "http://hindsight-mem.internal:8888"
os.environ["HINDSIGHT_BANK_ID"] = "hermes"

import plugins.memory.hindsight as m

p = m.HindsightMemoryProvider()
print("is_available:", p.is_available())

try:
    p.initialize(session_id="glue-test")
    print("initialize: OK")
except Exception as e:
    print("initialize ERR:", repr(e))

try:
    schemas = p.get_tool_schemas() or []
    names = [s.get("function", {}).get("name") for s in schemas]
    print("tool names:", names)
except Exception as e:
    print("get_tool_schemas ERR:", repr(e))

# 1) recall the fact retained earlier (proves end-to-end through the provider)
try:
    out = p.handle_tool_call("hindsight_recall",
                             {"query": "Which compute region does Blake prefer?"})
    print("RECALL_OUT:", str(out)[:1200])
except Exception as e:
    print("recall ERR:", repr(e))

# 2) retain a fresh fact through the provider, then recall it
try:
    out = p.handle_tool_call("hindsight_retain",
                             {"content": "Blake validated the Hermes Hindsight provider glue on 2026-06-25."})
    print("RETAIN_OUT:", str(out)[:600])
except Exception as e:
    print("retain ERR:", repr(e))

try:
    if hasattr(p, "shutdown"):
        p.shutdown()
except Exception:
    pass
print("GLUE_TEST_DONE")
