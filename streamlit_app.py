"""Streamlit dashboard: the same engine as server.py, styled as an animated network operations console.

    python -m streamlit run streamlit_app.py

Four panes: network map | agent conversation (QUERY/RESPONSE highlighted) | evidence | translation log.
A switch turns the agents' follow-up questions off, to show live what they add.
"""

import html
import json
from collections import defaultdict
from pathlib import Path
from string import Template

import streamlit as st

from netcoop import knowledge
from netcoop.orchestrator import diagnose

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "data" / "scenarios"
MANIFEST = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
UNITLESS = ("count", "state", "bool", "ratio")

# "Aurora NOC": deep night navy with a drifting teal / violet / pink aurora. Each pane has its own
# accent (map teal, conversation violet, evidence pink, translation sky); green / amber / red are
# reserved for device health so a colour never means two things.
C = {"bg": "#060A17", "panel": "#0E1628", "panel2": "#131F38", "line": "#22304D", "text": "#E8EEF9",
     "muted": "#8FA0BC", "teal": "#2DD4BF", "violet": "#8B5CF6", "lilac": "#A78BFA", "pink": "#F472B6",
     "sky": "#60A5FA", "ok": "#34D399", "warn": "#FBBF24", "crit": "#F87171", "silent": "#64748B",
     "okline": "#2C4A55"}
AGENTS = [("ingestion_agent", "Ingestion", C["sky"]), ("performance_agent", "Performance", C["teal"]),
          ("infrastructure_agent", "Infrastructure", "#F59E0B"), ("connectivity_agent", "Connectivity", C["pink"]),
          ("correlation_agent", "Correlation", C["lilac"]), ("reporting_agent", "Reporting", C["text"])]
AGENT_COLOR = {a: c for a, _, c in AGENTS} | {"orchestrator": C["muted"], "message_bus": C["crit"]}

st.set_page_config(page_title="NetCoop AI · Network diagnosis console", page_icon="◈", layout="wide",
                   initial_sidebar_state="expanded")

