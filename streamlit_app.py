"""Streamlit dashboard: the same engine as server.py, in four panes.

    streamlit run streamlit_app.py

Map | agent conversation (QUERY/RESPONSE highlighted) | evidence | translation log.
A switch turns the agents' follow-up questions off, to show live what they add.
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from netcoop import knowledge
from netcoop.orchestrator import diagnose

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "data" / "scenarios"
MANIFEST = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
UNITLESS = ("count", "state", "bool", "ratio")

st.set_page_config(page_title="NetCoop AI", page_icon="◈", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 1.6rem;}
.msg {border-bottom: 1px solid rgba(128,128,128,.25); padding: 6px 0; font-size: 0.92rem;}
.tag {display:inline-block; min-width:84px; text-align:center; font: 600 11px monospace; padding:2px 6px;
      border-radius:5px; margin-right:8px;}
.TASK {background:#eef0f5; color:#5d6475;} .TELEMETRY {background:#e2f3fb; color:#11658a;}
.FINDING {background:#e3f6ea; color:#126b39;} .QUERY, .RESPONSE {background:#f1e6ff; color:#6a2fc2;}
.ERROR {background:#fde7e7; color:#9c2323;} .REPORT {background:#141824; color:#fff;}
.qa {background:rgba(160,110,255,.10); border-left:3px solid #a06eff; padding:8px 12px; margin:4px 0 10px;}
</style>""", unsafe_allow_html=True)


def fmt(v, unit):
    if isinstance(v, float):
        v = round(v, 2)
    return f"{v}" if unit in UNITLESS else f"{v} {unit}"


@st.cache_data(show_spinner=False)
def run(raw, crash, follow_up, llm):
    narrator = None
    if llm:
        from netcoop.llm import narrator as make
        narrator = make()
    res = diagnose(raw, crash_agents=tuple(crash), follow_up=follow_up, narrator=narrator)
    return json.loads(json.dumps(res, default=str))


def health(res, device_id):
    tel = res["telemetry"] or {}
    if device_id in (tel.get("topology") or {}).get("no_telemetry", []):
        return "silent"
    dev = (tel.get("devices") or {}).get(device_id)
    if not dev:
        return "silent"
    worst = {"down": 2, "degraded": 1}.get(dev.get("status"), 0)
    for metric, info in dev["metrics"].items():
        sev = knowledge.assess(metric, info["value"])[0]
        peak = knowledge.assess(metric, info["worst"])[0]
        worst = max(worst, {"normal": 0, "warning": 1, "critical": 2}[sev],
                    1 if peak != "normal" else 0)
    return ["ok", "warn", "crit"][worst]


COLORS = {"ok": ("#e3f6ea", "#1f9d55"), "warn": ("#fff3d6", "#c98200"),
          "crit": ("#fde7e7", "#d23f3f"), "silent": ("#f1f2f5", "#9aa1b2")}


def topology_dot(res):
    r = res["report"]
    topo = ((res["telemetry"] or {}).get("topology") or {}).get("devices") or {}
    root = r["affected_devices"][0] if r["status"] == "diagnosed" and r["affected_devices"] else None
    affected = set(r["affected_devices"]) if r["status"] == "diagnosed" else set()
    lines = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.45;',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 margin="0.12,0.05"];',
             'edge [color="#b8bcc8" arrowhead=none];']
    for did, info in topo.items():
        fill, line = COLORS[health(res, did)]
        style = '"rounded,filled,dashed"' if health(res, did) == "silent" else '"rounded,filled"'
        pen = 3.2 if did == root else 1.2
        border = "#3f46c8" if did == root else line
        label = f"{did}\\n{info.get('type') or ''}{' · wifi' if info.get('medium') == 'wireless' else ''}"
        lines.append(f'"{did}" [label="{label}" fillcolor="{fill}" color="{border}" penwidth={pen} style={style}];')
    for did, info in topo.items():
        up = info.get("upstream")
        if up and up in topo:
            hot = did in affected and (up in affected or up == root)
            attr = ' [color="#d23f3f" penwidth=2.4]' if hot else ""
            lines.append(f'"{up}" -> "{did}"{attr};')
    lines.append("}")
    return "\n".join(lines)


