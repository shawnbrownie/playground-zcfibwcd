#!/usr/bin/env python3
"""pve-lab-chat: a tiny, zero-dependency chat web UI for the LLM running on ai01.

Serves a single-page chat interface and proxies requests to an LLM backend:
  * OpenAI-compatible (BACKEND_TYPE=openai, default) -- llama.cpp llama-server,
                       vLLM, LM Studio ... e.g. BACKEND_URL=http://127.0.0.1:8081
  * Ollama            (BACKEND_TYPE=ollama, e.g. http://127.0.0.1:11434)

Only the Python 3.8+ standard library is used, so it runs anywhere on the
Proxmox host, in an LXC, or in a VM. Access from outside is meant to go
through Tailscale (see README.md).

The browser always receives the reply as NDJSON lines:
  {"delta": "text"}       ... answer text, repeated
  {"reasoning": "text"}   ... "thinking" text from reasoning models, if any
  {"done": true}
  {"error": "message"}
"""

import hmac
import threading
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


def env(name, default=""):
    return os.environ.get(name, default).strip()


CONFIG = {
    "host": env("HOST", "127.0.0.1"),
    "port": int(env("PORT", "8080")),
    "backend_url": env("BACKEND_URL", "http://127.0.0.1:8081").rstrip("/"),
    "backend_type": env("BACKEND_TYPE", "openai").lower(),
    "api_key": env("API_KEY"),
    "default_model": env("DEFAULT_MODEL"),
    "system_prompt": env("SYSTEM_PROMPT", "You are a helpful assistant running on the pve-lab home server. Answer in the user's language."),
    "access_token": env("ACCESS_TOKEN"),
    "title": env("CHAT_TITLE", "pve-lab · ai01"),
    "timeout": float(env("BACKEND_TIMEOUT", "600")),
    "max_concurrent": int(env("MAX_CONCURRENT", "2")),
}

# A single GPU serves one or two generations well; refuse more instead of queueing forever.
_slots = threading.BoundedSemaphore(max(1, CONFIG["max_concurrent"]))

MAX_BODY = 4 * 1024 * 1024  # 4 MiB of conversation is plenty


# --------------------------------------------------------------------------- backend


def backend_request(path, payload=None):
    url = CONFIG["backend_url"] + path
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if CONFIG["api_key"]:
        headers["Authorization"] = "Bearer " + CONFIG["api_key"]
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    return urllib.request.urlopen(req, timeout=CONFIG["timeout"])


def list_models():
    if CONFIG["backend_type"] == "openai":
        with backend_request("/v1/models") as r:
            data = json.load(r)
        return sorted(m["id"] for m in data.get("data", []))
    with backend_request("/api/tags") as r:
        data = json.load(r)
    return sorted(m["name"] for m in data.get("models", []))


def stream_chat(model, messages, options):
    """Yield (kind, text) tuples from the backend; kind is "delta" or "reasoning"."""
    if CONFIG["backend_type"] == "openai":
        payload = {"model": model, "messages": messages, "stream": True}
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        with backend_request("/v1/chat/completions", payload) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                chunk = json.loads(data)
                for choice in chunk.get("choices", []):
                    d = choice.get("delta") or {}
                    thought = d.get("reasoning_content") or d.get("reasoning")
                    if thought:
                        yield "reasoning", thought
                    if d.get("content"):
                        yield "delta", d["content"]
        return

    payload = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options
    with backend_request("/api/chat", payload) as r:
        for raw in r:
            line = raw.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                raise RuntimeError(chunk["error"])
            msg = chunk.get("message") or {}
            if msg.get("thinking"):
                yield "reasoning", msg["thinking"]
            if msg.get("content"):
                yield "delta", msg["content"]
            if chunk.get("done"):
                return


def describe_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        return "backend HTTP %s: %s" % (exc.code, body or exc.reason)
    if isinstance(exc, urllib.error.URLError):
        return "cannot reach backend %s (%s)" % (CONFIG["backend_url"], exc.reason)
    return str(exc) or exc.__class__.__name__


# --------------------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    server_version = "pve-lab-chat/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers
    def send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        token = CONFIG["access_token"]
        if not token:
            return True
        given = self.headers.get("Authorization", "")
        if given.startswith("Bearer "):
            given = given[7:]
        return hmac.compare_digest(given.encode(), token.encode())

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("bad request body size")
        return json.loads(self.rfile.read(length))

    # -- routes
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/config":
            return self.send_json(200, {
                "title": CONFIG["title"],
                "defaultModel": CONFIG["default_model"],
                "systemPrompt": CONFIG["system_prompt"],
                "authRequired": bool(CONFIG["access_token"]),
                "backend": CONFIG["backend_type"],
            })
        if path == "/api/models":
            if not self.authorized():
                return self.send_json(401, {"error": "unauthorized"})
            try:
                return self.send_json(200, {"models": list_models()})
            except Exception as exc:  # noqa: BLE001
                return self.send_json(502, {"error": describe_error(exc)})
        if path == "/healthz":
            return self.send_json(200, {"ok": True})
        return self.serve_static(path)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/chat":
            return self.send_json(404, {"error": "not found"})
        if not self.authorized():
            return self.send_json(401, {"error": "unauthorized"})
        try:
            req = self.read_json()
            model = str(req.get("model") or CONFIG["default_model"])
            messages = [
                {"role": str(m["role"]), "content": str(m["content"])}
                for m in req.get("messages", [])
                if m.get("role") in ("system", "user", "assistant")
            ]
            options = {}
            if isinstance(req.get("temperature"), (int, float)):
                options["temperature"] = float(req["temperature"])
        except Exception as exc:  # noqa: BLE001
            return self.send_json(400, {"error": "invalid request: %s" % exc})
        if not model:
            return self.send_json(400, {"error": "no model selected"})
        if not messages:
            return self.send_json(400, {"error": "no messages"})
        if not _slots.acquire(blocking=False):
            return self.send_json(429, {"error": "ai01 正忙（已达到 MAX_CONCURRENT），请稍后再试"})
        try:
            self.stream_reply(model, messages, options)
        finally:
            _slots.release()

    def stream_reply(self, model, messages, options):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit(obj):
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        try:
            for kind, text in stream_chat(model, messages, options):
                emit({kind: text})
            emit({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            return  # browser pressed "stop" / closed the tab
        except Exception as exc:  # noqa: BLE001
            try:
                emit({"error": describe_error(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            return self.send_json(404, {"error": "not found"})
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    if CONFIG["backend_type"] not in ("ollama", "openai"):
        sys.exit("BACKEND_TYPE must be 'ollama' or 'openai'")
    httpd = ThreadingHTTPServer((CONFIG["host"], CONFIG["port"]), Handler)
    httpd.daemon_threads = True
    print("pve-lab-chat listening on http://%s:%d -> %s (%s)" % (
        CONFIG["host"], CONFIG["port"], CONFIG["backend_url"], CONFIG["backend_type"]), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
