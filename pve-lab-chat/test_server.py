"""End-to-end tests: real pve-lab-chat server against fake Ollama / OpenAI backends."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import server


class FakeBackend(BaseHTTPRequestHandler):
    last_payload = None

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._send(json.dumps({"models": [{"name": "qwen2.5:7b"}, {"name": "llama3:8b"}]}).encode())
        if self.path == "/v1/models":
            return self._send(json.dumps({"data": [{"id": "my-vllm-model"}]}).encode())
        self.send_error(404)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeBackend.last_payload = payload
        if self.path == "/api/chat":
            chunks = [{"message": {"content": "你好"}, "done": False},
                      {"message": {"content": "，世界"}, "done": False},
                      {"message": {"content": ""}, "done": True}]
            return self._send(b"".join(json.dumps(c, ensure_ascii=False).encode() + b"\n" for c in chunks),
                              "application/x-ndjson")
        if self.path == "/v1/chat/completions":
            lines = ['data: {"choices":[{"delta":{"role":"assistant"}}]}',
                     'data: {"choices":[{"delta":{"content":"Hello"}}]}',
                     'data: {"choices":[{"delta":{"content":" there"}}]}',
                     "data: [DONE]"]
            return self._send(("\n\n".join(lines) + "\n\n").encode(), "text/event-stream")
        self.send_error(404)


def start(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class ChatServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = start(FakeBackend)
        cls.app = start(server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.app.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.backend.shutdown()
        cls.app.shutdown()

    def setUp(self):
        self._saved = dict(server.CONFIG)
        server.CONFIG["backend_url"] = "http://127.0.0.1:%d" % self.backend.server_address[1]
        server.CONFIG["backend_type"] = "ollama"
        server.CONFIG["access_token"] = ""

    def tearDown(self):
        server.CONFIG.clear()
        server.CONFIG.update(self._saved)

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()

    def chat(self, body, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(), headers=h)
        with urllib.request.urlopen(req) as r:
            return [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]

    def test_index_served(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"pve-lab", body)

    def test_path_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/../server.py")
        self.assertEqual(cm.exception.code, 404)

    def test_ollama_models_and_chat(self):
        _, _, body = self.get("/api/models")
        self.assertEqual(json.loads(body)["models"], ["llama3:8b", "qwen2.5:7b"])
        events = self.chat({"model": "qwen2.5:7b", "temperature": 0.3,
                            "messages": [{"role": "user", "content": "hi"}, {"role": "bogus", "content": "x"}]})
        self.assertEqual("".join(e.get("delta", "") for e in events), "你好，世界")
        self.assertEqual(events[-1], {"done": True})
        self.assertEqual(FakeBackend.last_payload["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(FakeBackend.last_payload["options"], {"temperature": 0.3})

    def test_openai_models_and_chat(self):
        server.CONFIG["backend_type"] = "openai"
        _, _, body = self.get("/api/models")
        self.assertEqual(json.loads(body)["models"], ["my-vllm-model"])
        events = self.chat({"model": "my-vllm-model", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual("".join(e.get("delta", "") for e in events), "Hello there")
        self.assertEqual(events[-1], {"done": True})

    def test_backend_down_reports_error(self):
        server.CONFIG["backend_url"] = "http://127.0.0.1:1"
        events = self.chat({"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn("cannot reach backend", events[-1]["error"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/models")
        self.assertEqual(cm.exception.code, 502)

    def test_access_token(self):
        server.CONFIG["access_token"] = "s3cret"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/models")
        self.assertEqual(cm.exception.code, 401)
        status, _, _ = self.get("/api/models", {"Authorization": "Bearer s3cret"})
        self.assertEqual(status, 200)
        _, _, cfg = self.get("/api/config")
        self.assertTrue(json.loads(cfg)["authRequired"])


if __name__ == "__main__":
    unittest.main()
