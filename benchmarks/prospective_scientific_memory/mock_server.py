"""A tiny offline OpenAI-compatible endpoint for validating the PSMB harness.

No LLM credentials exist in this environment, so to prove the whole pipeline
runs end-to-end through the *real* Hermes agent loop we point Hermes at this
mock. The mock simulates a scientific collaborator whose recall is bounded by
what is in its context window -- exactly the mechanism the benchmark studies:

  * ``recall``  (default): on a task turn, names the distinctive scientific
    entities it can see in the conversation context (so full_context recalls the
    old chain; no_memory cannot). Brief, connection-free acks on lab-update
    turns -> timely, low interruption.
  * ``myopic`` : never volunteers prior entities (a weak baseline).
  * ``noisy``  : dumps entities every turn (high interruption / premature).

This is a harness fixture, NOT a scientific model. Point Hermes at a real
provider for real evaluation.
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Tuple

from benchmarks.prospective_scientific_memory.world import (
    _CELLTYPE_STEM,
    _GENE_STEM,
    _METABOLITE_STEM,
    _PATHWAY_STEM,
    _PHENO_STEM,
)

# The mock recognizes the domain vocabulary (shared fixtures with the generator)
# plus structural name shapes. It never sees per-episode ground truth -- it only
# echoes distinctive terms it can find in its own context window, which is the
# mechanism the benchmark studies.
_VOCAB = sorted(set(_METABOLITE_STEM + _PATHWAY_STEM + _GENE_STEM
                    + _PHENO_STEM + _CELLTYPE_STEM), key=len, reverse=True)
_VOCAB_RE = re.compile(r"\b(?:" + "|".join(re.escape(v) for v in _VOCAB) + r")\b",
                       re.IGNORECASE)

_ENTITY_PATTERNS = [
    re.compile(r"\bDrug\s+[A-Z]\b"),
    re.compile(r"\b(?:phenotype|pathway|metabolite)\s+[A-Z]\b"),
    re.compile(r"\b[A-Z]{2,5}-\d{2,4}\b"),           # compound codes DRX-114
    re.compile(r"\b[A-Z][A-Za-z0-9]*[0-9][A-Za-z0-9]*\b"),  # gene-ish NRF2, GPX4
    _VOCAB_RE,
]

_STOP = {"month", "lab", "update", "note", "report", "co2", "qc", "ra"}


def _extract_entities(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for pat in _ENTITY_PATTERNS:
        for m in pat.finditer(text):
            tok = m.group(0).strip()
            key = tok.lower()
            if key in _STOP or key in seen or len(tok) < 3:
                continue
            seen.add(key)
            found.append(tok)
    return found


def _context_text(messages: List[dict], *, include_last: bool) -> str:
    msgs = messages if include_last else messages[:-1]
    return "\n".join(str(m.get("content", "")) for m in msgs
                     if m.get("role") in ("user", "assistant"))


def _last_user(messages: List[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


_TASK_CUES = ("designing", "design the next", "what experiment", "interpret",
              "prioritize", "propose", "discrepancy", "diagnostic", "connect it to",
              "which earlier")


def _is_task(user: str) -> bool:
    u = user.lower()
    return any(c in u for c in _TASK_CUES)


def respond_policy(messages: List[dict], policy: str = "recall") -> str:
    """The mock 'brain'. Deterministic given the conversation."""
    user = _last_user(messages)
    task = _is_task(user)
    prior = _extract_entities(_context_text(messages, include_last=False))
    here = set(_extract_entities(user))
    # entities we could 'recall' that are NOT in the current stimulus
    recalled = [e for e in prior if e not in here]

    if policy == "myopic":
        return ("Here is a straightforward plan based on the current request. "
                "I'll focus on the immediate result and standard controls.")

    if policy == "noisy" or (policy == "recall" and task):
        if recalled:
            lst = ", ".join(dict.fromkeys(recalled))
            return (
                "This may connect to earlier results in the project. In "
                f"particular, {lst} appear related through the mechanistic axis "
                "we've been building up. I'd design the next experiment to test "
                f"that link explicitly, since the same phenotype recurs across "
                "these observations.")
        return ("Based on the current context I don't see an obvious link to "
                "prior work; I'd proceed with standard next steps and controls.")

    # recall policy, non-task (lab update): brief acknowledgment, no blurting
    return ("Noted and filed. I'll keep this in mind as the project develops.")


def _completion_json(model: str, content: str) -> Dict:
    return {
        "id": f"psmb-{int(time.time()*1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chunk_json(model: str, delta: Dict, finish=None) -> Dict:
    return {
        "id": "psmb-stream", "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def make_handler(policy: str = "recall"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence
            pass

        def _send(self, code: int, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                body = json.dumps({"object": "list", "data": [
                    {"id": "psmb-mock", "object": "model", "owned_by": "psmb"}]}).encode()
                return self._send(200, body)
            return self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(raw or b"{}")
            except Exception:
                req = {}
            messages = req.get("messages", [])
            model = req.get("model", "psmb-mock")
            content = respond_policy(messages, policy=policy)
            if req.get("stream"):
                return self._stream(model, content)
            body = json.dumps(_completion_json(model, content)).encode()
            return self._send(200, body)

        def _stream(self, model: str, content: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def emit(obj):
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                self.wfile.flush()

            emit(_chunk_json(model, {"role": "assistant"}))
            for piece in _split_stream(content):
                emit(_chunk_json(model, {"content": piece}))
            emit(_chunk_json(model, {}, finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _split_stream(content: str, n: int = 24) -> List[str]:
    return [content[i:i + n] for i in range(0, len(content), n)] or [""]


def start_server(port: int = 0, policy: str = "recall") -> Tuple[ThreadingHTTPServer, int]:
    """Start the mock server on a background thread. Returns (server, port)."""
    import threading
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(policy))
    real_port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd, real_port


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Run the PSMB mock OpenAI endpoint.")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--policy", default="recall", choices=["recall", "myopic", "noisy"])
    args = ap.parse_args(argv)
    httpd, port = start_server(args.port, args.policy)
    print(f"PSMB mock ({args.policy}) on http://127.0.0.1:{port}/v1")
    try:
        import time as _t
        while True:
            _t.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
