"""Streamlit dashboard: the same engine as server.py, styled as a network operations console.

    streamlit run streamlit_app.py

Four panes: network map | agent conversation (QUERY/RESPONSE highlighted) | evidence | translation log.
A switch turns the agents' follow-up questions off, to show live what they add.
"""

import html
import json
from pathlib import Path

import streamlit as st

from netcoop import knowledge
from netcoop.orchestrator import diagnose

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "data" / "scenarios"
MANIFEST = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
UNITLESS = ("count", "state", "bool", "ratio")

# palette: night-shift NOC navy, signal teal for links / root cause, violet for agent dialogue,
# green / amber / red reserved for device health
C = {"bg": "#0B1120", "panel": "#111A2E", "panel2": "#16213A", "line": "#22304D", "text": "#E6EDF7",
     "muted": "#8FA0BC", "teal": "#2DD4BF", "violet": "#A78BFA", "ok": "#34D399", "warn": "#FBBF24",
     "crit": "#F87171", "silent": "#64748B"}
AGENT_COLOR = {"orchestrator": C["muted"], "ingestion_agent": "#60A5FA", "performance_agent": C["teal"],
               "infrastructure_agent": "#F59E0B", "connectivity_agent": "#F472B6",
               "correlation_agent": C["violet"], "reporting_agent": "#E6EDF7", "message_bus": C["crit"]}

st.set_page_config(page_title="NetCoop AI · Network diagnosis console", page_icon="◈", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
:root {{ --mono: 'JetBrains Mono', ui-monospace, Consolas, monospace; }}
html, body, [class*="css"], .stMarkdown, .stText {{ font-family: 'Inter', system-ui, 'Segoe UI', sans-serif; }}
.stApp {{ background: {C['bg']};
  background-image: radial-gradient(rgba(45,212,191,0.07) 1px, transparent 1px); background-size: 22px 22px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding: 1.2rem 2rem 3rem; max-width: 1560px; }}
section[data-testid="stSidebar"] {{ background: #0D1528; border-right: 1px solid {C['line']}; }}
section[data-testid="stSidebar"] .block-container {{ padding-top: 1.5rem; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{ background: {C['panel']}; border: 1px solid {C['line']} !important;
  border-radius: 14px !important; }}
h1, h2, h3, h4 {{ letter-spacing: -0.01em; }}
code {{ font-family: var(--mono) !important; }}

.nc-top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom: 14px; gap: 16px; }}
.nc-brand {{ display:flex; gap:12px; align-items:center; }}
.nc-brand .name {{ font-size: 1.25rem; font-weight: 700; color:{C['text']}; }}
.nc-brand .tag {{ font-size: .82rem; color:{C['muted']}; }}
.nc-chips {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
.chip {{ font: 500 12px var(--mono); color:{C['muted']}; background:{C['panel2']}; border:1px solid {C['line']};
  padding: 4px 10px; border-radius: 999px; white-space: nowrap; }}
.chip b {{ color:{C['text']}; font-weight:600; }}

.nc-hero {{ display:grid; grid-template-columns: 1fr 190px; gap: 24px; align-items:center;
  background: linear-gradient(180deg, {C['panel2']}, {C['panel']}); border:1px solid {C['line']};
  border-radius: 16px; padding: 22px 26px; }}
.pill {{ display:inline-flex; align-items:center; gap:6px; font: 600 11px var(--mono); letter-spacing:.08em;
  text-transform: uppercase; padding: 4px 10px; border-radius: 999px; }}
.pill .dot {{ width:7px; height:7px; border-radius:50%; background: currentColor; }}
.st-diagnosed {{ color:{C['warn']}; background: rgba(251,191,36,.12); }}
.st-healthy {{ color:{C['ok']}; background: rgba(52,211,153,.12); }}
.st-insufficient_evidence {{ color:{C['violet']}; background: rgba(167,139,250,.14); }}
.st-error {{ color:{C['crit']}; background: rgba(248,113,113,.12); }}
.nc-cause {{ font-size: 1.65rem; font-weight: 650; line-height: 1.25; color:{C['text']}; margin: 10px 0 8px; }}
.nc-sum {{ color:{C['muted']}; font-size: .93rem; line-height: 1.55; max-width: 1050px; }}
.nc-dev {{ display:flex; gap:6px; flex-wrap:wrap; margin-top: 12px; align-items:center; }}
.dev {{ font: 500 12px var(--mono); padding: 3px 9px; border-radius: 6px; background:{C['panel']};
  border:1px solid {C['line']}; color:{C['text']}; }}
.dev.root {{ border-color:{C['teal']}; color:{C['teal']}; box-shadow: 0 0 0 1px rgba(45,212,191,.25); }}
.gauge {{ text-align:center; }}
.gauge .cat {{ font: 600 11px var(--mono); letter-spacing:.1em; text-transform:uppercase; margin-top:2px; }}

.kpis {{ display:grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 14px 0 18px; }}
.kpi {{ background:{C['panel']}; border:1px solid {C['line']}; border-radius: 12px; padding: 14px 16px; }}
.kpi .v {{ font: 600 1.6rem var(--mono); color:{C['text']}; line-height:1.1; }}
.kpi .l {{ font-size: .78rem; color:{C['muted']}; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }}
.kpi .s {{ font-size: .78rem; color:{C['muted']}; margin-top: 4px; }}
.kpi.accent {{ border-color: rgba(167,139,250,.55); }}
.kpi.accent .v {{ color:{C['violet']}; }}

.ph {{ display:flex; justify-content:space-between; align-items:baseline; margin: 2px 0 10px; gap: 10px; }}
.ph .t {{ font-size: .95rem; font-weight: 650; color:{C['text']}; letter-spacing:.01em; }}
.ph .t span {{ color:{C['teal']}; margin-right: 8px; }}
.ph .s {{ font-size: .78rem; color:{C['muted']}; text-align: right; }}
.legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:.76rem; color:{C['muted']}; margin-top: 4px; }}
.legend i {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; border:2px solid; }}

