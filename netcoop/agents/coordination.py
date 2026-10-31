"""Ingestion, Fault Correlation and Reporting agents."""

from collections import Counter

from .. import knowledge, scoring
from ..ingest import IngestError, normalize
from ..protocol import make_message
from .base import Agent

CONF_BANDS = [(0.75, "high"), (0.55, "medium"), (0.35, "low"), (0.0, "insufficient")]


def band(conf):
    return next(name for floor, name in CONF_BANDS if conf >= floor)


class IngestionAgent(Agent):
    """Interoperability gateway: turns whatever arrived into canonical telemetry."""
    name = "ingestion_agent"
    role = "interoperability_gateway"

    def __init__(self, raw, field_mapper=None):
        super().__init__()
        self.raw = raw
        self.field_mapper = field_mapper

    def on_task(self, msg):
        try:
            data = normalize(self.raw, field_mapper=self.field_mapper)
        except IngestError as exc:
            return [self.reply(msg, "ERROR", {"code": "unusable_input", "message": str(exc)})]
        q = data["quality"]
        data["summary"] = (f"{len(data['devices'])} device(s), {q['records_accepted']}/{q['records_total']} records accepted, "
                           f"{len(q['transformations'])} field(s) translated, "
                           f"{sum(1 for i in q['issues'] if i['level'] != 'info')} issue(s); data quality {q['score']}")
        return [self.reply(msg, "TELEMETRY", data, recipient="*")]


