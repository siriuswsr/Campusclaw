"""Local mock embedding gateway.
Serves OpenAI-compatible /v1/embeddings endpoint.
Start on Windows host so docker containers can reach via host.docker.internal.

python mock_embed_server.py 8001
"""
import sys, os, hashlib, re, json, math
from http.server import HTTPServer, BaseHTTPRequestHandler
import numpy as np

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
DIM = 1536


def text_to_bigrams(text):
    text = text.lower()
    bigrams = []
    for i in range(len(text) - 1):
        a, b = text[i], text[i+1]
        if '\u4e00' <= a <= '\u9fff' or '\u4e00' <= b <= '\u9fff':
            bigrams.append(a + b)
    for w in re.findall(r'[a-z]{3,}', text):
        bigrams.append(w)
    return bigrams


def hash_to_slot(bigram):
    h = hashlib.sha256(bigram.encode()).digest()
    slot = int.from_bytes(h[:6], 'big') % DIM
    w = (int.from_bytes(h[6:10], 'big') % 10000) / 10000.0 * 2.0 - 1.0
    return slot, w


def pseudo_embed(text):
    vec = np.zeros(DIM, dtype=np.float64)
    for bg in text_to_bigrams(text):
        slot, w = hash_to_slot(bg)
        vec[slot] += w
    norm = np.linalg.norm(vec)
    if norm < 1e-9:
        rng = np.random.default_rng(hash(text) & 0xffffffff)
        vec = rng.standard_normal(DIM)
        norm = np.linalg.norm(vec)
    vec /= norm
    return [float(x) for x in vec.tolist()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[embed-mock] " + fmt % args + "\n")

    def do_POST(self):
        path = self.path.split('?')[0]
        if path.endswith('/embeddings'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body)
            except Exception:
                self._send(400, {"error": "bad json"})
                return
            model = req.get('model', 'text-embedding-3-small')
            inp = req.get('input', '')
            if isinstance(inp, str):
                texts = [inp]
            else:
                texts = list(inp)
            data = []
            for i, t in enumerate(texts):
                data.append({
                    "object": "embedding",
                    "index": i,
                    "embedding": pseudo_embed(t),
                })
            resp = {
                "object": "list",
                "model": model,
                "data": data,
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            }
            self._send(200, resp)
        else:
            self._send(404, {"error": "not found", "path": path})

    def _send(self, status, obj):
        payload = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    print(f"[embed-mock] Listening on 0.0.0.0:{PORT}  (DIM={DIM})")
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