CSS = Template("""<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');
:root { --mono: 'JetBrains Mono', ui-monospace, Consolas, monospace; --disp: 'Space Grotesk', 'Inter', sans-serif; }
@property --num { syntax: '<integer>'; initial-value: 0; inherits: false; }
html, body, [class*="css"], .stMarkdown, .stText { font-family: 'Inter', system-ui, 'Segoe UI', sans-serif; }

/* aurora background: three soft colour fields drifting slowly over a dot grid */
.stApp { background:
    radial-gradient(1px 1px at 1px 1px, rgba(148,163,184,.10) 1px, transparent 0) 0 0 / 22px 22px,
    radial-gradient(60% 50% at 10% 10%, rgba(45,212,191,.16), transparent 60%),
    radial-gradient(55% 45% at 90% 5%, rgba(139,92,246,.20), transparent 60%),
    radial-gradient(50% 50% at 70% 100%, rgba(244,114,182,.12), transparent 60%),
    $bg;
  background-size: 22px 22px, 160% 160%, 160% 160%, 160% 160%, auto;
  animation: aurora 26s ease-in-out infinite alternate; }
@keyframes aurora {
  0%   { background-position: 0 0, 0% 0%, 100% 0%, 50% 100%, 0 0; }
  100% { background-position: 0 0, 30% 20%, 70% 30%, 20% 70%, 0 0; } }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding: 1.1rem 2rem 3rem; max-width: 1580px; }
section[data-testid="stSidebar"] { background: rgba(9,14,30,.82); backdrop-filter: blur(14px); border-right: 1px solid $line; }
section[data-testid="stSidebar"] .block-container { padding-top: 1.4rem; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: rgba(14,22,40,.66); backdrop-filter: blur(12px);
  border: 1px solid $line !important; border-radius: 16px !important; transition: border-color .3s, box-shadow .3s; }
div[data-testid="stVerticalBlockBorderWrapper"]:hover { border-color: #2E4268 !important; box-shadow: 0 10px 40px rgba(0,0,0,.35); }
code { font-family: var(--mono) !important; }

/* brand + header */
.brand { display:flex; gap:12px; align-items:center; }
.brand .name { font: 700 1.3rem var(--disp); letter-spacing: -.01em;
  background: linear-gradient(90deg, $teal, $lilac, $pink, $teal); background-size: 300% 100%;
  -webkit-background-clip: text; background-clip: text; color: transparent; animation: shimmer 9s linear infinite; }
@keyframes shimmer { to { background-position: 300% 0; } }
.brand .tag { font-size: .8rem; color:$muted; }
.logo circle { animation: blink 2.4s ease-in-out infinite; }
.logo circle:nth-child(2) { animation-delay: .4s; } .logo circle:nth-child(3) { animation-delay: .8s; }
@keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
.top { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom: 14px; }
.top h1 { font: 700 1.55rem var(--disp); color:$text; margin: 0; letter-spacing: -.02em; }
.top .tag { font-size: .84rem; color:$muted; margin-top: 2px; }
.chips { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; align-items:center; }
.chip { font: 500 12px var(--mono); color:$muted; background: rgba(19,31,56,.8); border:1px solid $line;
  padding: 4px 10px; border-radius: 999px; white-space: nowrap; }
.chip b { color:$text; font-weight: 600; }
.live { display:inline-flex; align-items:center; gap:7px; font: 600 11px var(--mono); letter-spacing: .1em; color:$ok;
  padding: 4px 11px; border-radius: 999px; background: rgba(52,211,153,.10); border: 1px solid rgba(52,211,153,.3); }
.live i { width:7px; height:7px; border-radius:50%; background:$ok; box-shadow: 0 0 0 0 rgba(52,211,153,.7);
  animation: ping 1.8s ease-out infinite; }
@keyframes ping { 0% { box-shadow: 0 0 0 0 rgba(52,211,153,.7); } 100% { box-shadow: 0 0 0 9px rgba(52,211,153,0); } }

/* hero card with a flowing gradient border and a light sweep */
.hero { position: relative; overflow: hidden; display:grid; grid-template-columns: 1fr 200px; gap: 26px; align-items:center;
  border: 1.5px solid transparent; border-radius: 18px; padding: 24px 28px;
  background: linear-gradient(180deg, rgba(19,31,56,.94), rgba(14,22,40,.94)) padding-box,
              linear-gradient(115deg, $teal, $violet, $pink, $teal) border-box;
  background-size: 100% 100%, 300% 300%; animation: border-flow 10s linear infinite, rise .6s ease both; }
@keyframes border-flow { 0% { background-position: 0 0, 0% 50%; } 100% { background-position: 0 0, 300% 50%; } }
.hero::after { content:""; position:absolute; inset:0; pointer-events:none;
  background: linear-gradient(100deg, transparent 30%, rgba(255,255,255,.05) 50%, transparent 70%);
  transform: translateX(-100%); animation: sweep 7s ease-in-out infinite; }
@keyframes sweep { 0%, 60% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
.pill { display:inline-flex; align-items:center; gap:7px; font: 600 11px var(--mono); letter-spacing:.1em;
  text-transform: uppercase; padding: 4px 11px; border-radius: 999px; }
.pill .dot { width:7px; height:7px; border-radius:50%; background: currentColor; animation: blink 1.6s ease-in-out infinite; }
.st-diagnosed { color:$warn; background: rgba(251,191,36,.12); }
.st-healthy { color:$ok; background: rgba(52,211,153,.12); }
.st-insufficient_evidence { color:$lilac; background: rgba(167,139,250,.14); }
.st-error { color:$crit; background: rgba(248,113,113,.12); }
.cause { font: 650 1.75rem/1.22 var(--disp); color:$text; margin: 12px 0 8px; letter-spacing: -.015em; }
.sum { color:$muted; font-size: .93rem; line-height: 1.6; max-width: 1060px; }
.devrow { display:flex; gap:6px; flex-wrap:wrap; margin-top: 13px; align-items:center; }
.dev { font: 500 12px var(--mono); padding: 3px 9px; border-radius: 6px; background: rgba(14,22,40,.9);
  border:1px solid $line; color:$text; }
.dev.root { border-color:$teal; color:$teal; box-shadow: 0 0 14px rgba(45,212,191,.35); }
.gauge { text-align:center; }
.gauge .cat { font: 600 11px var(--mono); letter-spacing:.12em; text-transform:uppercase; margin-top: 2px; }
.ring { animation: ring 1.6s cubic-bezier(.2,.8,.2,1) both; }
@keyframes ring { from { stroke-dasharray: 0 400; } }

/* KPI tiles, each with its own accent, counters that count up */
.kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 16px 0 18px; }
.kpi { position:relative; overflow:hidden; background: rgba(14,22,40,.7); backdrop-filter: blur(10px);
  border:1px solid $line; border-radius: 14px; padding: 15px 17px; transition: transform .25s, box-shadow .25s;
  animation: rise .55s ease both; }
.kpi:hover { transform: translateY(-3px); box-shadow: 0 12px 30px rgba(0,0,0,.35); }
.kpi::before { content:""; position:absolute; left:0; right:0; top:0; height:2px; background: var(--acc); opacity:.9; }
.kpi .l { font-size: .74rem; color:$muted; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 6px; }
.kpi .v { font: 600 1.75rem var(--mono); color: var(--acc); line-height: 1.1; }
.kpi .s { font-size: .77rem; color:$muted; margin-top: 4px; }
.count { animation: count 1.3s ease-out both; counter-reset: num var(--num); }
.count::after { content: counter(num); }
@keyframes count { from { --num: 0; } to { --num: var(--target); } }

/* pane headers: icon + title + accent underline in the pane's colour */
.ph { display:flex; justify-content:space-between; align-items:center; gap: 10px; padding-bottom: 9px; margin: 2px 0 12px;
  border-bottom: 1px solid $line; position: relative; }
.ph::after { content:""; position:absolute; left:0; bottom:-1px; width: 90px; height: 2px; background: var(--acc);
  border-radius: 2px; box-shadow: 0 0 12px var(--acc); }
.ph .t { font: 600 1rem var(--disp); color:$text; display:flex; align-items:center; gap: 9px; }
.ph .ic { width: 26px; height: 26px; border-radius: 8px; display:grid; place-items:center; font-size: 13px;
  color: var(--acc); background: color-mix(in srgb, var(--acc) 16%, transparent); }
.ph .s { font: 500 11.5px var(--mono); color:$muted; text-align: right; }
.legend { display:flex; gap:14px; flex-wrap:wrap; font-size:.75rem; color:$muted; margin-top: 6px; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; border:2px solid; }

/* topology map */
.topo svg { width: 100%; height: auto; display:block; }
.topo text { font-family: var(--mono); }
.node { animation: pop .55s cubic-bezier(.2,.9,.3,1.3) both; transform-box: fill-box; transform-origin: center; }
@keyframes pop { from { opacity: 0; transform: scale(.6); } }
.flow { fill: none; stroke-dasharray: 2 12; stroke-linecap: round; animation: flow 2.6s linear infinite; }
.flow.hot { stroke-dasharray: 6 9; animation-duration: .85s; }
@keyframes flow { to { stroke-dashoffset: -28; } }
.halo { fill: none; transform-box: fill-box; transform-origin: center; animation: halo 1.9s ease-out infinite; }
.halo.h2 { animation-delay: .95s; }
@keyframes halo { 0% { opacity: .9; transform: scale(1); } 100% { opacity: 0; transform: scale(1.28, 1.75); } }
.node.crit .box { animation: critglow 1.5s ease-in-out infinite; }
@keyframes critglow { 0%,100% { stroke-opacity: 1; } 50% { stroke-opacity: .35; } }
.node:hover .box { stroke-width: 2.6; }

/* agent roster + conversation */
.roster { display:flex; gap:6px; flex-wrap:wrap; margin: 0 0 10px; }
.ag { display:inline-flex; align-items:center; gap:6px; font: 500 11.5px var(--mono); color:$text;
  padding: 3px 9px; border-radius: 999px; background: rgba(19,31,56,.8); border:1px solid $line; }
.ag i { width:7px; height:7px; border-radius:50%; background: var(--c); box-shadow: 0 0 8px var(--c); animation: blink 2s ease-in-out infinite; }
.ag b { color:$muted; font-weight: 500; }
.ag.down { opacity: .55; text-decoration: line-through; }
.ag.down i { background:$crit; box-shadow: none; animation: none; }
.msg { display:grid; grid-template-columns: 14px 1fr; gap: 10px; padding: 9px 4px; border-bottom: 1px solid $line;
  opacity: 0; animation: rise .45s ease forwards; }
@keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.msg .rail { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; box-shadow: 0 0 8px currentColor; }
.msg .hd { font: 12px var(--mono); color:$muted; }
.msg .hd b { color:$text; font-weight: 600; }
.msg .bd { font-size: .88rem; color:$text; margin-top: 3px; line-height: 1.5; }
.msg.dialogue { background: linear-gradient(90deg, rgba(139,92,246,.16), rgba(139,92,246,.03)); border-left: 3px solid $violet;
  border-radius: 0 10px 10px 0; padding-left: 9px; margin: 5px 0; border-bottom: none; box-shadow: 0 0 22px rgba(139,92,246,.12); }
.ty { font: 600 10px var(--mono); letter-spacing:.06em; padding: 2px 7px; border-radius: 4px; margin-right: 8px; }
.ty-TASK { background: rgba(143,160,188,.14); color:$muted; }
.ty-TELEMETRY { background: rgba(96,165,250,.15); color:#93C5FD; }
.ty-FINDING { background: rgba(45,212,191,.14); color:$teal; }
.ty-QUERY, .ty-RESPONSE { background: rgba(139,92,246,.24); color:#C4B5FD; }
.ty-ERROR { background: rgba(248,113,113,.15); color:$crit; }
.ty-REPORT { background: linear-gradient(90deg, $teal, $violet); color: #060A17; }
.verdict { font: 600 11px var(--mono); padding: 1px 7px; border-radius: 4px; margin-left: 6px; white-space: nowrap; }
.verdict.sup { background: rgba(52,211,153,.15); color:$ok; }
.verdict.ref { background: rgba(248,113,113,.15); color:$crit; }

/* tables, callouts */
.tbl { width:100%; border-collapse: collapse; font-size: .84rem; }
.tbl th { position: sticky; top: 0; background:$panel2; color:$muted; font-weight: 600; text-align:left;
  font-size: .7rem; letter-spacing:.08em; text-transform: uppercase; padding: 8px 10px; border-bottom:1px solid $line; }
.tbl td { padding: 8px 10px; border-bottom: 1px solid $line; color:$text; vertical-align: top; }
.tbl td.m { font-family: var(--mono); font-size: .8rem; }
.tbl tr { animation: rise .4s ease both; }
.tbl tr:hover td { background: rgba(139,92,246,.06); }
.tblwrap { overflow-x: auto; border: 1px solid $line; border-radius: 12px; }
.scroll { max-height: 300px; overflow: auto; border: 1px solid $line; border-radius: 12px; }
.sev { font: 600 10px var(--mono); padding: 2px 7px; border-radius: 4px; text-transform: uppercase; letter-spacing:.05em; }
.sev-critical, .sev-error { background: rgba(248,113,113,.16); color:$crit; }
.sev-warning { background: rgba(251,191,36,.15); color:$warn; }
.sev-normal { background: rgba(52,211,153,.14); color:$ok; }
.sev-info { background: rgba(143,160,188,.14); color:$muted; }
.qa { background: linear-gradient(135deg, rgba(139,92,246,.14), rgba(244,114,182,.06)); border:1px solid rgba(139,92,246,.4);
  border-radius: 12px; padding: 10px 14px; margin: 10px 0; animation: rise .5s ease both; }
.qa .q { font: 12px var(--mono); color:#C4B5FD; margin-bottom: 4px; }
.qa .a { font-size: .88rem; color:$text; line-height: 1.5; }
.call { border-left: 3px solid $pink; background: rgba(244,114,182,.07); padding: 9px 14px; border-radius: 0 12px 12px 0;
  font-size: .86rem; color:$text; margin: 10px 0; line-height: 1.5; }
.call b { color:$pink; }
.call.sky { border-color:$sky; background: rgba(96,165,250,.07); } .call.sky b { color:$sky; }
.formula { font: 12.5px var(--mono); background: rgba(6,10,23,.8); border:1px solid $line; border-radius: 10px; padding: 10px 14px;
  color:$text; margin: 10px 0; overflow-x: auto; white-space: nowrap; }
.formula .r { color:$teal; font-weight: 600; text-shadow: 0 0 10px rgba(45,212,191,.5); }
.sub { font-size: .72rem; letter-spacing: .1em; text-transform: uppercase; color:$muted; font-weight: 600; margin: 16px 0 6px; }
.actions { margin: 0; padding-left: 20px; font-size: .88rem; color:$text; line-height: 1.7; }
.actions li::marker { color:$pink; font-weight: 700; }
.stats { display:flex; gap:8px; flex-wrap:wrap; margin-bottom: 10px; }
.empty { color:$muted; font-size: .88rem; padding: 18px 0; text-align:center; }
.side-k { display:flex; align-items:center; gap:8px; font-size: .68rem; letter-spacing: .12em; text-transform: uppercase;
  color:$muted; font-weight: 600; margin: 16px 0 4px; }
.side-k::before { content:""; width: 14px; height: 2px; border-radius: 2px; background: linear-gradient(90deg, $teal, $violet); }

@media (max-width: 1100px) { .hero { grid-template-columns: 1fr; } .kpis { grid-template-columns: repeat(2, 1fr); } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  .msg { opacity: 1; } }
</style>""").substitute(C)
st.markdown(CSS, unsafe_allow_html=True)


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
    body = "".join(f'<tr style="animation-delay:{min(i * 50, 900)}ms">'
                   + "".join(c if isinstance(c, Raw) else f"<td>{e(c)}</td>" for c in r) + "</tr>"
                   for i, r in enumerate(rows))
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


