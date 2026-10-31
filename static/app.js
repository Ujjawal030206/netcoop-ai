"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, unit) => {
  if (typeof v === "number") v = Math.round(v * 100) / 100;
  return ["count", "state", "bool", "ratio"].includes(unit) ? `${v}` : `${v} ${unit}`;
};

let last = null;
let timers = [];

// ---------------------------------------------------------------- scenarios
async function loadScenarios() {
  const list = await (await fetch("/api/scenarios")).json();
  const box = $("#scenarios");
  box.innerHTML = "";
  for (const s of list) {
    const b = document.createElement("button");
    b.className = "scn";
    b.title = s.description || "";
    b.innerHTML = `<span>${esc(s.file.replace(/\.(json|txt|ndjson)$/, "").replace(/_/g, " "))}</span><small>${esc(s.cause || s.status)}</small>`;
    b.onclick = () => {
      document.querySelectorAll(".scn").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      run({ scenario: s.file });
    };
    box.appendChild(b);
  }
  const h = await (await fetch("/api/health")).json();
  if (h.llm_configured) $("#llmRow").hidden = false;
}

async function run(body) {
  const crash = $("#crash").value;
  if (crash) body.crash_agents = [crash];
  if ($("#llm").checked) body.llm = true;
  $("#report").innerHTML = `<div class="empty">Agents working…</div>`;
  const resp = await fetch("/api/diagnose", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const res = await resp.json();
  if (!resp.ok) {
    $("#report").innerHTML = `<span class="status st-error">error</span><div class="cause">${esc(res.detail || resp.statusText)}</div>`;
    return;
  }
  last = res;
  render(res, crash);
}

$("#runPaste").onclick = () => {
  const text = $("#paste").value.trim();
  if (!text) return;
  document.querySelectorAll(".scn").forEach((x) => x.classList.remove("on"));
  run({ telemetry: text });
};
$("#file").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("#paste").value = await f.text();
  $("#runPaste").click();
};
$("#replay").onclick = () => last && animate(last.messages, last._crash);
document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("#tabs button, .tab").forEach((x) => x.classList.remove("on"));
  b.classList.add("on");
  $(`#tab-${b.dataset.tab}`).classList.add("on");
}));

// ---------------------------------------------------------------- render
function render(res, crash) {
  res._crash = crash;
  const r = res.report;
  $("#meta").innerHTML = `${esc(r.incident_id)}<br>${res.messages.length} messages · ${res.rejected_messages.length} rejected · ${res.elapsed_ms} ms`;
  renderReport(r);
  renderTopo(res);
  renderEvidence(res);
  renderInterop(res);
  $("#json").textContent = JSON.stringify(r, null, 2);
  $("#replay").disabled = false;
  animate(res.messages, crash);
}

function confColor(c, status) {
  if (status === "healthy") return "var(--ok)";
  return c >= 0.75 ? "var(--ok)" : c >= 0.55 ? "var(--accent)" : c >= 0.35 ? "var(--warn)" : "var(--silent)";
}

function renderReport(r) {
  const root = r.status === "diagnosed" && r.affected_devices.length ? r.affected_devices[0] : null;
  $("#report").innerHTML = `
    <span class="status st-${esc(r.status)}">${esc(r.status.replace("_", " "))}</span>
    <div class="cause">${esc(r.suspected_root_cause)}</div>
    <div class="conf">
      <div class="bar"><i style="width:${Math.round(r.confidence * 100)}%;background:${confColor(r.confidence, r.status)}"></i></div>
      <b>${r.confidence.toFixed(2)}</b><span class="muted">confidence · ${esc(r.confidence_category)}</span>
    </div>
    ${r.affected_devices.length ? `<div class="chips">${r.affected_devices.map((d) => `<span class="chip ${d === root ? "root" : ""}">${esc(d)}</span>`).join("")}</div>` : ""}
    <p class="sum">${esc(r.summary_llm || r.summary)}</p>
    <div class="kv">
      <span>Evidence from <b>${esc((r.evidence_sources || []).join(", "))}</b></span>
      ${r.fault_domain ? `<span>Fault domain <b>${esc(r.fault_domain)}</b></span>` : ""}
      ${r.data_quality ? `<span>Data quality <b>${r.data_quality.score}</b> (${r.data_quality.fields_translated} fields translated, ${r.data_quality.issues} issues)</span>` : ""}
    </div>`;
}