class CorrelationAgent(Agent):
    """Combines every agent's evidence, resolves disagreements by asking targeted
    follow-up questions, and decides on the most likely root cause."""
    name = "correlation_agent"
    role = "fault_correlation"

    def __init__(self, expected_agents, follow_up=True):
        super().__init__()
        self.expected = list(expected_agents)
        self.follow_up = follow_up  # False only for the ablation experiment
        self.findings = {}
        self.telemetry = None

    def on_telemetry(self, msg):
        self.telemetry = msg["payload"]
        return []

    def on_finding(self, msg):
        self.findings[msg["sender"]] = msg["payload"]
        return []

    def _ask(self, msg, cause, result, dialogue):
        sig = knowledge.signatures()[cause]
        conf = sig.get("confirm")
        if not conf or conf["agent"] not in self.bus.agents:
            return []
        q = make_message("QUERY", self.name, conf["agent"], msg["incident_id"], {
            "question": conf["question"],
            "args": {**conf.get("args", {}), "device": result["device"], "cause": cause},
            "reason": f"check '{cause}' on {result['device']} (score {result['score']:.2f})",
        })
        replies = self.bus.send(q)
        for r in replies:
            if r["type"] == "RESPONSE":
                p = r["payload"]
                dialogue.append({"from": self.name, "to": conf["agent"], "question": conf["question"],
                                 "about": cause, "device": result["device"], "answer": p["detail"],
                                 "supports": p.get("supports"), "refutes": p.get("refutes")})
                return [p]
            if r["type"] == "ERROR":
                dialogue.append({"from": self.name, "to": conf["agent"], "question": conf["question"],
                                 "about": cause, "device": result["device"],
                                 "answer": f"error: {r['payload']['message']}", "supports": None, "refutes": None})
        return []

    def on_task(self, msg):
        topology = (self.telemetry or {}).get("topology")
        obs = [o for f in self.findings.values() for o in f.get("observations", [])]
        idx = scoring.EvidenceIndex(obs, topology)
        ranked = dict(scoring.rank(idx))

        votes = {}
        for agent, f in self.findings.items():
            for h in f.get("hypotheses", []):
                votes.setdefault(h["cause"], []).append((agent, h["score"], f.get("agent_role")))
        # causes only an external agent proposed still compete, at half weight
        for cause, vs in votes.items():
            if cause not in ranked and cause in knowledge.signatures():
                ext = [v for v in vs if v[2] == "external"]
                if ext:
                    agent, score, _ = max(ext, key=lambda v: v[1])
                    hyp = next(h for h in self.findings[agent]["hypotheses"] if h["cause"] == cause)
                    ranked[cause] = {"device": (hyp["affected_devices"] or ["unknown"])[0],
                                     "score": round(0.5 * score, 3), "primary": None, "support": None,
                                     "contra": 0.0, "coverage": 0.0, "evidence": hyp["evidence"],
                                     "contradicting_evidence": [], "affected_devices": hyp["affected_devices"],
                                     "missing_primary": []}
        adjustments = {c: [] for c in ranked}
        for cause, r in ranked.items():
            srcs = {e.get("source") for e in r["evidence"] if e.get("source")} | {a for a, _, _ in votes.get(cause, [])}
            r["sources"] = sorted(s for s in srcs if s)
            internal = [s for s in r["sources"] if self.findings.get(s, {}).get("agent_role") != "external"]
            if len(internal) >= 2:
                r["score"] = min(1.0, r["score"] + 0.05)
                adjustments[cause].append(f"+0.05: corroborated by {len(internal)} agents")
            ext = [v for v in votes.get(cause, []) if v[2] == "external"]
            if ext and r.get("primary") is not None:
                bonus = round(0.05 * max(v[1] for v in ext), 3)
                r["score"] = min(1.0, r["score"] + bonus)
                adjustments[cause].append(f"+{bonus}: external agent {ext[0][0]} agrees")

        abnormal = [o for o in obs if o["severity"] != "normal"]
        dialogue, conflicts = [], []
        order = sorted(ranked, key=lambda c: -ranked[c]["score"])

        if order and self.follow_up:
            top = order[0]
            to_check = [top]
            if len(order) > 1 and ranked[order[1]]["score"] >= ranked[top]["score"] - 0.3:
                to_check.append(order[1])
            for cause in to_check:
                for p in self._ask(msg, cause, ranked[cause], dialogue):
                    if p.get("supports") in ranked:
                        ranked[p["supports"]]["score"] = min(1.0, ranked[p["supports"]]["score"] + 0.1)
                        adjustments[p["supports"]].append(f"+0.10: {p['question']} supports it")
                    if p.get("refutes") in ranked:
                        ranked[p["refutes"]]["score"] = max(0.0, ranked[p["refutes"]]["score"] - 0.15)
                        adjustments[p["refutes"]].append(f"-0.15: {p['question']} argues against it")
            new_order = sorted(ranked, key=lambda c: -ranked[c]["score"])
            if len(to_check) > 1 or new_order[0] != top:
                a, b = new_order[0], (new_order[1] if len(new_order) > 1 else None)
                conflicts.append({
                    "between": [x for x in (top, to_check[-1]) if x],
                    "resolution": (f"'{a}' kept ({ranked[a]['score']:.2f})" +
                                   (f" over '{b}' ({ranked[b]['score']:.2f})" if b else "") +
                                   " after follow-up questions: " +
                                   "; ".join(d["answer"] for d in dialogue if d["answer"])),
                })
            order = new_order

        # agents whose own best guess differs from the final answer
        if order:
            for agent, f in self.findings.items():
                hs = f.get("hypotheses") or []
                if hs and hs[0]["cause"] != order[0] and hs[0]["score"] >= 0.5:
                    conflicts.append({
                        "between": [f"{agent}: {hs[0]['cause']}", f"combined: {order[0]}"],
                        "resolution": (f"{agent} only sees {f.get('agent_role')} metrics; '{order[0]}' explains evidence "
                                       f"from {len(ranked[order[0]]['sources'])} source(s) and scored higher once all "
                                       f"evidence was combined"),
                    })

        missing_agents = [a for a in self.expected if a not in self.findings]
        dq = (self.telemetry or {}).get("quality", {}).get("score", 0.5)
        diag = {"ranked": [], "dialogue": dialogue, "conflicts": conflicts, "missing_agents": missing_agents,
                "data_quality": dq, "abnormal_count": len(abnormal), "adjustments": adjustments,
                "scope": self.findings.get("connectivity_agent", {}).get("context", {})}
        for c in order:
            r = ranked[c]
            diag["ranked"].append({"cause": c, "label": knowledge.signatures()[c]["label"],
                                   "score": round(r["score"], 3), "device": r["device"],
                                   "affected_devices": r["affected_devices"], "sources": r["sources"],
                                   "evidence": r["evidence"], "contradicting_evidence": r["contradicting_evidence"],
                                   "missing_primary": r["missing_primary"], "coverage": r["coverage"],
                                   "adjustments": adjustments.get(c, [])})

        checked = {o["metric"] for o in obs}
        if not abnormal:
            diag["status"] = "healthy" if len(checked) >= 3 else "insufficient_evidence"
            diag["confidence"] = round(min(0.95, 0.5 + 0.05 * len(checked)) * (0.5 + 0.5 * dq), 2) \
                if diag["status"] == "healthy" else 0.2
        elif not order:
            diag["status"] = "insufficient_evidence"
            diag["confidence"] = 0.2
        else:
            top, second = ranked[order[0]]["score"], (ranked[order[1]]["score"] if len(order) > 1 else 0.0)
            margin = (top - second) / top if top > 0 else 0.0
            penalty = 0.85 ** len(missing_agents)
            conf = top * (0.65 + 0.35 * margin) * (0.5 + 0.5 * dq) * penalty
            diag["confidence"] = round(max(0.0, min(0.95, conf)), 2)  # telemetry never proves a cause outright
            diag["confidence_inputs"] = {"top_score": round(top, 3), "runner_up_score": round(second, 3),
                                         "margin": round(margin, 3), "data_quality": dq,
                                         "missing_agent_penalty": round(penalty, 3)}
            breadth = len({(e.get("device_id"), e["metric"]) for e in ranked[order[0]]["evidence"]
                           if e["severity"] != "normal"})
            diag["confidence_inputs"]["abnormal_readings_behind_top_cause"] = breadth
            # one abnormal reading is a symptom, not a diagnosis
            diag["status"] = "diagnosed" if diag["confidence"] >= 0.35 and breadth >= 2 else "insufficient_evidence"
            if diag["status"] == "insufficient_evidence":
                diag["confidence"] = min(diag["confidence"], 0.3)
        diag["confidence_category"] = band(diag["confidence"]) if diag["status"] != "healthy" else \
            ("high" if diag["confidence"] >= 0.75 else "medium")

        summary = ("no abnormal readings" if not abnormal else
                   f"top cause {order[0]} ({ranked[order[0]]['score']:.2f})" if order else
                   f"{len(abnormal)} abnormal readings but no known fault pattern matches")
        payload = {"agent_role": self.role, "status": "anomalies_found" if abnormal else "no_anomalies",
                   "observations": ranked[order[0]]["evidence"] if order else [],
                   "hypotheses": [{"cause": c["cause"], "score": min(1.0, c["score"]),
                                   "affected_devices": c["affected_devices"], "evidence": c["evidence"][:6],
                                   "rationale": c["label"]} for c in diag["ranked"][:5]],
                   "checked_metrics": sorted(checked), "missing_metrics": [], "data_quality": dq,
                   "summary": summary, "context": {"diagnosis": diag}}
        return [self.reply(msg, "FINDING", payload, recipient="reporting_agent")]