def panel_head(icon, title, sub, accent):
    st.markdown(f'<div class="ph" style="--acc:{accent}"><div class="t"><span class="ic">{icon}</span>{e(title)}</div>'
                f'<div class="s">{e(sub)}</div></div>', unsafe_allow_html=True)


def counter(n):
    return f'<span class="count" style="--num:{int(n)};--target:{int(n)}"></span>'


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


def tooltip(res, device_id, h):
    dev = ((res["telemetry"] or {}).get("devices") or {}).get(device_id)
    if not dev:
        return f"{device_id}: no telemetry received"
    bad = []
    for metric, info in dev["metrics"].items():
        sev = knowledge.assess(metric, info["value"])[0]
        if sev != "normal" or knowledge.assess(metric, info["worst"])[0] != "normal":
            bad.append(f"{metric} {fmt(info['value'], info['unit'])} (peak {fmt(info['worst'], info['unit'])})")
    status = dev.get("status") or "no status"
    return f"{device_id} · {status}" + ("\n" + "\n".join(bad[:5]) if bad else "\nall readings normal")


def topology_svg(res):
    """Hand-laid-out tree with animated packet flow; the root cause pulses like a radar ping."""
    r = res["report"]
    topo = ((res["telemetry"] or {}).get("topology") or {}).get("devices") or {}
    if not topo:
        return None
    root = r["affected_devices"][0] if r["status"] == "diagnosed" and r["affected_devices"] else None
    affected = set(r["affected_devices"]) if r["status"] == "diagnosed" else set()
    parent = {d: (i.get("upstream") if i.get("upstream") in topo else None) for d, i in topo.items()}
    children = defaultdict(list)
    for d in topo:
        if parent[d]:
            children[parent[d]].append(d)
    roots = [d for d in topo if not parent[d]]
    pos, depth, slot, seen = {}, {}, [0], set()

    def place(d, level):
        seen.add(d)
        depth[d] = level
        kids = [k for k in children[d] if k not in seen]
        if not kids:
            pos[d] = slot[0]
            slot[0] += 1
            return
        for k in kids:
            place(k, level + 1)
        pos[d] = sum(pos[k] for k in kids) / len(kids)

    for d in roots:
        place(d, 0)
    W, H, GX, GY, PAD, TOP = 128, 42, 142, 88, 16, 34
    if max(depth.values(), default=0) == 0 and len(topo) > 5:      # no topology: lay devices out in a grid
        cols = 4
        for i, d in enumerate(topo):
            pos[d], depth[d] = i % cols, i // cols
        slot[0] = min(cols, len(topo))
    width = PAD * 2 + (slot[0] - 1) * GX + W
    height = TOP + max(depth.values(), default=0) * GY + H + PAD
    xy = {d: (PAD + pos[d] * GX, TOP + depth[d] * GY) for d in topo}

    edges, nodes = [], []
    for d in topo:
        p = parent[d]
        if not p:
            continue
        (px, py), (cx, cy) = xy[p], xy[d]
        x1, y1, x2, y2 = px + W / 2, py + H, cx + W / 2, cy
        path = f"M{x1:.0f},{y1:.0f} C{x1:.0f},{y1 + GY / 2:.0f} {x2:.0f},{y2 - GY / 2:.0f} {x2:.0f},{y2:.0f}"
        hot = d in affected and (p in affected or p == root)
        silent = "silent" in (health(res, d), health(res, p))
        edges.append(f'<path d="{path}" fill="none" stroke="{C["crit"] if hot else "#1E2B47"}" '
                     f'stroke-width="{2.2 if hot else 1.4}" stroke-opacity="{0.55 if hot else 1}"/>')
        if not silent:
            edges.append(f'<path class="flow{" hot" if hot else ""}" d="{path}" stroke="{C["crit"] if hot else C["teal"]}" '
                         f'stroke-width="{2.6 if hot else 1.8}" stroke-opacity="{1 if hot else 0.45}"/>')
    for d, info in topo.items():
        x, y = xy[d]
        h = health(res, d)
        kind = (info.get("type") or "") + (" · wifi" if info.get("medium") == "wireless" else "")
        label = d if len(d) <= 17 else d[:16] + "…"
        fill, stroke, txt, sw, dash = {
            "ok": ("#0D1830", C["okline"], C["muted"], 1.2, ""),
            "warn": ("rgba(251,191,36,.10)", C["warn"], C["text"], 1.8, ""),
            "crit": ("rgba(248,113,113,.12)", C["crit"], C["text"], 2.0, ""),
            "silent": ("#0B1222", C["silent"], C["silent"], 1.3, ' stroke-dasharray="5 4"'),
        }[h]
        extra = ""
        if d == root:
            fill, stroke, txt, sw = "#0B2F32", C["teal"], C["teal"], 2.6
            extra = (f'<rect class="halo" x="{x}" y="{y}" width="{W}" height="{H}" rx="10" stroke="{C["teal"]}" stroke-width="2"/>'
                     f'<rect class="halo h2" x="{x}" y="{y}" width="{W}" height="{H}" rx="10" stroke="{C["teal"]}" stroke-width="2"/>'
                     f'<rect x="{x + W / 2 - 42}" y="{y - 21}" width="84" height="16" rx="8" fill="{C["teal"]}"/>'
                     f'<text x="{x + W / 2}" y="{y - 10}" text-anchor="middle" font-size="9" font-weight="600" '
                     f'fill="#04201E" letter-spacing="1.2">ROOT CAUSE</text>')
        nodes.append(
            f'<g class="node {h}" style="animation-delay:{depth[d] * 110 + pos[d] * 25:.0f}ms"><title>{e(tooltip(res, d, h))}</title>'
            f'{extra}<rect class="box" x="{x}" y="{y}" width="{W}" height="{H}" rx="10" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"{dash}/>'
            f'<text x="{x + W / 2}" y="{y + 18}" text-anchor="middle" font-size="10.5" font-weight="600" fill="{txt}">{e(label)}</text>'
            f'<text x="{x + W / 2}" y="{y + 32}" text-anchor="middle" font-size="9" fill="{C["muted"]}">{e(kind)}</text></g>')
    return (f'<div class="topo"><svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="Network map" '
            f'style="max-width:{width * 1.35:.0f}px;margin:0 auto">'
            + "".join(edges) + "".join(nodes) + "</svg></div>")