// ---------------------------------------------------------------- topology
function deviceHealth(res, id) {
  const topo = res.topology || {};
  if ((topo.no_telemetry || []).includes(id)) return "silent";
  const d = res.devices[id];
  if (!d) return "silent";
  let worst = d.status === "down" ? 2 : d.status === "degraded" ? 1 : 0;
  for (const [m, info] of Object.entries(d.metrics)) {
    const t = res.thresholds[m];
    if (!t) continue;
    const v = t.direction === "low" ? -info.value : info.value;
    const w = t.direction === "low" ? -t.warning : t.warning;
    const c = t.direction === "low" ? -t.critical : t.critical;
    worst = Math.max(worst, v >= c ? 2 : v >= w ? 1 : 0);
  }
  return ["ok", "warn", "crit"][worst];
}

function renderTopo(res) {
  const box = $("#topo");
  const topo = res.topology;
  if (!topo || !topo.devices || !Object.keys(topo.devices).length) { box.innerHTML = `<div class="empty">No telemetry</div>`; return; }
  const devs = topo.devices;
  const ids = Object.keys(devs);
  const kids = {};
  const roots = [];
  for (const id of ids) {
    const up = devs[id].upstream;
    if (up && devs[up]) (kids[up] = kids[up] || []).push(id); else roots.push(id);
  }
  Object.values(kids).forEach((k) => k.sort());
  const pos = {};
  let leaf = 0, maxDepth = 0;
  const place = (id, depth) => {
    maxDepth = Math.max(maxDepth, depth);
    const ch = kids[id] || [];
    if (!ch.length) { pos[id] = { x: leaf++, y: depth }; return pos[id].x; }
    const xs = ch.map((c) => place(c, depth + 1));
    pos[id] = { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: depth };
    return pos[id].x;
  };
  roots.sort().forEach((r) => place(r, 0));
  const W = 132, H = 78, NW = 120, NH = 38;
  const width = Math.max(leaf, 1) * W + 20, height = (maxDepth + 1) * H + 20;
  const r = res.report;
  const rootDev = r.status === "diagnosed" ? r.affected_devices[0] : null;
  const affected = new Set(r.status === "diagnosed" ? r.affected_devices : []);
  const cx = (id) => 10 + pos[id].x * W + W / 2, cy = (id) => 10 + pos[id].y * H + NH / 2;
  let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="network topology">`;
  for (const id of ids) {
    const up = devs[id].upstream;
    if (up && pos[up]) {
      const hot = affected.has(id) && (affected.has(up) || up === rootDev);
      svg += `<path class="edge ${hot ? "hot" : ""}" d="M${cx(up)},${cy(up) + NH / 2} C${cx(up)},${cy(up) + NH} ${cx(id)},${cy(id) - NH} ${cx(id)},${cy(id) - NH / 2}"/>`;
    }
  }
  for (const id of ids) {
    const h = deviceHealth(res, id);
    const x = cx(id) - NW / 2, y = cy(id) - NH / 2;
    const label = id.length > 17 ? id.slice(0, 16) + "…" : id;
    svg += `<g class="node n-${h} ${id === rootDev ? "n-root" : ""}"><title>${esc(id)} (${esc(devs[id].type)})</title>
      <rect x="${x}" y="${y}" width="${NW}" height="${NH}" rx="8"/>
      <text x="${cx(id)}" y="${y + 16}" text-anchor="middle">${esc(label)}</text>
      <text class="sub" x="${cx(id)}" y="${y + 30}" text-anchor="middle">${esc(devs[id].type)}${devs[id].medium === "wireless" ? " · wifi" : ""}</text></g>`;
  }
  box.innerHTML = svg + "</svg>";
}

// ---------------------------------------------------------------- agents graph + conversation
const AG = {
  orchestrator: { x: 230, y: 8, label: "Orchestrator", role: "coordinates tasks" },
  ingestion_agent: { x: 10, y: 120, label: "Ingestion", role: "interop gateway" },
  performance_agent: { x: 185, y: 70, label: "Performance", role: "latency · loss · util" },
  infrastructure_agent: { x: 185, y: 140, label: "Infrastructure", role: "cpu · errors · drops" },
  connectivity_agent: { x: 185, y: 210, label: "Connectivity", role: "topology · wifi · dns" },
  correlation_agent: { x: 360, y: 120, label: "Fault Correlation", role: "combines · asks" },
  reporting_agent: { x: 360, y: 225, label: "Reporting", role: "report · actions" },
  external: { x: 10, y: 225, label: "External agents", role: "legacy / 3rd party" },
};
const AW = 140, AH = 44;
const node = (n) => AG[n] || AG.external;
const mid = (n) => ({ x: node(n).x + AW / 2, y: node(n).y + AH / 2 });

function edgePath(a, b) {
  const A = mid(a), B = mid(b);
  const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
  const sx = A.x + (dx / len) * 50, sy = A.y + (dy / len) * 22, ex = B.x - (dx / len) * 56, ey = B.y - (dy / len) * 26;
  const bend = 14;
  return `M${sx},${sy} Q${(sx + ex) / 2 - (dy / len) * bend},${(sy + ey) / 2 + (dx / len) * bend} ${ex},${ey}`;
}

function drawAgents(messages, crash) {
  const pairs = new Map();
  for (const m of messages) {
    const recips = m.recipient === "*" ? Object.keys(AG).filter((k) => !["orchestrator", "external", m.sender].includes(k)) : [m.recipient];
    for (const rc of recips) {
      if (m.sender === "orchestrator" || rc === "orchestrator" || rc === "message_bus") continue;
      // telemetry also reaches correlation/reporting; drawing those lines only adds clutter
      if (m.type === "TELEMETRY" && ["correlation_agent", "reporting_agent"].includes(rc)) continue;
      const s = AG[m.sender] ? m.sender : "external";
      const key = `${s}>${AG[rc] ? rc : "external"}`;
      pairs.set(key, (pairs.get(key) || 0) + 1);
    }
  }
  const hasExt = messages.some((m) => !AG[m.sender] && m.sender !== "message_bus");
  let svg = `<svg viewBox="0 0 510 290"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#9aa1b2"/></marker></defs>`;
  for (const [key] of pairs) {
    const [a, b] = key.split(">");
    const q = a === "correlation_agent" && b.endsWith("_agent") && b !== "reporting_agent";
    svg += `<path class="link ${q ? "q" : ""}" data-k="${key}" d="${edgePath(a, b)}"/>`;
  }
  for (const [name, n] of Object.entries(AG)) {
    if (name === "external" && !hasExt) continue;
    const crashed = crash === name;
    svg += `<g class="a ${crashed ? "crashed" : ""}" data-a="${name}"><rect x="${n.x}" y="${n.y}" width="${AW}" height="${AH}" rx="10"/>
      <text x="${n.x + 10}" y="${n.y + 19}">${n.label}${crashed ? " ✕" : ""}</text><text class="role" x="${n.x + 10}" y="${n.y + 34}">${n.role}</text></g>`;
  }
  svg += `<text class="now" id="now" x="10" y="284"></text></svg>`;
  $("#agents").innerHTML = svg;
}