class ReportingAgent(Agent):
    """Writes the final structured report (JSON) and a readable Markdown version."""
    name = "reporting_agent"
    role = "recommendation_reporting"

    def __init__(self, narrator=None):
        super().__init__()
        self.narrator = narrator
        self.telemetry = None
        self.diagnosis = None
        self.findings = {}

    def on_telemetry(self, msg):
        self.telemetry = msg["payload"]
        return []

    def on_finding(self, msg):
        if msg["sender"] == "correlation_agent":
            self.diagnosis = msg["payload"]["context"]["diagnosis"]
        return []

    def on_task(self, msg):
        report = build_report(msg["incident_id"], self.diagnosis, self.telemetry, msg["payload"].get("notes", ""))
        report["summary"] = narrative(report)
        if self.narrator:
            text = self.narrator(report)
            if text:
                report["summary_llm"] = text
        return [self.reply(msg, "REPORT", {"report": report, "markdown": to_markdown(report)}, recipient="orchestrator")]


def _fmt(v, unit):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v}" if unit in ("count", "state", "bool", "ratio") else f"{v} {unit}"


def build_report(incident_id, diag, telemetry, notes=""):
    from datetime import datetime, timezone
    quality = (telemetry or {}).get("quality", {})
    issues = quality.get("issues", [])
    limitations = []
    report = {"incident_id": incident_id, "status": diag["status"]}
    ranked = diag["ranked"]
    top = ranked[0] if ranked and diag["status"] == "diagnosed" else None
    cat = diag["confidence_category"]

    if diag["status"] == "healthy":
        report["suspected_root_cause"] = "No fault detected: all measurements are within expected ranges"
        report["root_cause_id"] = "normal"
        affected, evidence, actions = [], [], ["No action needed; continue routine monitoring"]
    elif top:
        prefix = {"high": "Likely", "medium": "Probable", "low": "Possible (low confidence):"}[cat]
        label = top["label"][0].lower() + top["label"][1:]
        report["suspected_root_cause"] = f"{prefix} {label} at {top['device']}"
        report["root_cause_id"] = top["cause"]
        affected = top["affected_devices"]
        evidence = top["evidence"]
        sig = knowledge.signatures()[top["cause"]]
        actions = list(sig["actions"])
        actions[0] = f"{actions[0]} ({top['device']})"
        if cat == "low":
            actions.insert(0, "Verify on site before acting: the evidence is weak or ambiguous")
        for m in top["missing_primary"]:
            limitations.append(f"{m} was not reported for {top['device']}; it would help confirm '{top['cause']}'")
    else:
        report["suspected_root_cause"] = "Insufficient evidence to determine a reliable root cause"
        report["root_cause_id"] = None
        affected = sorted({e["device_id"] for r in ranked[:2] for e in r["evidence"] if e.get("device_id")})
        evidence = [e for r in ranked[:2] for e in r["evidence"]][:6]
        wanted = sorted({m for r in ranked[:3] for m in r["missing_primary"]})
        actions = ["Collect more telemetry before acting" + (f": {', '.join(wanted)}" if wanted else ""),
                   "Re-run the diagnosis with several samples per device and the network topology"]
        if diag.get("abnormal_count", 0) == 0:
            limitations.append("Too few metrics were supplied to confirm the network is healthy")

    report["confidence"] = diag["confidence"]
    report["confidence_category"] = cat
    report["affected_devices"] = affected
    scope = diag.get("scope") or {}
    report["fault_domain"] = scope.get("fault_domain") if diag["status"] != "healthy" else None
    report["supporting_evidence"] = [
        {"metric": e["metric"], "value": e["value"], "unit": e["unit"], "device_id": e.get("device_id"),
         "interpretation": e["interpretation"], "severity": e["severity"], "source": e.get("source")}
        for e in sorted(evidence, key=lambda e: -scoring.strength(e) if "corr" not in e["metric"] else 0)]
    alts = [r for r in ranked if not top or r["cause"] != top["cause"]][:3]
    report["alternative_causes"] = [f"{r['label']} at {r['device']} (score {r['score']:.2f})" for r in alts]
    report["alternatives_detail"] = [
        {"cause": r["cause"], "device": r["device"], "score": r["score"],
         "why_less_likely": ("; ".join(r["adjustments"]) or
                             (f"contradicted by {', '.join(e['metric'] for e in r['contradicting_evidence'])}"
                              if r["contradicting_evidence"] else "explains fewer of the abnormal readings"))}
        for r in alts]
    report["recommended_actions"] = actions
    srcs = set()
    for e in report["supporting_evidence"]:
        if e.get("source"):
            srcs.add(e["source"])
    if top:
        srcs |= set(top["sources"])
    report["evidence_sources"] = sorted(srcs) or ["ingestion_agent"]

    if quality.get("records_rejected"):
        limitations.append(f"{quality['records_rejected']} input record(s) rejected as malformed")
    codes = Counter(i["code"] for i in issues if i["level"] != "info")
    for code, n in codes.most_common(4):
        example = next(i["message"] for i in issues if i["code"] == code)
        limitations.append(f"{n} x {code.replace('_', ' ')} (e.g. {example})")
    topo = (telemetry or {}).get("topology", {})
    if telemetry and not topo.get("available"):
        limitations.append("No topology supplied: the fault scope covers only devices that reported")
    if telemetry and all(m.get("samples", 1) == 1 for d in telemetry["devices"].values() for m in d["metrics"].values()):
        limitations.append("Single snapshot per device: trends and load-latency correlation could not be tested")
    for a in diag.get("missing_agents", []):
        limitations.append(f"{a} did not report; its evidence is missing and confidence was reduced")
    if diag["status"] == "diagnosed" and cat != "high":
        limitations.append("Root cause requires further verification")
    report["limitations"] = limitations
    report["conflicts_resolved"] = diag["conflicts"]
    report["inter_agent_queries"] = diag["dialogue"]
    report["confidence_method"] = diag.get("confidence_inputs")
    report["data_quality"] = {"score": quality.get("score"), "records_total": quality.get("records_total"),
                              "records_accepted": quality.get("records_accepted"),
                              "fields_translated": len(quality.get("transformations", [])),
                              "issues": len([i for i in issues if i["level"] != "info"])}
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if notes:
        report["notes"] = notes
    return report