def message_line(m):
    p = m.get("payload") or {}
    text = {
        "TASK": lambda: f"task: <b>{p.get('action')}</b>",
        "TELEMETRY": lambda: p.get("summary", ""),
        "FINDING": lambda: f"<b>{p.get('status')}</b>: {p.get('summary', '')}",
        "QUERY": lambda: f"<b>{p.get('question')}</b>: {p.get('reason', '')}",
        "RESPONSE": lambda: (p.get("detail", "")
                             + (f" &nbsp;<b>supports {p['supports']}</b>" if p.get("supports") else "")
                             + (f" &nbsp;<b>argues against {p['refutes']}</b>" if p.get("refutes") else "")),
        "ERROR": lambda: f"<b>{p.get('code')}</b>: {p.get('message', '')}",
        "REPORT": lambda: "final report delivered",
    }.get(m["type"], lambda: "")()
    note = (p.get("context") or {}).get("protocol_note")
    note = f" <i>({note})</i>" if note else ""
    return (f'<div class="msg"><span class="tag {m["type"]}">{m["type"]}</span>'
            f'<code>{m["sender"]} → {m["recipient"]}</code>{note}<br>{text}</div>')


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("◈ NetCoop AI")
    st.caption("Specialist agents that share evidence and agree on one network diagnosis.")
    source = st.radio("Input", ["Bundled scenario", "Paste or upload telemetry"], horizontal=False)
    raw = None
    if source == "Bundled scenario":
        names = list(MANIFEST)
        default = names.index("conflicting_wifi_vs_congestion.json") if "conflicting_wifi_vs_congestion.json" in names else 0
        name = st.selectbox("Scenario", names, index=default, key="scenario",
                            format_func=lambda n: n.rsplit(".", 1)[0].replace("_", " "))
        st.caption(MANIFEST[name].get("description", ""))
        raw = (SCENARIOS / name).read_text(encoding="utf-8")
    else:
        up = st.file_uploader("JSON, NDJSON or CSV", type=["json", "ndjson", "csv", "txt"])
        text = st.text_area("…or paste it here", height=150,
                            placeholder='{"device_id": "SW_CAMPUS_4", "metrics": {"latency_ms": 926}, "status": "Critical"}')
        raw = up.getvalue().decode("utf-8", errors="replace") if up else (text.strip() or None)
    st.divider()
    follow_up = st.toggle("Agents ask follow-up questions", value=True, key="follow_up",
                          help="Off = the correlation agent decides from the first findings only (the ablation baseline).")
    crash = st.selectbox("Simulate an agent crash", key="crash", options=["none", "performance_agent", "infrastructure_agent", "connectivity_agent"])
    from netcoop import llm as llm_mod
    use_llm = st.checkbox("LLM plain-English summary", value=False, disabled=not llm_mod.is_configured(),
                          help="Needs LLM_API_KEY or GROQ_API_KEY in .env; the diagnosis never depends on it.")

if not raw:
    st.info("Paste or upload telemetry in the sidebar to run a diagnosis.")
    st.stop()

res = run(raw, [] if crash == "none" else [crash], follow_up, use_llm)
r = res["report"]

# ---------------------------------------------------------------- headline
status_badge = {"diagnosed": "🟠", "healthy": "🟢", "insufficient_evidence": "🟣", "error": "🔴"}.get(r["status"], "")
st.markdown(f"#### {status_badge} {r['status'].replace('_', ' ')} · `{r['incident_id']}`")
st.markdown(f"## {r['suspected_root_cause']}")
c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
c1.metric("Confidence", f"{r['confidence']:.2f}")
c1.progress(min(1.0, r["confidence"]), text=r.get("confidence_category"))
c2.metric("Agents' messages", len(res["messages"]))
c2.caption(f"{len(res['rejected_messages'])} rejected by the protocol check")
dq = r.get("data_quality") or {}
c3.metric("Data quality", dq.get("score", "–"))
c3.caption(f"{dq.get('fields_translated', 0)} fields translated")
c4.metric("Follow-up questions", len(r.get("inter_agent_queries") or []))
c4.caption("asked between agents" if follow_up else "switched off (ablation baseline)")
st.write(r.get("summary_llm") or r.get("summary", ""))
if r.get("affected_devices"):
    st.caption("Affected: " + ", ".join(f"`{d}`" for d in r["affected_devices"])
               + (f" · fault domain `{r['fault_domain']}`" if r.get("fault_domain") else ""))