function msgText(m) {
  const p = m.payload || {};
  switch (m.type) {
    case "TASK": return `Task: <b>${esc(p.action)}</b>`;
    case "TELEMETRY": return esc(p.summary);
    case "FINDING": {
      const h = (p.hypotheses || [])[0];
      return `<b>${esc(p.status)}</b>: ${esc(p.summary)}${h ? `<br><span class="muted">best local hypothesis: ${esc(h.cause)} (${h.score.toFixed(2)})</span>` : ""}`;
    }
    case "QUERY": return `<b>${esc(p.question)}</b>: ${esc(p.reason)}`;
    case "RESPONSE": return `${esc(p.detail)}${p.supports ? ` <span class="sev sev-normal">supports ${esc(p.supports)}</span>` : ""}${p.refutes ? ` <span class="sev sev-critical">argues against ${esc(p.refutes)}</span>` : ""}`;
    case "ERROR": return `<b>${esc(p.code)}</b>: ${esc(p.message)}`;
    case "REPORT": return "Final report delivered";
    default: return "";
  }
}

function animate(messages, crash) {
  timers.forEach(clearTimeout);
  timers = [];
  drawAgents(messages, crash);
  const talk = $("#tab-talk");
  talk.innerHTML = "";
  messages.forEach((m, i) => {
    timers.push(setTimeout(() => {
      document.querySelectorAll(".a.active, .link.active").forEach((x) => x.classList.remove("active"));
      const s = AG[m.sender] ? m.sender : "external";
      document.querySelector(`.a[data-a="${s}"]`)?.classList.add("active");
      const recips = m.recipient === "*" ? Object.keys(AG).filter((k) => !["orchestrator", "external", s].includes(k)) : [AG[m.recipient] ? m.recipient : "external"];
      for (const rc of recips) {
        document.querySelector(`.a[data-a="${rc}"]`)?.classList.add("active");
        document.querySelector(`.link[data-k="${s}>${rc}"]`)?.classList.add("active");
      }
      const now = document.getElementById("now");
      if (now) now.textContent = `${i + 1}/${messages.length}  ${m.type}  ${m.sender} → ${m.recipient}`;
      const div = document.createElement("div");
      div.className = "msg";
      div.innerHTML = `<span class="t t-${m.type}">${m.type}</span><div>
        <div class="who"><b>${esc(m.sender)}</b> → <b>${esc(m.recipient)}</b> · ${esc(m.msg_id)}${m.payload?.context?.protocol_note ? ` · <span class="sev sev-warning">${esc(m.payload.context.protocol_note)}</span>` : ""}</div>
        <div class="txt">${msgText(m)}</div>
        <details><summary>message JSON (${esc(m.protocol)})</summary><pre>${esc(JSON.stringify(m, null, 2).slice(0, 12000))}</pre></details></div>`;
      talk.appendChild(div);
    }, i * 260));
  });
  timers.push(setTimeout(() => document.querySelectorAll(".a.active, .link.active").forEach((x) => x.classList.remove("active")), messages.length * 260 + 600));
}