def gauge(conf, category, status):
    stops = {"high": (C["teal"], C["violet"]), "medium": (C["violet"], C["pink"]), "low": (C["warn"], C["pink"])}
    a, b = stops.get(category, (C["silent"], C["silent"]))
    if status == "healthy":
        a, b = C["ok"], C["teal"]
    r, circ = 56, 2 * 3.14159 * 56
    dash = circ * max(0.0, min(1.0, conf))
    label_col = a
    return (f'<div class="gauge"><svg width="164" height="164" viewBox="0 0 164 164">'
            f'<defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/>'
            f'<stop offset="1" stop-color="{b}"/></linearGradient></defs>'
            f'<circle cx="82" cy="82" r="{r}" fill="none" stroke="{C["line"]}" stroke-width="10"/>'
            f'<circle cx="82" cy="82" r="{r + 9}" fill="none" stroke="{a}" stroke-opacity=".18" stroke-width="1" stroke-dasharray="2 5"/>'
            f'<circle class="ring" cx="82" cy="82" r="{r}" fill="none" stroke="url(#rg)" stroke-width="10" stroke-linecap="round" '
            f'stroke-dasharray="{dash:.1f} {circ:.1f}" transform="rotate(-90 82 82)" style="filter:drop-shadow(0 0 6px {a})"/>'
            f'<text x="82" y="88" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="32" '
            f'font-weight="600" fill="{C["text"]}">{conf:.2f}</text>'
            f'<text x="82" y="110" text-anchor="middle" font-family="Inter, sans-serif" font-size="10" '
            f'fill="{C["muted"]}" letter-spacing="1.8">CONFIDENCE</text></svg>'
            f'<div class="cat" style="color:{label_col}">{e(category)}</div></div>')


