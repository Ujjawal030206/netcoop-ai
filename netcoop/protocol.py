"""netcoop-msg/1.0: the message envelope, schema validation, version adapters
and the message bus that every agent talks through.

Every message is validated against schemas/message.schema.json before it is
delivered. Messages in an older format (netcoop-msg/0.9, used by the legacy
campus probes) are upgraded by a registered adapter first. A message that
still fails validation is not delivered: the bus logs an ERROR message naming
the exact field that was wrong, and the diagnosis continues without it.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from . import catalog, knowledge
from .ingest import convert, map_field, parse_value

PROTOCOL = "netcoop-msg/1.0"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "message.schema.json"
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_message(type_, sender, recipient, incident_id, payload, in_reply_to=None):
    return {
        "protocol": PROTOCOL,
        "msg_id": f"msg-{uuid.uuid4().hex[:10]}",
        "incident_id": incident_id,
        "type": type_,
        "sender": sender,
        "recipient": recipient,
        "in_reply_to": in_reply_to,
        "timestamp": now_iso(),
        "payload": payload,
    }


def validation_errors(msg):
    """-> list of human-readable problems (empty if the message is valid)."""
    out = []
    for err in sorted(_VALIDATOR.iter_errors(msg), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in err.absolute_path) or "(message)"
        out.append(f"{path}: {err.message}")
    return out


# --------------------------------------------------------------------------
# Version adapters: older message formats -> netcoop-msg/1.0
# --------------------------------------------------------------------------

LEGACY_ISSUE_WORDS = [
    (("signal", "wifi", "wi-fi", "rssi", "coverage", "wireless"), "weak_wifi_signal"),
    (("congest", "utiliz", "utilis", "saturat", "bandwidth"), "link_congestion"),
    (("crc", "interface", "cable", "port", "ethernet", "flap"), "interface_fault"),
    (("dns", "name resolution"), "dns_failure"),
    (("dhcp", "lease", "ip address"), "dhcp_failure"),
    (("cpu", "memory", "overload"), "device_overload"),
    (("hardware", "fan", "power supply", "psu"), "hardware_failure"),
    (("latency", "delay", "jitter", "wan", "upstream"), "path_latency"),
]


def _legacy_cause(text):
    t = str(text or "").lower()
    for words, cause in LEGACY_ISSUE_WORDS:
        if any(w in t for w in words):
            return cause
    return None


def _upgrade_0_9(old):
    """netcoop-msg/0.9 (flat 'from/to/kind/data' with free-text issue and raw readings)."""
    data = old.get("data") or {}
    kind = str(old.get("kind", "")).upper()
    sender = old.get("from") or old.get("agent")
    devices = data.get("devices") or ([data["device"]] if data.get("device") else [])
    evidence, notes = [], []
    for name, raw in (data.get("readings") or {}).items():
        metric, name_unit, _ = map_field(name)
        num, vunit, prob = parse_value(raw)
        if not metric or prob:
            notes.append(f"reading '{name}' could not be interpreted")
            continue
        value, rule, _, err = convert(metric, num, vunit or name_unit)
        if err:
            notes.append(f"reading '{name}': {err}")
            continue
        sev, _, thr = knowledge.assess(metric, value)
        evidence.append({"metric": metric, "value": round(value, 3), "unit": catalog.unit(metric),
                         "device_id": devices[0] if devices else None, "threshold": thr,
                         "interpretation": knowledge.interpret(metric, sev), "severity": sev,
                         "source": sender or "legacy"})
    payload = data
    if kind == "FINDING":
        cause = _legacy_cause(data.get("issue"))
        conf = data.get("conf", data.get("confidence"))
        hyps = []
        if cause and isinstance(conf, (int, float)):
            hyps.append({"cause": cause, "score": max(0.0, min(1.0, float(conf))),
                         "affected_devices": [str(d) for d in devices], "evidence": evidence,
                         "rationale": f"legacy issue text: '{data.get('issue')}'"})
        payload = {
            "agent_role": "external",
            "status": "anomalies_found" if hyps or any(e["severity"] != "normal" for e in evidence) else "no_anomalies",
            "observations": evidence,
            "hypotheses": hyps,
            "checked_metrics": sorted({e["metric"] for e in evidence}),
            "missing_metrics": [],
            "data_quality": 0.8,
            "summary": str(data.get("issue") or ""),
            "context": {"adapted_from": "netcoop-msg/0.9", "adapter_notes": notes},
        }
    sent = old.get("sent_at")
    try:
        ts = (datetime.fromtimestamp(float(sent), tz=timezone.utc).isoformat().replace("+00:00", "Z")
              if isinstance(sent, (int, float)) else str(sent) if sent else now_iso())
    except (OverflowError, OSError, ValueError):
        ts = now_iso()
    msg = {
        "protocol": PROTOCOL,
        "msg_id": str(old.get("id") or f"msg-{uuid.uuid4().hex[:10]}"),
        "incident_id": str(old.get("incident") or old.get("incident_id") or ""),
        "type": kind,
        "sender": sender,
        "recipient": old.get("to") or "correlation_agent",
        "in_reply_to": old.get("re"),
        "timestamp": ts,
        "payload": payload,
    }
    return msg


ADAPTERS = {"netcoop-msg/0.9": _upgrade_0_9}


def detect_version(msg):
    if not isinstance(msg, dict):
        return None
    if "protocol" in msg:
        return msg["protocol"]
    if "version" in msg and str(msg["version"]).startswith("0.9"):
        return "netcoop-msg/0.9"
    if {"from", "kind"} <= set(msg):
        return "netcoop-msg/0.9"
    return None


def upgrade(msg):
    """-> (message in 1.0 format or the original, note or None)."""
    ver = detect_version(msg)
    if ver in ADAPTERS:
        return ADAPTERS[ver](msg), f"upgraded from {ver} by adapter"
    return msg, None


# --------------------------------------------------------------------------
# Bus
# --------------------------------------------------------------------------

class MessageBus:
    """Validates, logs and delivers messages for one incident."""

    def __init__(self, incident_id):
        self.incident_id = incident_id
        self.log = []          # every delivered message, in order
        self.rejected = []     # messages that failed validation, with reasons
        self.agents = {}

    def register(self, agent):
        self.agents[agent.name] = agent
        agent.bus = self

    def _reject(self, msg, problems, note=None):
        sender = msg.get("sender") if isinstance(msg, dict) else None
        err = make_message("ERROR", "message_bus", sender or "orchestrator", self.incident_id, {
            "code": "schema_validation_failed",
            "message": f"message from {sender or 'unknown sender'} rejected: {problems[0]}",
            "details": {"problems": problems, "upgrade": note,
                        "original_type": msg.get("type") if isinstance(msg, dict) else None},
        })
        self.rejected.append({"message": msg, "problems": problems})
        self.log.append(err)
        return None

    def post(self, msg):
        """Validate and log a message. -> the (possibly upgraded) message, or None if rejected."""
        msg, note = upgrade(msg)
        if isinstance(msg, dict) and not msg.get("incident_id"):
            msg["incident_id"] = self.incident_id
        problems = validation_errors(msg) if isinstance(msg, dict) else ["message is not a JSON object"]
        if problems:
            return self._reject(msg if isinstance(msg, dict) else {"raw": msg}, problems, note)
        if note:
            msg.setdefault("payload", {}).setdefault("context", {})["protocol_note"] = note
        self.log.append(msg)
        return msg

    def send(self, msg):
        """Validate, log and deliver a message. Replies addressed to a third agent are
        delivered onward automatically; replies to the original sender are returned."""
        msg = self.post(msg)
        if msg is None:
            return []
        return self._deliver(msg)

    def _deliver(self, msg):
        if msg["recipient"] == "*":
            targets = [a for n, a in self.agents.items() if n != msg["sender"]]
        else:
            targets = [self.agents[msg["recipient"]]] if msg["recipient"] in self.agents else []
        out = []
        for agent in targets:
            try:
                replies = agent.handle(msg) or []
            except Exception as exc:  # an agent crash must not take the diagnosis down
                err = make_message("ERROR", agent.name, msg["sender"], self.incident_id,
                                   {"code": "agent_exception",
                                    "message": f"{agent.name} failed while handling {msg['type']}: {exc!r}"},
                                   in_reply_to=msg["msg_id"])
                self.post(err)
                out.append(err)
                continue
            for r in replies:
                posted = self.post(r)
                if posted is None:
                    continue
                out.append(posted)
                if posted["recipient"] not in (msg["sender"], "orchestrator"):
                    out.extend(self._deliver(posted))
        return out
