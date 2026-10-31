"""Optional LLM layer. The diagnosis never depends on it.

The narrator rewrites the finished report as a short plain-English summary for
a non-specialist. It is given only the report fields and told not to add
facts; the root cause, confidence and evidence are decided by the agents.

Configure in .env (any OpenAI-compatible endpoint):
    LLM_API_KEY=...            (or GROQ_API_KEY)
    LLM_BASE_URL=https://api.groq.com/openai/v1
    LLM_MODEL=openai/gpt-oss-20b
"""

import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROMPT = ("You are writing for a campus IT operator. Rewrite this network diagnosis as 3 short sentences: "
          "what is wrong and where, how sure the system is and why, and the first thing to do. "
          "Use only facts in the JSON. Do not add causes, devices or numbers that are not in it. "
          "If status is insufficient_evidence, say clearly that no root cause is claimed.\n\n")


def _client():
    key = os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not key:
        return None, None
    from openai import OpenAI
    base = os.environ.get("LLM_BASE_URL") or "https://api.groq.com/openai/v1"
    model = os.environ.get("LLM_MODEL") or "openai/gpt-oss-20b"
    return OpenAI(api_key=key, base_url=base, timeout=20), model


def narrator():
    client, model = _client()
    if client is None:
        return None

    def narrate(report):
        keep = {k: report.get(k) for k in ("status", "suspected_root_cause", "confidence", "confidence_category",
                                           "affected_devices", "supporting_evidence", "alternative_causes",
                                           "recommended_actions", "limitations")}
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0.2, max_tokens=900,
                messages=[{"role": "user", "content": PROMPT + json.dumps(keep, default=str)}])
            text = resp.choices[0].message.content or ""
            # models like to emit narrow / non-breaking spaces that old consoles cannot print
            text = " ".join(text.replace(" ", " ").replace(" ", " ").split())
            return text or None
        except Exception:
            return None   # the deterministic summary is always there as fallback

    return narrate


def is_configured():
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY"))
