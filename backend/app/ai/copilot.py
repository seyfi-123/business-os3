import os, json, logging
from dataclasses import dataclass
from typing import Literal
from sqlalchemy.orm import Session
from app.ai.context_builder import build_context, ContextConfig

logger = logging.getLogger(__name__)


@dataclass
class CopilotResponse:
    answer_tj: str
    mode: Literal["local", "openai"]
    intent: str
    used_sections: list
    def to_dict(self):
        return {"answer_tj": self.answer_tj, "mode": self.mode,
                "intent": self.intent, "used_sections": self.used_sections}


INTENTS = {
    "profit": ["foyda", "profit", "mаржа", "margin", "tushum", "revenue"],
    "leakage": ["oqib", "leak", "yoqotish", "zarar", "loss"],
    "dead": ["dead", "qotib", "zaxira", "stock", "eski"],
    "supplier": ["supplier", "taminotchi", "narx", "price", "qimmat"],
    "variance": ["variance", "inventarizatsiya", "sanoq", "count", "farq"],
    "forecast": ["prognoz", "forecast", "kelajak", "keyingi"],
    "action": ["nima qilish", "action", "tavsiya", "recommend", "harakat"],
}


def _intent(q):
    ql = q.lower()
    scores = {k: sum(1 for key in keys if key in ql) for k, keys in INTENTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "overview"


def _local(intent, ctx):
    cur = ctx.get("company", {}).get("currency", "TJS")
    nar = ctx.get("narrative_tj", {})
    used = []
    def _f(v):
        try:
            return f"{float(v):,.0f} {cur}"
        except Exception:
            return str(v)
    if intent == "profit":
        p = ctx.get("profit") or {}
        used.append("profit")
        ans = f"Tushum: {_f(p.get('revenue', 0))}. Yalpi foyda: {_f(p.get('gross_profit', 0))} (mаржа {p.get('gross_margin_pct', 0)}%). Sof foyda: {_f(p.get('net_profit', 0))}."
        if nar.get("leakage_tj"):
            ans += f" {nar['leakage_tj']}"
        return CopilotResponse(ans, "local", intent, used)
    if intent == "leakage":
        l = ctx.get("leakage") or {}
        used.append("leakage")
        ans = f"Potensial oqib ketish: {_f(l.get('total_leak', 0))}."
        for f in (l.get("findings") or [])[:4]:
            ans += f" {f['message_tj']}"
        return CopilotResponse(ans, "local", intent, used)
    if intent == "dead":
        d = ctx.get("dead_stock") or {}
        s = d.get("summary") or {}
        used.append("dead_stock")
        return CopilotResponse(f"Qotib qolgan kapital: {_f(s.get('total_capital_locked', 0))}.", "local", intent, used)
    if intent == "supplier":
        sa = ctx.get("supplier_anomaly") or {}
        used.append("supplier_anomaly")
        tops = sa.get("top_suppliers") or []
        if not tops:
            return CopilotResponse("Supplier anomaliyalari topilmadi.", "local", intent, used)
        return CopilotResponse(f"{len(tops)} supplierda anomaliya.", "local", intent, used)
    if intent == "variance":
        v = ctx.get("variance") or {}
        s = v.get("summary") or {}
        used.append("variance")
        return CopilotResponse(f"Inventarizatsiyada yoqotish: {_f(s.get('loss_value', 0))}.", "local", intent, used)
    if intent == "forecast":
        r = (ctx.get("forecast") or {}).get("revenue") or {}
        used.append("forecast")
        if not r.get("available"):
            return CopilotResponse("Prognoz uchun tarix yetarli emas.", "local", intent, used)
        return CopilotResponse(f"7 kun: {_f(r['forecast']['7d']['total'])}. 30 kun: {_f(r['forecast']['30d']['total'])}.", "local", intent, used)
    if intent == "action":
        o = ctx.get("optimization") or {}
        acts = o.get("actions") or []
        used.append("optimization")
        if not acts:
            return CopilotResponse("Harakat talab qilinmaydi.", "local", intent, used)
        ans = "Tavsiya etilgan harakatlar:"
        for i, a in enumerate(acts[:5], 1):
            ans += f" {i}. [{a['priority'].upper()}] {a['action']} - {a.get('name')} ({_f(a['impact'])});"
        return CopilotResponse(ans, "local", intent, used)
    used.append("all")
    ans = nar.get("headline_tj") or ""
    for k in ("profit_tj", "leakage_tj", "dead_stock_tj", "supplier_tj", "variance_tj", "forecast_tj", "risks_tj"):
        if nar.get(k):
            ans += f" {nar[k]}"
    if nar.get("top_actions_tj"):
        ans += f"\n\n{nar['top_actions_tj']}"
    return CopilotResponse(ans.strip(), "local", "overview", used)


_SYS = "Sen Business OS AI Copilot'san. Faqat berilgan kontekst raqamlarini izohla. Raqamlarni qayta hisoblama. Tojiк tilida qisqa javob ber."


def _openai(question, ctx, intent):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    slim = {"narrative_tj": ctx.get("narrative_tj"), "profit": ctx.get("profit"),
            "leakage": ctx.get("leakage"),
            "dead_stock_summary": (ctx.get("dead_stock") or {}).get("summary"),
            "supplier_summary": (ctx.get("supplier_anomaly") or {}).get("summary"),
            "variance_summary": (ctx.get("variance") or {}).get("summary"),
            "optimization_summary": (ctx.get("optimization") or {}).get("summary"),
            "top_actions": (ctx.get("optimization") or {}).get("actions", [])[:10]}
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": _SYS},
                      {"role": "system", "content": "KONTEKST: " + json.dumps(slim, ensure_ascii=False, default=str)},
                      {"role": "user", "content": question}],
            temperature=0.2, max_tokens=800)
        return CopilotResponse(resp.choices[0].message.content.strip(),
                               "openai", intent, ["all"])
    except Exception:
        logger.exception("OpenAI failed")
        return None


def ask(db: Session, tenant_id: int, question: str, branch_id=None,
        use_openai=True, cfg=None):
    if not question or not question.strip():
        return CopilotResponse("Savolni yozing.", "local", "empty", [])
    ctx = build_context(db, tenant_id, branch_id, cfg)
    intent = _intent(question)
    if use_openai:
        oa = _openai(question, ctx, intent)
        if oa is not None:
            return oa
    return _local(intent, ctx)