def _metric_name(metric):
    for suffix in ("_ms", "_pct", "_dbm", "_db", "_mbps", "_c"):
        if metric.endswith(suffix):
            metric = metric[: -len(suffix)]
    return metric.replace("_", " ")


def narrative(r):
    """Plain-English summary built only from the report fields (no invented facts)."""
    if r["status"] == "healthy":
        return "All agents agree the network is operating normally; no measurement crossed a warning threshold."
    if r["status"] == "error":
        return f"The diagnosis could not run: {r['suspected_root_cause']}."
    ev = r["supporting_evidence"][:3]
    ev_txt = ", ".join(f"{_metric_name(e['metric'])} {_fmt(e['value'], e['unit'])} on {e['device_id']}" for e in ev)
    if r["status"] == "insufficient_evidence":
        return (f"Abnormal readings were found ({ev_txt}) but they do not point clearly to one cause, so no root cause "
                f"is claimed. {r['recommended_actions'][0]}.")
    alt = f" The main alternative was {r['alternative_causes'][0]}." if r["alternative_causes"] else ""
    return (f"{r['suspected_root_cause']} (confidence {r['confidence']:.2f}, {r['confidence_category']}). "
            f"Key evidence: {ev_txt}, reported by {', '.join(r['evidence_sources'])}.{alt} "
            f"First action: {r['recommended_actions'][0]}.")


