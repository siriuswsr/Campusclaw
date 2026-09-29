"""Combined mock gateway: embeddings + chat.completions.
Starts one HTTP server, serves both /v1/embeddings and /v1/chat/completions.

Usage: python mock_gateway.py 8001
"""
import sys, json, hashlib, re
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


def bigram_set(text):
    """CJK bigrams + ascii words, lowercased — used for question/chunk overlap."""
    text = text.lower()
    out = set()
    for i in range(len(text) - 1):
        a, b = text[i], text[i+1]
        if '\u4e00' <= a <= '\u9fff' and '\u4e00' <= b <= '\u9fff':
            out.add(a + b)
    for w in re.findall(r'[a-z0-9]{2,}', text):
        out.add(w)
    return out


CHUNK_RE = re.compile(
    r'\[(\d+)\]\s*材料标题：(.+?)\n切片序号：#(\d+)\n切片正文：\n(.*?)'
    r'(?=\n\n\[\d+\]\s*材料标题：|\Z)', re.S)


def parse_chunks(context):
    """Parse the 资料块 written by app.qa._build_messages → [(no, title, index, body)]."""
    return [(int(no), title.strip(), int(idx), body.strip())
            for no, title, idx, body in CHUNK_RE.findall(context)]


def split_sentences(body):
    """Split a chunk body into sentence-ish pieces (keeps the trailing punctuation)."""
    return [s.strip() for s in re.split(r'(?<=[。！？；])|\n+', body) if s.strip()]


def overlap(sentence, q_grams):
    return len(q_grams & bigram_set(sentence))


def mock_chat(messages):
    """Extractive stub standing in for the real LLM gateway: it answers FROM the
    supplied chunk bodies — pick the chunk(s) whose text overlaps the question most,
    then quote that chunk's overlapping sentences verbatim (headings skipped), and
    tag the used chunk numbers. No overlap at all → the fixed no-evidence text.
    """
    last_user = ""
    for m in messages:
        if m.get('role') == 'user':
            last_user = m.get('content', '')

    marker = "以下是相关资料："
    if marker in last_user:
        question, context = last_user.split(marker, 1)
        question = question.strip()
    else:
        question, context = "", last_user

    q_grams = bigram_set(question)
    scored = []
    for no, _title, _idx, body in parse_chunks(context):
        sentences = split_sentences(body)
        total = sum(overlap(s, q_grams) for s in sentences)
        if total <= 0:
            continue
        quotes = [s for s in sentences if not s.startswith('#') and overlap(s, q_grams) > 0]
        if not quotes:
            quotes = [s for s in sentences if not s.startswith('#')]
        scored.append((total, no, quotes))

    if not scored:
        return "资料中未找到相关内容"

    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][0]
    # best chunk, plus a runner-up that is at least half as relevant
    used = [c for c in scored if c[0] * 2 >= best][:2]

    picked = []
    for _score, _no, quotes in used:
        for s in quotes:
            if s not in picked:
                picked.append(s)
    nums = sorted({no for _s, no, _q in used})
    return " ".join(picked) + " [" + ", ".join(str(n) for n in nums) + "]"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[mock-gw] " + fmt % args + "\n")

    def do_POST(self):
        path = self.path.split('?')[0]
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body) if body else {}
        except Exception as e:
            self._send(400, {"error": f"bad json: {e}"})
            return

        if path.endswith('/embeddings'):
            self._handle_embed(req)
        elif path.endswith('/chat/completions'):
            self._handle_chat(req)
        else:
            self._send(404, {"error": "not found", "path": path})

    def _handle_embed(self, req):
        model = req.get('model', 'text-embedding-3-small')
        inp = req.get('input', '')
        texts = [inp] if isinstance(inp, str) else list(inp)
        data = [{"object": "embedding", "index": i, "embedding": pseudo_embed(t)} for i, t in enumerate(texts)]
        resp = {"object": "list", "model": model, "data": data, "usage": {"prompt_tokens": 0, "total_tokens": 0}}
        self._send(200, resp)

    def _handle_chat(self, req):
        model = req.get('model', 'gpt-4o-mini')
        messages = req.get('messages', [])
        answer = mock_chat(messages)
        resp = {
            "id": "mock-1",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        self._send(200, resp)

    def _send(self, status, obj):
        payload = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    print(f"[mock-gateway] 0.0.0.0:{PORT}  embeddings+chat.completions  (DIM={DIM})")
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
