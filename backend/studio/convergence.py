"""Stage-scoped human budgets and auditable AI decisions, separate from cycle epochs."""
import re
import hashlib

HUMAN_ROUNDS = 2


def topic_key(request):
    explicit = request.get("topic_key", "").strip()
    text = explicit or re.sub(r"[^\w]", "", request.get("question", "").lower())
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def budget(state, stage):
    current = state.get("stage_budgets", {}).get(stage)
    if current:
        return dict(current)
    legacy = sum(answer.get("stage_id") == stage and bool(answer.get("choices") or answer.get("questions"))
                 for answer in state.get("answers", []))
    return dict(used=legacy, limit=HUMAN_ROUNDS)


def remaining_requests(state, stage, draft):
    requests = draft.get("decision_requests") or [dict(id="Q"+str(i+1),question=q,category="scope") for i,q in enumerate(draft.get("questions", []))]
    answered = {item.get("key") for item in state.get("decision_ledger", [])
                if item.get("stage_id") == stage and item.get("status") == "answered"}
    return [dict(request, key=topic_key(request)) for request in requests
            if topic_key(request) not in answered and request.get("category") != "ordinary"]


def auto_records(draft, requests, stage, mode):
    records = []
    for request in requests:
        choice = request.get("recommendation", "").strip()
        records.append(dict(stage_id=stage, key=topic_key(request), question=request["question"],
                            choice=choice or "保留当前方案，将此项列为未决风险，由最终确认决定；不声称已经落实。",
                            reason="普通细节自主处理" if request.get("category") == "ordinary" else "人工答疑额度用完或已回答，不再自动重复追问",
                            impact=request.get("impact", "待最终确认"), status=mode))
    for item in draft.get("ai_decisions", []):
        records.append(dict(item, stage_id=stage, key=topic_key(item), status=mode))
    return records