def to_markdown(r):
    lines = [f"# Network diagnosis {r['incident_id']}", "",
             f"**Status:** {r['status']}  ", f"**Root cause:** {r['suspected_root_cause']}  ",
             f"**Confidence:** {r['confidence']:.2f} ({r['confidence_category']})  ",
             f"**Affected devices:** {', '.join(r['affected_devices']) or 'none identified'}", "",
             r.get("summary_llm") or r["summary"], ""]
    if r["supporting_evidence"]:
        lines += ["## Evidence", "", "| Device | Metric | Value | Interpretation | Agent |", "|---|---|---|---|---|"]
        for e in r["supporting_evidence"]:
            lines.append(f"| {e['device_id']} | {e['metric']} | {_fmt(e['value'], e['unit'])} | {e['interpretation']} | {e['source']} |")
        lines.append("")
    if r["inter_agent_queries"]:
        lines += ["## How the agents settled it", ""]
        for q in r["inter_agent_queries"]:
            lines.append(f"- **{q['from']} -> {q['to']}** ({q['question']} on {q['device']}): {q['answer']}")
        lines.append("")
    if r["alternative_causes"]:
        lines += ["## Alternatives considered", ""] + [f"- {a}" for a in r["alternative_causes"]] + [""]
    lines += ["## Recommended actions", ""] + [f"{i}. {a}" for i, a in enumerate(r["recommended_actions"], 1)] + [""]
    if r["limitations"]:
        lines += ["## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    return "\n".join(lines)
