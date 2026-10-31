"""Shared agent behaviour: message handling and the standard FINDING builder."""

from .. import catalog, knowledge, scoring
from ..protocol import make_message


class Agent:
    name = "agent"
    role = "base"

    def __init__(self):
        self.bus = None

    def handle(self, msg):
        method = getattr(self, f"on_{msg['type'].lower()}", None)
        return method(msg) if method else []

    def reply(self, msg, type_, payload, recipient=None):
        return make_message(type_, self.name, recipient or msg["sender"], msg["incident_id"], payload,
                            in_reply_to=msg["msg_id"])


class AnalysisAgent(Agent):
    """An agent that owns a set of canonical metrics and reports a FINDING on them."""

    owned = set()

    def __init__(self):
        super().__init__()
        self.telemetry = None

    def on_telemetry(self, msg):
        self.telemetry = msg["payload"]
        return []

    def evidence(self, device_id, metric, info):
        sev, _, thr = knowledge.assess(metric, info["value"])
        detail = knowledge.interpret(metric, sev)
        if info.get("samples", 1) > 1:
            detail += f" (median of {info['samples']} samples, worst {info['worst']})"
        ev = {"metric": metric, "value": info["value"], "unit": info["unit"], "device_id": device_id,
              "threshold": thr, "interpretation": detail, "severity": sev, "source": self.name}
        if info.get("samples", 1) > 1:
            ev["worst"] = info["worst"]
            if sev == "normal" and knowledge.assess(metric, info["worst"])[0] != "normal":
                ev["severity"] = "warning"   # a peak past the threshold is worth a warning
                ev["interpretation"] = (f"{knowledge.interpret(metric, 'warning')}: peaked at {info['worst']} "
                                        f"(median {info['value']} of {info['samples']} samples)")
        return ev

    def observations(self):
        out = []
        for did, dev in self.telemetry["devices"].items():
            for metric, info in dev["metrics"].items():
                if metric in self.owned:
                    out.append(self.evidence(did, metric, info))
        return out

    def extra_observations(self):
        return []

    def context(self, observations):
        return {}

    def on_task(self, msg):
        if self.telemetry is None:
            return [self.reply(msg, "ERROR", {"code": "no_telemetry", "message": f"{self.name} received no telemetry"})]
        obs = self.observations() + self.extra_observations()
        checked = sorted({o["metric"] for o in obs})
        missing = sorted(m for m in self.owned if m in catalog.METRICS and m not in checked)
        idx = scoring.EvidenceIndex(obs, self.telemetry.get("topology"))
        allowed = set(self.owned) | scoring.PSEUDO & set(checked)
        hyps = []
        for cause, r in scoring.rank(idx, allowed=allowed):
            if r["score"] < 0.3:
                continue
            sig = knowledge.signatures()[cause]
            hyps.append({"cause": cause, "score": r["score"], "affected_devices": r["affected_devices"],
                         "evidence": r["evidence"],
                         "rationale": f"{sig['label']} - seen from {self.role} metrics only "
                                      f"({len(r['evidence'])} supporting readings, "
                                      f"{int(r['coverage'] * 100)}% of defining metrics available)"})
        abnormal = [o for o in obs if o["severity"] != "normal"]
        if not obs:
            status, summary = "insufficient_data", f"No {self.role} metrics were present in the telemetry."
        elif abnormal:
            status = "anomalies_found"
            top = sorted(abnormal, key=lambda o: -scoring.strength(o))[:3]
            summary = (f"{len(abnormal)} abnormal reading(s) on "
                       f"{len({o['device_id'] for o in abnormal})} device(s); worst: "
                       + "; ".join(f"{o['device_id']} {o['metric']}={o['value']}{'' if o['unit'] in ('count', 'state', 'bool') else ' ' + o['unit']} ({o['severity']})"
                                   for o in top))
        else:
            status, summary = "no_anomalies", f"All {len(obs)} {self.role} readings are within normal range."
        payload = {"agent_role": self.role, "status": status, "observations": obs, "hypotheses": hyps,
                   "checked_metrics": checked, "missing_metrics": missing,
                   "data_quality": self.telemetry["quality"]["score"], "summary": summary,
                   "context": self.context(obs)}
        return [self.reply(msg, "FINDING", payload, recipient="correlation_agent")]

    def on_query(self, msg):
        q = msg["payload"]
        handler = getattr(self, f"answer_{q['question']}", None)
        if handler is None or self.telemetry is None:
            return [self.reply(msg, "ERROR", {"code": "unsupported_question",
                                              "message": f"{self.name} cannot answer '{q['question']}'"})]
        supports, refutes, evidence, detail = handler(q.get("args") or {})
        return [self.reply(msg, "RESPONSE", {"question": q["question"], "supports": supports,
                                             "refutes": refutes, "evidence": evidence, "detail": detail})]

    # helpers for answers
    def series(self, device, metric):
        dev = self.telemetry["devices"].get(device) or {}
        info = dev.get("metrics", {}).get(metric)
        return info["series"] if info else []

    def upstream_of(self, device):
        return (self.telemetry["topology"]["devices"].get(device) or {}).get("upstream")