def message_row(m, i):
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
    return (f'<div class="{cls}" style="animation-delay:{min(i * 70, 1600)}ms"><div class="rail" '
            f'style="background:{color};color:{color}"></div><div>'
            f'<div class="hd"><span class="ty ty-{t}">{t}</span><b>{e(m["sender"])}</b> → {e(m["recipient"])}{note}</div>'
            f'<div class="bd">{body}</div></div></div>')


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown(f'<div class="brand"><svg class="logo" width="32" height="32" viewBox="0 0 32 32">'
                f'<path d="M16 6 L6 24 L26 24 Z" fill="none" stroke="{C["violet"]}" stroke-width="1.3" opacity=".7"/>'
                f'<circle cx="16" cy="6" r="3.4" fill="{C["teal"]}"/><circle cx="6" cy="24" r="3.4" fill="{C["lilac"]}"/>'
                f'<circle cx="26" cy="24" r="3.4" fill="{C["pink"]}"/></svg>'
                f'<div><div class="name">NetCoop AI</div><div class="tag">Multi-agent diagnosis console</div></div></div>',
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

# ---------------------------------------------------------------- header + hero
crash_chip = f'<span class="chip" style="border-color:{C["crit"]}">crashed <b>{e(crash)}</b></span>' if crash != "none" else ""
online = 6 - (1 if crash != "none" else 0)
st.markdown(
    f'<div class="top"><div><h1>Network diagnosis</h1>'
    f'<div class="tag">Specialist agents share evidence, question each other, and agree on one explainable answer</div></div>'
    f'<div class="chips"><span class="live"><i></i>{online}/6 AGENTS ONLINE</span>'
    f'<span class="chip">incident <b>{e(r["incident_id"])}</b></span>'
    f'<span class="chip"><b>{res["elapsed_ms"]}</b> ms</span>'
    f'<span class="chip">follow-ups <b>{"on" if follow_up else "off"}</b></span>{crash_chip}</div></div>',
    unsafe_allow_html=True)

root = r["affected_devices"][0] if r["status"] == "diagnosed" and r["affected_devices"] else None
devs = "".join(f'<span class="dev {"root" if d == root else ""}">{e(d)}</span>' for d in r.get("affected_devices") or [])
domain = f'<span class="chip">fault domain <b>{e(r["fault_domain"])}</b></span>' if r.get("fault_domain") else ""
dev_row = f'<div class="devrow">{devs}{domain}</div>' if devs or domain else ""
st.markdown(
    f'<div class="hero"><div><span class="pill st-{e(r["status"])}"><span class="dot"></span>'
    f'{e(r["status"].replace("_", " "))}</span>'
    f'<div class="cause">{e(r["suspected_root_cause"])}</div>'
    f'<div class="sum">{e(r.get("summary_llm") or r.get("summary", ""))}</div>{dev_row}</div>'
    f'{gauge(r["confidence"], r.get("confidence_category", ""), r["status"])}</div>',
    unsafe_allow_html=True)

score = dq.get("score")
dq_col = C["ok"] if (score or 0) >= 0.9 else C["warn"] if (score or 0) >= 0.6 else C["crit"]
rej = len(res["rejected_messages"])
st.markdown(
    f'<div class="kpis" data-followups="{len(queries)}" data-confidence="{r["confidence"]}">'
    f'<div class="kpi" style="--acc:{C["teal"]}"><div class="l">Agent messages</div><div class="v">{counter(len(res["messages"]))}</div>'
    f'<div class="s">validated against netcoop-msg/1.0</div></div>'
    f'<div class="kpi" style="--acc:{C["lilac"]};animation-delay:80ms"><div class="l">Follow-up questions</div>'
    f'<div class="v">{counter(len(queries))}</div>'
    f'<div class="s">{"asked between agents" if follow_up else "switched off (baseline)"}</div></div>'
    f'<div class="kpi" style="--acc:{dq_col};animation-delay:160ms"><div class="l">Data quality</div>'
    f'<div class="v">{e(score if score is not None else "–")}</div>'
    f'<div class="s">{dq.get("fields_translated", 0)} fields translated</div></div>'
    f'<div class="kpi" style="--acc:{C["crit"] if rej else C["muted"]};animation-delay:240ms"><div class="l">Rejected messages</div>'
    f'<div class="v">{counter(rej)}</div><div class="s">failed the protocol check</div></div></div>',
    unsafe_allow_html=True)

# ---------------------------------------------------------------- four panes
with st.container(border=True):
    n_dev = len(((res["telemetry"] or {}).get("topology") or {}).get("devices") or {})
    panel_head("◇", "Network map", f"{n_dev} devices · hover for readings", C["teal"])
    svg = topology_svg(res) if res["telemetry"] else None
    if svg:
        st.markdown(svg, unsafe_allow_html=True)
        st.markdown(
            f'<div class="legend"><span><i style="border-color:{C["okline"]}"></i>healthy</span>'
            f'<span><i style="border-color:{C["warn"]}"></i>warning</span>'
            f'<span><i style="border-color:{C["crit"]}"></i>critical</span>'
            f'<span><i style="border-color:{C["silent"]};border-style:dashed"></i>no telemetry</span>'
            f'<span><i style="border-color:{C["teal"]};background:#0B2F32"></i>root cause</span>'
            f'<span><i style="border-color:{C["crit"]};width:16px;height:0;border-width:2px 0 0;border-radius:0"></i>'
            f'fault path · moving dots = traffic</span></div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="empty">{e(r["suspected_root_cause"])}</div>', unsafe_allow_html=True)
mid_left, mid_right = st.columns([1, 1.08], gap="medium")
with mid_left:
    with st.container(border=True):
        panel_head("⇄", "Agent conversation", f"{len(res['messages'])} messages · {len(queries)} follow-ups", C["violet"])
        sent = defaultdict(int)
        for m in res["messages"]:
            sent[m["sender"]] += 1
        st.markdown('<div class="roster">' + "".join(
            f'<span class="ag{" down" if a == crash else ""}" style="--c:{c}"><i></i>{e(label)} <b>{sent.get(a, 0)}</b></span>'
            for a, label, c in AGENTS) + "</div>", unsafe_allow_html=True)
        only_dialogue = st.toggle("Show only follow-up questions (QUERY / RESPONSE)", value=False, key="only_dialogue")
        shown = [m for m in res["messages"] if not only_dialogue or m["type"] in ("QUERY", "RESPONSE")]
        with st.container(height=520, border=False):
            st.markdown("".join(message_row(m, i) for i, m in enumerate(shown))
                        or '<div class="empty">No follow-up questions in this run.</div>', unsafe_allow_html=True)
        with st.expander("Raw netcoop-msg/1.0 messages (JSON)"):
            st.json(res["messages"], expanded=False)

with mid_right:
    with st.container(border=True):
        panel_head("✓", "Evidence and reasoning",
                   f"{len(r['supporting_evidence'])} readings · {len(r.get('evidence_sources') or [])} sources", C["pink"])
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
            for i, q in enumerate(queries):
                st.markdown(f'<div class="qa" style="animation-delay:{i * 120}ms"><div class="q">{e(q["from"])} → {e(q["to"])} · '
                            f'{e(q["question"])} on {e(q["device"])}</div><div class="a">{e(q["answer"])}</div></div>',
                            unsafe_allow_html=True)
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
with st.container(border=True):
    q = (res["telemetry"] or {}).get("quality")
    panel_head("⟲", "Translation log", "incoming fields → canonical schema", C["sky"])
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
                        f'<span style="color:{C["sky"]}">→</span> <b>{e(fmt(t["value"], t["unit"]))}</b>'
                        f'<div style="color:{C["muted"]}">{e(t["canonical"])}</div></td>'),
                    t["rule"]])
            st.markdown(table(["Incoming field", "Value → canonical", "Rule"], rows, scroll=True),
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="call sky"><b>Already canonical.</b> Every field used standard names and units; '
                        'nothing needed translating.</div>', unsafe_allow_html=True)
        if q["issues"]:
            st.markdown('<div class="sub">Validation issues</div>', unsafe_allow_html=True)
            st.markdown(table(["Level", "Code", "Detail"],
                              [[td_sev(i["level"]), td_mono(i["code"]), i["message"]] for i in q["issues"]], scroll=True),
                        unsafe_allow_html=True)
    for rej_msg in res["rejected_messages"]:
        st.markdown(f'<div class="sub">Rejected message</div><div class="qa" style="border-color:{C["crit"]};'
                    f'background:rgba(248,113,113,.07)"><div class="q" style="color:{C["crit"]}">from '
                    f'{e(rej_msg["message"].get("sender", "?"))}</div><div class="a">'
                    + "<br>".join(f"<code>{e(p)}</code>" for p in rej_msg["problems"]) + "</div></div>",
                    unsafe_allow_html=True)

st.download_button("⬇  Download JSON report", json.dumps(r, indent=2), file_name=f"{r['incident_id']}.json",
                   mime="application/json")
