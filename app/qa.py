"""QA module — POST /api/ask.

Flow: hybrid retrieval → top 4 chunks → assemble messages → QA gateway.

Spec constraints:
  - Only the latest user message drives retrieval (history is NOT used as query).
  - Top 4 hybrid hits only; 0 hits → fixed Chinese text "资料中未找到相关内容",
    citations=[], NO gateway call.
  - Client-supplied system message DISCARDED (service writes its own).
  - Citations order matches [1][2][3][4] in the answer.
  - NO streaming.
  - class_id from the JWT ONLY — request body class_id discarded.
"""
from typing import List, Optional
import requests
from flask import Blueprint, request, jsonify
from .auth import require_auth, get_current_user
from . import llm_traffic, retrieval
from .retrieval import QdrantUnavailable
from .config import Config


qa_bp = Blueprint("qa", __name__, url_prefix="/api")

NO_EVIDENCE_ANSWER = "资料中未找到相关内容"

SYSTEM_PROMPT = (
    "你是基于本班讲义资料回答问题的教学助手。\n"
    "必须依据下方提供的切片正文直接回答用户问题，给出具体、完整的答案"
    "（把规则、定义、步骤、结论说清楚），不要复述问题，"
    "不要写「材料中提到」「请参考」「相关资料」这类空泛套话。\n"
    "若多个切片共同支撑答案，合并它们的信息一起回答。\n"
    "回答末尾用方括号编号标注你实际引用的切片（例如 [1]、[2]），"
    "编号取自各条资料前的序号，标注顺序与资料顺序一致。\n"
    "如果切片正文不足以回答，只回答「资料中未找到相关内容」，不得编造资料中没有的信息。"
)


@qa_bp.route("/ask", methods=["POST"])
@require_auth
def ask_api():
    """POST /api/ask  body: {"message": str, "history": [...optional...], "system": <IGNORED>}"""
    user = get_current_user()
    class_id = user["class_id"]  # JWT ONLY; any body class_id discarded

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message required"}), 400

    history = data.get("history") or []  # optional, list of {role, content}
    # IMPORTANT: client-supplied "system" in body is DISCARDED per spec line 119-121

    # 1) Hybrid retrieval → top 4 (spec line 107-109)
    try:
        result = retrieval.search("hybrid", message, class_id, top_k=4)
    except QdrantUnavailable:
        # Hybrid needs Qdrant; if down, no hybrid → still try keyword fallback?
        # spec says hybrid returns 503 when Qdrant down. But 0 hits is a distinct case.
        # Here Qdrant down means we cannot even try — return degraded answer.
        return jsonify({"answer": NO_EVIDENCE_ANSWER, "citations": [],
                        "degraded": True, "reason": "vector service unavailable"}), 503
    except Exception:
        return jsonify({"error": "service unavailable"}), 503

    # 2) 0 candidate chunks → fixed text, NO gateway call (spec line 113-117)
    if not result.hits:
        return jsonify({"answer": NO_EVIDENCE_ANSWER, "citations": []}), 200

    # 3) Assemble messages for QA gateway
    messages = _build_messages(result.hits, history, message)

    # 4) Call QA gateway — unreachable / non-200 → 503, same shape as the
    #    Qdrant-down branch: fixed answer, NO citations (a degraded answer must
    #    never be paired with citations it cannot back up), degraded flag set.
    try:
        answer_text = _call_qa_gateway(messages)
    except QaGatewayUnavailable:
        return jsonify({"answer": NO_EVIDENCE_ANSWER, "citations": [],
                        "degraded": True, "reason": "qa gateway unavailable"}), 503
    citations = [
        {
            "material_title": h.material_title,
            "chunk_index": h.chunk_index,
            "material_id": h.material_id,
            "char_start": h.char_start,
            "char_end": h.char_end,
        }
        for h in result.hits
    ]

    return jsonify({"answer": answer_text, "citations": citations}), 200


# ---------------------------------------------------------------------------
# Message assembly
# ---------------------------------------------------------------------------

def _build_messages(hits, history: list, user_message: str) -> list:
    """Assemble chat messages. Service writes system message; client system discarded.

    user message = the question, then "以下是相关资料：", then every retrieved chunk
    with 材料标题 / 切片序号 / 切片正文.
    The body is the FULL chunk_text: Hit.excerpt comes straight from
    knowledge_chunks.chunk_text (app/retrieval.py) — the 300-char cap only applies to
    the HTTP search response (SearchResult.to_json), never to the gateway payload.
    """
    parts = []
    for i, h in enumerate(hits, start=1):
        parts.append(
            f"[{i}] 材料标题：{h.material_title}\n"
            f"切片序号：#{h.chunk_index}\n"
            f"切片正文：\n{h.excerpt}"
        )
    context_block = user_message + "\n\n以下是相关资料：\n\n" + "\n\n".join(parts)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # History: filter to valid roles, strip any client-supplied "system" entries
    clean_history = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role", "")
        if role not in ("user", "assistant"):
            continue   # client-supplied "system" → DISCARDED
        content = entry.get("content", "")
        if not content:
            continue
        clean_history.append({"role": role, "content": str(content)})
    # Keep recent 6 rounds max (spec doesn't cap; reasonable bound)
    clean_history = clean_history[-12:]

    messages.extend(clean_history)
    messages.append({"role": "user", "content": context_block})

    return messages


# ---------------------------------------------------------------------------
# QA gateway client (OpenAI chat.completions compatible)
# ---------------------------------------------------------------------------

class QaGatewayUnavailable(Exception):
    """Raised when the chat gateway cannot produce an answer (unreachable,
    non-200, no choices, empty content). Caller turns this into a 503."""
    pass


def _call_qa_gateway(messages: list) -> str:
    url = (Config.QA_URL or "").rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {Config.QA_KEY}",
    }
    body = {
        "model": Config.QA_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 400,
    }
    # 流量日志：请求体原样落盘（写失败不影响问答，见 llm_traffic）。
    scene = llm_traffic.current_scene()
    llm_traffic.log_request(scene, "POST", url, body)

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
    except Exception as e:
        raise QaGatewayUnavailable(f"qa gateway unreachable: {e}")
    if resp.status_code != 200:
        raise QaGatewayUnavailable(f"qa gateway returned HTTP {resp.status_code}")
    try:
        choices = resp.json().get("choices", [])
    except Exception:
        raise QaGatewayUnavailable("qa gateway returned non-JSON body")
    if not choices:
        raise QaGatewayUnavailable("qa gateway returned no choices")
    content = (choices[0].get("message", {}).get("content") or "").strip()
    if not content:
        raise QaGatewayUnavailable("qa gateway returned empty content")
    llm_traffic.log_response(scene, content)
    return content