.msg {{ display:grid; grid-template-columns: 14px 1fr; gap: 10px; padding: 9px 4px; border-bottom: 1px solid {C['line']}; }}
.msg .rail {{ width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; }}
.msg .hd {{ font: 12px var(--mono); color:{C['muted']}; }}
.msg .hd b {{ color:{C['text']}; font-weight:600; }}
.msg .bd {{ font-size: .88rem; color:{C['text']}; margin-top: 3px; line-height: 1.5; }}
.msg.dialogue {{ background: rgba(167,139,250,.08); border-left: 3px solid {C['violet']}; border-radius: 0 8px 8px 0;
  padding-left: 8px; margin: 4px 0; border-bottom: none; }}
.ty {{ font: 600 10px var(--mono); letter-spacing:.06em; padding: 2px 7px; border-radius: 4px; margin-right: 8px; }}
.ty-TASK {{ background: rgba(143,160,188,.14); color:{C['muted']}; }}
.ty-TELEMETRY {{ background: rgba(96,165,250,.14); color:#93C5FD; }}
.ty-FINDING {{ background: rgba(45,212,191,.13); color:{C['teal']}; }}
.ty-QUERY, .ty-RESPONSE {{ background: rgba(167,139,250,.18); color:{C['violet']}; }}
.ty-ERROR {{ background: rgba(248,113,113,.14); color:{C['crit']}; }}
.ty-REPORT {{ background: {C['text']}; color:{C['bg']}; }}
.verdict {{ font: 600 11px var(--mono); padding: 1px 7px; border-radius: 4px; margin-left: 6px; white-space: nowrap; }}
.verdict.sup {{ background: rgba(52,211,153,.14); color:{C['ok']}; }}
.verdict.ref {{ background: rgba(248,113,113,.14); color:{C['crit']}; }}

.tbl {{ width:100%; border-collapse: collapse; font-size: .84rem; }}
.tbl th {{ position: sticky; top: 0; background:{C['panel2']}; color:{C['muted']}; font-weight: 600; text-align:left;
  font-size: .72rem; letter-spacing:.06em; text-transform: uppercase; padding: 8px 10px; border-bottom:1px solid {C['line']}; }}
.tbl td {{ padding: 7px 10px; border-bottom: 1px solid {C['line']}; color:{C['text']}; vertical-align: top; }}
.tbl td.m {{ font-family: var(--mono); font-size: .8rem; }}
.tbl tr:hover td {{ background: rgba(45,212,191,.04); }}
.tblwrap {{ overflow-x: auto; border: 1px solid {C['line']}; border-radius: 10px; }}
.scroll {{ max-height: 300px; overflow: auto; border: 1px solid {C['line']}; border-radius: 10px; }}
.sev {{ font: 600 10px var(--mono); padding: 2px 7px; border-radius: 4px; text-transform: uppercase; letter-spacing:.05em; }}
.sev-critical, .sev-error {{ background: rgba(248,113,113,.15); color:{C['crit']}; }}
.sev-warning {{ background: rgba(251,191,36,.14); color:{C['warn']}; }}
.sev-normal {{ background: rgba(52,211,153,.13); color:{C['ok']}; }}
.sev-info {{ background: rgba(143,160,188,.14); color:{C['muted']}; }}
.qa {{ background: rgba(167,139,250,.08); border:1px solid rgba(167,139,250,.35); border-radius: 10px; padding: 10px 14px; margin: 10px 0; }}
.qa .q {{ font: 12px var(--mono); color:{C['violet']}; margin-bottom: 4px; }}
.qa .a {{ font-size: .88rem; color:{C['text']}; line-height: 1.5; }}
.call {{ border-left: 3px solid {C['teal']}; background: rgba(45,212,191,.07); padding: 9px 14px; border-radius: 0 10px 10px 0;
  font-size: .86rem; color:{C['text']}; margin: 10px 0; line-height: 1.5; }}
.call b {{ color:{C['teal']}; }}
.formula {{ font: 12.5px var(--mono); background:{C['bg']}; border:1px solid {C['line']}; border-radius: 10px; padding: 10px 14px;
  color:{C['text']}; margin: 10px 0; overflow-x: auto; white-space: nowrap; }}
.formula .r {{ color:{C['teal']}; font-weight: 600; }}
.sub {{ font-size: .74rem; letter-spacing: .08em; text-transform: uppercase; color:{C['muted']}; font-weight: 600; margin: 16px 0 6px; }}
.actions {{ margin: 0; padding-left: 20px; font-size: .88rem; color:{C['text']}; line-height: 1.65; }}
.stats {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom: 10px; }}
.empty {{ color:{C['muted']}; font-size: .88rem; padding: 18px 0; text-align:center; }}
.side-k {{ font-size: .7rem; letter-spacing: .1em; text-transform: uppercase; color:{C['muted']}; font-weight: 600; margin: 14px 0 4px; }}
@media (max-width: 1100px) {{ .nc-hero {{ grid-template-columns: 1fr; }} .kpis {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>""", unsafe_allow_html=True)


class Raw(str):
    """A pre-rendered table cell."""


def e(x):
    return html.escape(str(x if x is not None else ""))


def fmt(v, unit):
    if isinstance(v, float):
        v = round(v, 2)
    return f"{v}" if unit in UNITLESS else f"{v} {unit}"


def table(headers, rows, scroll=False):
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(c if isinstance(c, Raw) else f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in rows)
    out = f'<table class="tbl"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    return f'<div class="scroll">{out}</div>' if scroll else f'<div class="tblwrap">{out}</div>'


def td_value(res, ev):
    """Median value, plus the peak when several samples were received."""
    info = (((res["telemetry"] or {}).get("devices") or {}).get(ev.get("device_id")) or {}).get("metrics", {}).get(ev["metric"])
    peak = ""
    if info and info.get("samples", 1) > 1 and info.get("worst") != info.get("value"):
        peak = f'<div style="color:{C["muted"]};font-size:.72rem">peak {e(fmt(info["worst"], ev["unit"]))}</div>'
    return Raw(f'<td class="m" style="white-space:nowrap">{e(fmt(ev["value"], ev["unit"]))}{peak}</td>')


def td_mono(x):
    return Raw(f'<td class="m">{e(x)}</td>')


def td_sev(level):
    return Raw(f'<td><span class="sev sev-{e(level)}">{e(level)}</span></td>')


def panel_head(icon, title, sub=""):
    st.markdown(f'<div class="ph"><div class="t"><span>{icon}</span>{e(title)}</div><div class="s">{e(sub)}</div></div>',
                unsafe_allow_html=True)


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
        worst = max(worst, {"normal": 0, "warning": 1, "critical": 2}[sev], 1 if peak != "normal" else 0)
    return ["ok", "warn", "crit"][worst]


def topology_dot(res):
    r = res["report"]
    topo = ((res["telemetry"] or {}).get("topology") or {}).get("devices") or {}
    root = r["affected_devices"][0] if r["status"] == "diagnosed" and r["affected_devices"] else None
    affected = set(r["affected_devices"]) if r["status"] == "diagnosed" else set()
    out = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.28; ranksep=0.5; pad=0.2;',
           f'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 fontcolor="{C["text"]}" '
           f'fillcolor="{C["panel2"]}" margin="0.14,0.06" penwidth=1.6];',
           'edge [color="#2A3A5C" arrowhead=none penwidth=1.3];']
    for did, info in topo.items():
        h = health(res, did)
        # healthy devices stay quiet so that problems stand out, as on a real operations screen
        col = {"ok": "#2C4A55", "warn": C["warn"], "crit": C["crit"], "silent": C["silent"]}[h]
        font = C["muted"] if h in ("ok", "silent") else C["text"]
        style = '"rounded,filled,dashed"' if h == "silent" else '"rounded,filled"'
        kind = (info.get("type") or "") + (" · wifi" if info.get("medium") == "wireless" else "")
        if did == root:
            out.append(f'"{did}" [label="◆ {did}\\n{kind} · root cause" color="{C["teal"]}" penwidth=3.6 '
                       f'fillcolor="#0E3436" fontcolor="{C["teal"]}" fontname="Helvetica-Bold" style={style}];')
        else:
            pen = 1.2 if h == "ok" else 2.0
            out.append(f'"{did}" [label="{did}\\n{kind}" color="{col}" fontcolor="{font}" penwidth={pen} style={style}];')
    for did, info in topo.items():
        up = info.get("upstream")
        if up and up in topo:
            hot = did in affected and (up in affected or up == root)
            out.append(f'"{up}" -> "{did}"' + (f' [color="{C["crit"]}" penwidth=2.6]' if hot else "") + ";")
    out.append("}")
    return "\n".join(out)


def gauge(conf, category, status):
    col = {"high": C["teal"], "medium": C["violet"], "low": C["warn"]}.get(category, C["silent"])
    if status == "healthy":
        col = C["ok"]
    r, circ = 52, 2 * 3.14159 * 52
    dash = circ * max(0.0, min(1.0, conf))
    return (f'<div class="gauge"><svg width="150" height="150" viewBox="0 0 150 150">'
            f'<circle cx="75" cy="75" r="{r}" fill="none" stroke="{C["line"]}" stroke-width="11"/>'
            f'<circle cx="75" cy="75" r="{r}" fill="none" stroke="{col}" stroke-width="11" stroke-linecap="round" '
            f'stroke-dasharray="{dash:.1f} {circ:.1f}" transform="rotate(-90 75 75)"/>'
            f'<text x="75" y="80" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="30" '
            f'font-weight="600" fill="{C["text"]}">{conf:.2f}</text>'
            f'<text x="75" y="102" text-anchor="middle" font-family="Inter, sans-serif" font-size="10.5" '
            f'fill="{C["muted"]}" letter-spacing="1.5">CONFIDENCE</text></svg>'
            f'<div class="cat" style="color:{col}">{e(category)}</div></div>')


def message_row(m):
    p = m.get("payload") or {}
    t = m["type"]
    body = {
        "TASK": lambda: f"Task: <b>{e(p.get('action'))}</b>",
        "TELEMETRY": lambda: e(p.get("summary", "")),
        "FINDING": lambda: f"<b>{e(p.get('status', '').replace('_', ' '))}</b> · {e(p.get('summary', ''))}",
        "QUERY": lambda: f"<b>{e(p.get('question'))}</b> · {e(p.get('reason', ''))}",
        "RESPONSE": lambda: e(p.get("detail", ""))
        + (f'<span class="verdict sup">supports {e(p["supports"])}</span>' if p.get("supports") else "")
        + (f'<span class="verdict ref">against {e(p["refutes"])}</span>' if p.get("refutes") else ""),
        "ERROR": lambda: f"<b>{e(p.get('code'))}</b> · {e(p.get('message', ''))}",
        "REPORT": lambda: "Final report delivered to the operator",
    }.get(t, lambda: "")()
    note = (p.get("context") or {}).get("protocol_note")
    note = f' · <span style="color:{C["warn"]}">{e(note)}</span>' if note else ""
    color = AGENT_COLOR.get(m["sender"], C["muted"])
    cls = "msg dialogue" if t in ("QUERY", "RESPONSE") else "msg"
    return (f'<div class="{cls}"><div class="rail" style="background:{color}"></div><div>'
            f'<div class="hd"><span class="ty ty-{t}">{t}</span><b>{e(m["sender"])}</b> → {e(m["recipient"])}{note}</div>'
            f'<div class="bd">{body}</div></div></div>')


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown(f'<div class="nc-brand"><svg width="30" height="30" viewBox="0 0 30 30"><circle cx="15" cy="6" r="3.2" fill="{C["teal"]}"/>'
                f'<circle cx="6" cy="22" r="3.2" fill="{C["violet"]}"/><circle cx="24" cy="22" r="3.2" fill="{C["violet"]}"/>'
                f'<path d="M15 6 L6 22 L24 22 Z" fill="none" stroke="{C["teal"]}" stroke-width="1.4" opacity=".7"/></svg>'
                f'<div><div class="name">NetCoop AI</div><div class="tag">Diagnosis console</div></div></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="side-k">Telemetry input</div>', unsafe_allow_html=True)
    source = st.radio("Input", ["Bundled scenario", "Paste or upload telemetry"], label_visibility="collapsed")
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
    st.markdown('<div class="side-k">Agent behaviour</div>', unsafe_allow_html=True)
    follow_up = st.toggle("Agents ask follow-up questions", value=True, key="follow_up",
                          help="Off = the correlation agent decides from the first findings only (the ablation baseline).")
    crash = st.selectbox("Simulate an agent crash", key="crash",
                         options=["none", "performance_agent", "infrastructure_agent", "connectivity_agent"])
    from netcoop import llm as llm_mod
    use_llm = st.checkbox("LLM plain-English summary", value=False, disabled=not llm_mod.is_configured(),
                          help="Needs LLM_API_KEY or GROQ_API_KEY in .env; the diagnosis never depends on it.")
    st.markdown('<div class="side-k">About</div>', unsafe_allow_html=True)
    st.caption("Six agents exchange schema-validated netcoop-msg/1.0 messages. The diagnosis is deterministic "
               "and runs offline; the LLM only rewrites the summary.")

if not raw:
    st.markdown('<div class="empty">Paste or upload telemetry in the sidebar to start a diagnosis.</div>',
                unsafe_allow_html=True)
    st.stop()

res = run(raw, [] if crash == "none" else [crash], follow_up, use_llm)
r = res["report"]
dq = r.get("data_quality") or {}
queries = r.get("inter_agent_queries") or []

# ---------------------------------------------------------------- top bar + hero
crash_chip = f'<span class="chip">crashed <b>{e(crash)}</b></span>' if crash != "none" else ""
st.markdown(
    f'<div class="nc-top"><div class="nc-brand"><div><div class="name">Network diagnosis</div>'
    f'<div class="tag">Specialist agents share evidence, question each other, and agree on one explainable answer</div></div></div>'
    f'<div class="nc-chips"><span class="chip">incident <b>{e(r["incident_id"])}</b></span>'
    f'<span class="chip"><b>{res["elapsed_ms"]}</b> ms</span>'
    f'<span class="chip">follow-ups <b>{"on" if follow_up else "off"}</b></span>{crash_chip}</div></div>',
    unsafe_allow_html=True)

root = r["affected_devices"][0] if r["status"] == "diagnosed" and r["affected_devices"] else None
devs = "".join(f'<span class="dev {"root" if d == root else ""}">{e(d)}</span>' for d in r.get("affected_devices") or [])
domain = f'<span class="chip">fault domain <b>{e(r["fault_domain"])}</b></span>' if r.get("fault_domain") else ""
dev_row = f'<div class="nc-dev">{devs}{domain}</div>' if devs or domain else ""
st.markdown(
    f'<div class="nc-hero"><div><span class="pill st-{e(r["status"])}"><span class="dot"></span>'
    f'{e(r["status"].replace("_", " "))}</span>'
    f'<div class="nc-cause">{e(r["suspected_root_cause"])}</div>'
    f'<div class="nc-sum">{e(r.get("summary_llm") or r.get("summary", ""))}</div>{dev_row}</div>'
    f'{gauge(r["confidence"], r.get("confidence_category", ""), r["status"])}</div>',
    unsafe_allow_html=True)

st.markdown(
    f'<div class="kpis" data-followups="{len(queries)}" data-confidence="{r["confidence"]}">'
    f'<div class="kpi"><div class="l">Agent messages</div><div class="v">{len(res["messages"])}</div>'
    f'<div class="s">validated against netcoop-msg/1.0</div></div>'
    f'<div class="kpi accent"><div class="l">Follow-up questions</div><div class="v">{len(queries)}</div>'
    f'<div class="s">{"asked between agents" if follow_up else "switched off (baseline)"}</div></div>'
    f'<div class="kpi"><div class="l">Data quality</div><div class="v">{e(dq.get("score", "–"))}</div>'
    f'<div class="s">{dq.get("fields_translated", 0)} fields translated</div></div>'
    f'<div class="kpi"><div class="l">Rejected messages</div><div class="v">{len(res["rejected_messages"])}</div>'
    f'<div class="s">failed the protocol check</div></div></div>',
    unsafe_allow_html=True)

# ---------------------------------------------------------------- four panes
top_left, top_right = st.columns([1.12, 1], gap="medium")
with top_left:
    with st.container(border=True):
        n_dev = len(((res["telemetry"] or {}).get("topology") or {}).get("devices") or {})
        panel_head("◇", "Network map", f"{n_dev} devices")
        if res["telemetry"]:
            st.graphviz_chart(topology_dot(res), width="stretch")
            st.markdown(
                f'<div class="legend"><span><i style="border-color:#2C4A55"></i>healthy</span>'
                f'<span><i style="border-color:{C["warn"]}"></i>warning</span>'
                f'<span><i style="border-color:{C["crit"]}"></i>critical</span>'
                f'<span><i style="border-color:{C["silent"]};border-style:dashed"></i>no telemetry</span>'
                f'<span><i style="border-color:{C["teal"]};background:#0F2A2E"></i>root cause</span>'
                f'<span><i style="border-color:{C["crit"]};width:16px;height:0;border-width:2px 0 0;border-radius:0"></i>'
                f'affected path</span></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="empty">{e(r["suspected_root_cause"])}</div>', unsafe_allow_html=True)
with top_right:
    with st.container(border=True):
        panel_head("⇄", "Agent conversation", f"{len(res['messages'])} messages · {len(queries)} follow-ups")
        only_dialogue = st.toggle("Show only follow-up questions (QUERY / RESPONSE)", value=False, key="only_dialogue")
        shown = [m for m in res["messages"] if not only_dialogue or m["type"] in ("QUERY", "RESPONSE")]
        with st.container(height=360, border=False):
            st.markdown("".join(message_row(m) for m in shown)
                        or '<div class="empty">No follow-up questions in this run.</div>', unsafe_allow_html=True)
        with st.expander("Raw netcoop-msg/1.0 messages (JSON)"):
            st.json(res["messages"], expanded=False)

bottom_left, bottom_right = st.columns([1.12, 1], gap="medium")
with bottom_left:
    with st.container(border=True):
        panel_head("✓", "Evidence and reasoning",
                   f"{len(r['supporting_evidence'])} readings · {len(r.get('evidence_sources') or [])} sources")
        if r["supporting_evidence"]:
            st.markdown(table(["Reading", "Value", "Severity", "Interpretation"],
                              [[Raw(f'<td class="m"><b>{e(x["device_id"])}</b><div style="color:{C["muted"]}">{e(x["metric"])}</div></td>'),
                                td_value(res, x), td_sev(x["severity"]),
                                Raw(f'<td>{e(x["interpretation"].split(" (")[0].split(": peaked")[0])}'
                                    f'<div style="color:{C["muted"]};font-size:.74rem">reported by '
                                    f'{e((x.get("source") or "").replace("_agent", " agent"))}</div></td>')]
                               for x in r["supporting_evidence"]]), unsafe_allow_html=True)
        if queries:
            st.markdown('<div class="sub">Follow-up questions between agents</div>', unsafe_allow_html=True)
            for q in queries:
                st.markdown(f'<div class="qa"><div class="q">{e(q["from"])} → {e(q["to"])} · {e(q["question"])} on '
                            f'{e(q["device"])}</div><div class="a">{e(q["answer"])}</div></div>', unsafe_allow_html=True)
        if r.get("counterfactual"):
            st.markdown(f'<div class="call"><b>What would change the answer.</b> {e(r["counterfactual"])}</div>',
                        unsafe_allow_html=True)
        m = r.get("confidence_method")
        if m:
            st.markdown(f'<div class="sub">Confidence calculation</div><div class="formula">{m["top_score"]} × (0.65 + 0.35 × '
                        f'{m["margin"]}) × (0.5 + 0.5 × {m["data_quality"]}) × {m["missing_agent_penalty"]} = '
                        f'<span class="r">{r["confidence"]}</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="sub">Recommended actions</div><ol class="actions">'
                    + "".join(f"<li>{e(a)}</li>" for a in r["recommended_actions"]) + "</ol>", unsafe_allow_html=True)
        if r.get("conflicts_resolved"):
            with st.expander(f"Conflicts resolved ({len(r['conflicts_resolved'])})"):
                for c in r["conflicts_resolved"]:
                    st.markdown(f"**{' vs '.join(c['between'])}**: {c['resolution']}")
        if r.get("alternatives_detail"):
            with st.expander(f"Alternatives considered ({len(r['alternatives_detail'])})"):
                st.markdown(table(["Cause", "Device", "Score", "Why less likely"],
                                  [[td_mono(a["cause"]), td_mono(a["device"]), td_mono(f'{a["score"]:.2f}'), a["why_less_likely"]]
                                   for a in r["alternatives_detail"]]), unsafe_allow_html=True)
        if r.get("early_warnings"):
            with st.expander(f"Early warnings ({len(r['early_warnings'])})"):
                st.markdown("\n".join(f"- {w}" for w in r["early_warnings"]))
        if r["limitations"]:
            with st.expander(f"Limitations ({len(r['limitations'])})", expanded=r["status"] != "diagnosed"):
                st.markdown("\n".join(f"- {x}" for x in r["limitations"]))
with bottom_right:
    with st.container(border=True):
        q = (res["telemetry"] or {}).get("quality")
        panel_head("⟲", "Translation log", "incoming fields → canonical schema")
        if not q:
            st.markdown(f'<div class="qa" style="border-color:{C["crit"]}"><div class="a">'
                        f'{e(r["limitations"][0] if r["limitations"] else "input could not be parsed")}</div></div>',
                        unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="stats"><span class="chip">records <b>{q["records_accepted"]}/{q["records_total"]}</b></span>'
                        f'<span class="chip">translated <b>{len(q["transformations"])}</b></span>'
                        f'<span class="chip">issues <b>{len(q["issues"])}</b></span>'
                        f'<span class="chip">quality <b>{q["score"]}</b></span></div>', unsafe_allow_html=True)
            if q["transformations"]:
                rows = []
                for t in q["transformations"]:
                    raw_v = json.dumps(t["raw"]) if isinstance(t["raw"], (dict, list)) else str(t["raw"])
                    rows.append([
                        Raw(f'<td class="m"><b>{e(t["field"])}</b><div style="color:{C["muted"]}">{e(t["device_id"])}</div></td>'),
                        Raw(f'<td class="m"><span style="color:{C["muted"]}">{e(raw_v)}</span> '
                            f'<span style="color:{C["teal"]}">→</span> <b>{e(fmt(t["value"], t["unit"]))}</b>'
                            f'<div style="color:{C["muted"]}">{e(t["canonical"])}</div></td>'),
                        t["rule"]])
                st.markdown(table(["Incoming field", "Value → canonical", "Rule"], rows, scroll=True),
                            unsafe_allow_html=True)
            else:
                st.markdown('<div class="call"><b>Already canonical.</b> Every field used standard names and units; '
                            'nothing needed translating.</div>', unsafe_allow_html=True)
            if q["issues"]:
                st.markdown('<div class="sub">Validation issues</div>', unsafe_allow_html=True)
                st.markdown(table(["Level", "Code", "Detail"],
                                  [[td_sev(i["level"]), td_mono(i["code"]), i["message"]] for i in q["issues"]], scroll=True),
                            unsafe_allow_html=True)
        for rej in res["rejected_messages"]:
            st.markdown(f'<div class="sub">Rejected message</div><div class="qa" style="border-color:{C["crit"]};'
                        f'background:rgba(248,113,113,.07)"><div class="q" style="color:{C["crit"]}">from '
                        f'{e(rej["message"].get("sender", "?"))}</div><div class="a">'
                        + "<br>".join(f"<code>{e(p)}</code>" for p in rej["problems"]) + "</div></div>",
                        unsafe_allow_html=True)

st.download_button("⬇  Download JSON report", json.dumps(r, indent=2), file_name=f"{r['incident_id']}.json",
                   mime="application/json")