# ---------------------------------------------------------------- four panes
top_left, top_right = st.columns([1.15, 1])
with top_left:
    st.subheader("Network map")
    if res["telemetry"]:
        st.graphviz_chart(topology_dot(res), width="stretch")
        st.caption("green healthy · amber warning · red critical · dashed no data · thick blue border = root cause")
    else:
        st.error(r["suspected_root_cause"])
with top_right:
    st.subheader("Agent conversation")
    only_dialogue = st.toggle("Show only follow-up questions (QUERY / RESPONSE)", value=False, key="only_dialogue")
    shown = [m for m in res["messages"] if not only_dialogue or m["type"] in ("QUERY", "RESPONSE")]
    with st.container(height=470):
        st.markdown("".join(message_line(m) for m in shown) or "_no follow-up questions in this run_",
                    unsafe_allow_html=True)
        with st.expander("Raw netcoop-msg/1.0 messages (JSON)"):
            st.json(res["messages"], expanded=False)

bottom_left, bottom_right = st.columns([1.15, 1])
with bottom_left:
    st.subheader("Evidence and reasoning")
    if r["supporting_evidence"]:
        st.dataframe(pd.DataFrame([{"device": e["device_id"], "metric": e["metric"], "value": fmt(e["value"], e["unit"]),
                                    "severity": e["severity"], "interpretation": e["interpretation"], "agent": e["source"]}
                                   for e in r["supporting_evidence"]]), hide_index=True, width="stretch")
    for q in r.get("inter_agent_queries") or []:
        st.markdown(f'<div class="qa"><code>{q["from"]} → {q["to"]} · {q["question"]} on {q["device"]}</code><br>'
                    f'{q["answer"]}</div>', unsafe_allow_html=True)
    for c in r.get("conflicts_resolved") or []:
        st.markdown(f"**Conflict {' vs '.join(c['between'])}:** {c['resolution']}")
    if r.get("counterfactual"):
        st.markdown(f"**What would change the answer:** {r['counterfactual']}")
    m = r.get("confidence_method")
    if m:
        st.code(f"confidence = {m['top_score']} x (0.65 + 0.35 x {m['margin']}) x (0.5 + 0.5 x {m['data_quality']}) "
                f"x {m['missing_agent_penalty']} = {r['confidence']}", language=None)
    if r.get("alternatives_detail"):
        st.dataframe(pd.DataFrame(r["alternatives_detail"])[["cause", "device", "score", "why_less_likely"]],
                     hide_index=True, width="stretch")
    st.markdown("**Recommended actions**\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(r["recommended_actions"], 1)))
    if r.get("early_warnings"):
        st.markdown("**Early warnings** (statistical baseline, not used for the diagnosis)\n"
                    + "\n".join(f"- {w}" for w in r["early_warnings"]))
    if r["limitations"]:
        st.markdown("**Limitations**\n" + "\n".join(f"- {x}" for x in r["limitations"]))
with bottom_right:
    st.subheader("Translation log")
    q = (res["telemetry"] or {}).get("quality")
    if not q:
        st.error(r["limitations"][0] if r["limitations"] else "input could not be parsed")
    else:
        st.caption(f"{q['records_accepted']}/{q['records_total']} records accepted · "
                   f"{len(q['transformations'])} fields translated · data quality {q['score']}")
        if q["transformations"]:
            st.dataframe(pd.DataFrame([{"device": t["device_id"], "incoming field": t["field"], "incoming value": str(t["raw"]),
                                        "canonical": t["canonical"], "value": fmt(t["value"], t["unit"]), "rule": t["rule"]}
                                       for t in q["transformations"]]), hide_index=True, width="stretch", height=260)
        else:
            st.success("All fields already used canonical names and units; nothing needed translating.")
        if q["issues"]:
            st.dataframe(pd.DataFrame([{"level": i["level"], "code": i["code"], "detail": i["message"]} for i in q["issues"]]),
                         hide_index=True, width="stretch", height=200)
    for rej in res["rejected_messages"]:
        st.warning(f"Rejected message from `{rej['message'].get('sender', '?')}`:\n\n"
                   + "\n".join(f"- `{p}`" for p in rej["problems"]))

st.download_button("Download JSON report", json.dumps(r, indent=2), file_name=f"{r['incident_id']}.json",
                   mime="application/json")
