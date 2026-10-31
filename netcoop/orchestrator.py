"""Orchestrator: runs one diagnosis end to end over the message bus.

  1. TASK normalize  -> ingestion_agent        -> TELEMETRY broadcast to every agent
  2. external findings (any protocol version)  -> adapted + validated -> correlation_agent
  3. TASK analyze    -> performance / infrastructure / connectivity agents -> FINDING -> correlation_agent
  4. TASK correlate  -> correlation_agent  (QUERY/RESPONSE with specialists) -> FINDING -> reporting_agent
  5. TASK report     -> reporting_agent    -> REPORT (JSON + Markdown)

Every step is a validated message in the log, so a run can be replayed and audited.
"""

import hashlib
import json
import time
from datetime import datetime, timezone

from .agents import (ConnectivityAgent, CorrelationAgent, InfrastructureAgent, IngestionAgent,
                     PerformanceAgent, ReportingAgent)
from .protocol import MessageBus, make_message

SPECIALISTS = [PerformanceAgent, InfrastructureAgent, ConnectivityAgent]


class _Crashing:
    """Wraps an agent so its analysis raises: used to demonstrate fault tolerance."""

    def __init__(self, agent):
        self._agent = agent

    def __getattr__(self, item):
        return getattr(self._agent, item)

    def handle(self, msg):
        if msg["type"] == "TASK":
            raise RuntimeError("simulated agent crash")
        return self._agent.handle(msg)


def _incident_id(raw):
    parsed = raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            parsed = None
    if isinstance(parsed, dict) and parsed.get("incident_id"):
        return str(parsed["incident_id"])
    blob = raw if isinstance(raw, (str, bytes)) else json.dumps(raw, sort_keys=True, default=str)
    if isinstance(blob, str):
        blob = blob.encode("utf-8", errors="replace")
    return "INC-" + hashlib.sha1(blob).hexdigest()[:6].upper()


def diagnose(raw, incident_id=None, crash_agents=(), narrator=None, field_mapper=None, follow_up=True):
    """Run the full multi-agent diagnosis. -> dict with report, markdown, messages, telemetry."""
    started = time.perf_counter()
    incident_id = incident_id or _incident_id(raw)
    bus = MessageBus(incident_id)
    ingestion = IngestionAgent(raw, field_mapper=field_mapper)
    specialists = [cls() for cls in SPECIALISTS]
    correlation = CorrelationAgent([a.name for a in specialists], follow_up=follow_up)
    reporting = ReportingAgent(narrator=narrator)
    for agent in [ingestion, *specialists, correlation, reporting]:
        bus.register(_Crashing(agent) if agent.name in crash_agents else agent)
        agent.bus = bus

    def task(recipient, action, notes=None):
        payload = {"action": action}
        if notes:
            payload["notes"] = notes
        return bus.send(make_message("TASK", "orchestrator", recipient, incident_id, payload))

    replies = task("ingestion_agent", "normalize")
    telemetry = next((r["payload"] for r in replies if r["type"] == "TELEMETRY"), None)
    if telemetry is None:
        err = next((r["payload"]["message"] for r in replies if r["type"] == "ERROR"), "ingestion failed")
        report = error_report(incident_id, err)
        bus.post(make_message("REPORT", "orchestrator", "orchestrator", incident_id, {"report": report}))
        return _result(report, f"# Network diagnosis {incident_id}\n\n**Status:** error\n\n{err}\n", bus, None, started)

    for ext in telemetry.get("external_findings", []):
        if isinstance(ext, dict):
            ext = dict(ext)
            ext.setdefault("incident_id" if "protocol" in ext else "incident", incident_id)
        bus.send(ext)

    for agent in specialists:
        task(agent.name, "analyze")
    task("correlation_agent", "correlate")
    replies = task("reporting_agent", "report")
    out = next((r["payload"] for r in replies if r["type"] == "REPORT"), None)
    if out is None:
        report = error_report(incident_id, "reporting agent produced no report")
        return _result(report, "", bus, telemetry, started)
    return _result(out["report"], out["markdown"], bus, telemetry, started)


def error_report(incident_id, message):
    return {
        "incident_id": incident_id, "status": "error",
        "suspected_root_cause": f"Input could not be processed: {message}",
        "root_cause_id": None, "confidence": 0.0, "confidence_category": "insufficient",
        "affected_devices": [], "supporting_evidence": [], "alternative_causes": [],
        "recommended_actions": ["Fix the input format (see docs/PROTOCOL.md and data/scenarios/ for examples) and re-run"],
        "evidence_sources": ["ingestion_agent"], "limitations": [message],
        "summary": f"The diagnosis could not run: {message}.",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _result(report, markdown, bus, telemetry, started):
    return {
        "report": report,
        "markdown": markdown,
        "messages": bus.log,
        "rejected_messages": bus.rejected,
        "telemetry": telemetry,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