// ---------------------------------------------------------------- evidence
function spark(series) {
  if (!series || series.length < 2) return "";
  const w = 70, h = 18, lo = Math.min(...series), hi = Math.max(...series), span = hi - lo || 1;
  const pts = series.map((v, i) => `${(i / (series.length - 1)) * w},${h - ((v - lo) / span) * h}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h + 2}"><polyline points="${pts}" fill="none" stroke="#3f46c8" stroke-width="1.6"/></svg>`;
}

function renderEvidence(res) {
  const r = res.report;
  let html = "";
  if (r.supporting_evidence.length) {
    html += `<div class="block"><h3>Supporting evidence</h3><table><tr><th>Device</th><th>Metric</th><th>Value</th><th>Trend</th><th>Interpretation</th><th>Agent</th></tr>`;
    for (const e of r.supporting_evidence) {
      const s = res.devices[e.device_id]?.metrics?.[e.metric]?.series;
      html += `<tr><td class="mono">${esc(e.device_id)}</td><td class="mono">${esc(e.metric)}</td><td class="mono"><span class="sev sev-${esc(e.severity)}">${esc(fmt(e.value, e.unit))}</span></td><td>${spark(s)}</td><td>${esc(e.interpretation)}</td><td class="mono">${esc(e.source)}</td></tr>`;
    }
    html += `</table></div>`;
  }
  if ((r.inter_agent_queries || []).length) {
    html += `<div class="block"><h3>Follow-up questions between agents</h3>`;
    for (const q of r.inter_agent_queries) {
      html += `<div class="qa"><div class="q">${esc(q.from)} → ${esc(q.to)} · ${esc(q.question)} on ${esc(q.device)} (checking ${esc(q.about)})</div>${esc(q.answer)}</div>`;
    }
    html += `</div>`;
  }
  if ((r.conflicts_resolved || []).length) {
    html += `<div class="block"><h3>Conflicts resolved</h3><ul>${r.conflicts_resolved.map((c) => `<li><b>${esc(c.between.join(" vs "))}</b>: ${esc(c.resolution)}</li>`).join("")}</ul></div>`;
  }
  if ((r.alternatives_detail || []).length) {
    html += `<div class="block"><h3>Alternatives considered</h3><table><tr><th>Cause</th><th>Device</th><th>Score</th><th>Why less likely</th></tr>${r.alternatives_detail.map((a) => `<tr><td class="mono">${esc(a.cause)}</td><td class="mono">${esc(a.device)}</td><td class="mono">${a.score.toFixed(2)}</td><td>${esc(a.why_less_likely)}</td></tr>`).join("")}</table></div>`;
  }
  if (r.confidence_method) {
    const c = r.confidence_method;
    html += `<div class="block"><h3>How the confidence was calculated</h3><p class="mono">confidence = top score × (0.65 + 0.35 × margin) × (0.5 + 0.5 × data quality) × missing-agent penalty<br>
      = ${c.top_score} × (0.65 + 0.35 × ${c.margin}) × (0.5 + 0.5 × ${c.data_quality}) × ${c.missing_agent_penalty} = <b>${r.confidence}</b> (capped at 0.95)</p>
      <p class="muted">margin = (top − runner-up) / top, with runner-up score ${c.runner_up_score}. ${c.abnormal_readings_behind_top_cause} abnormal readings back the top cause (at least 2 are required to claim a cause).</p></div>`;
  }
  html += `<div class="block"><h3>Recommended actions</h3><ol>${r.recommended_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol></div>`;
  if (r.limitations.length) html += `<div class="block"><h3>Limitations</h3><ul>${r.limitations.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
  $("#tab-evidence").innerHTML = html;
}

// ---------------------------------------------------------------- interop
function renderInterop(res) {
  const q = res.quality;
  let html = "";
  if (!q) {
    html = `<p>The input could not be parsed: <b>${esc(res.report.limitations[0] || "")}</b></p>`;
  } else {
    html += `<div class="kv"><span>Data quality score <b>${q.score}</b></span><span>Records accepted <b>${q.records_accepted}/${q.records_total}</b></span>
      <span>Fields translated <b>${q.transformations.length}</b></span><span>Issues <b>${q.issues.length}</b></span></div>`;
    if (q.transformations.length) {
      html += `<div class="block"><h3>Field and unit translation (to the canonical schema)</h3><table><tr><th>Device</th><th>Incoming field</th><th>Incoming value</th><th>Canonical</th><th>Value</th><th>Rule</th></tr>`;
      for (const t of q.transformations.slice(0, 60)) {
        html += `<tr><td class="mono">${esc(t.device_id)}</td><td class="mono">${esc(t.field)}</td><td class="mono">${esc(typeof t.raw === "object" ? JSON.stringify(t.raw) : t.raw)}</td><td class="mono">${esc(t.canonical)}</td><td class="mono">${esc(fmt(t.value, t.unit))}</td><td>${esc(t.rule)}</td></tr>`;
      }
      html += `</table>${q.transformations.length > 60 ? `<p class="muted">…and ${q.transformations.length - 60} more</p>` : ""}</div>`;
    } else {
      html += `<p class="muted" style="margin-top:12px">All fields already used canonical names and units; nothing needed translating.</p>`;
    }
    if (q.issues.length) {
      html += `<div class="block"><h3>Validation issues</h3><table><tr><th>Level</th><th>Code</th><th>Detail</th></tr>${q.issues.map((i) => `<tr><td><span class="sev sev-${esc(i.level)}">${esc(i.level)}</span></td><td class="mono">${esc(i.code)}</td><td>${esc(i.message)}</td></tr>`).join("")}</table></div>`;
    }
  }
  if (res.rejected_messages.length) {
    html += `<div class="block"><h3>Rejected inter-agent messages</h3>${res.rejected_messages.map((m) => `<div class="qa"><div class="q">from ${esc(m.message.sender || "?")} (${esc(m.message.protocol || m.message.version || "no protocol")})</div><ul>${m.problems.map((p) => `<li class="mono">${esc(p)}</li>`).join("")}</ul></div>`).join("")}</div>`;
  }
  const upgraded = res.messages.filter((m) => m.payload?.context?.protocol_note);
  if (upgraded.length) {
    html += `<div class="block"><h3>Messages upgraded by protocol adapters</h3><ul>${upgraded.map((m) => `<li><span class="mono">${esc(m.sender)}</span>: ${esc(m.payload.context.protocol_note)}</li>`).join("")}</ul></div>`;
  }
  $("#tab-interop").innerHTML = html;
}

loadScenarios();
