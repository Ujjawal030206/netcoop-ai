from netcoop.protocol import MessageBus, make_message, upgrade, validation_errors


def finding(**over):
    p = {"agent_role": "x", "status": "no_anomalies", "observations": [], "hypotheses": [],
         "checked_metrics": [], "missing_metrics": [], "data_quality": 1.0}
    p.update(over)
    return make_message("FINDING", "a", "b", "INC-1", p)


def test_valid_finding_passes_schema():
    assert validation_errors(finding()) == []


def test_bad_score_and_missing_fields_are_reported_precisely():
    msg = finding(hypotheses=[{"cause": "x", "score": 1.7, "affected_devices": [], "evidence": []}])
    del msg["payload"]["checked_metrics"]
    errs = validation_errors(msg)
    assert any("1.7" in e for e in errs)
    assert any("checked_metrics" in e for e in errs)


def test_bus_rejects_invalid_message_and_logs_error():
    bus = MessageBus("INC-1")
    assert bus.post({"protocol": "netcoop-msg/1.0", "type": "FINDING"}) is None
    assert bus.rejected and bus.log[-1]["type"] == "ERROR"
    assert bus.log[-1]["payload"]["code"] == "schema_validation_failed"


def test_legacy_0_9_message_is_upgraded_and_validates():
    old = {"version": "0.9", "from": "campus_nms_probe", "to": "correlation_agent", "kind": "finding",
           "sent_at": 1730000000, "incident": "INC-1",
           "data": {"issue": "Uplink congestion", "conf": 0.7, "devices": ["SW_1"],
                    "readings": {"ifUtil": "96%", "outDiscards": 280}}}
    new, note = upgrade(old)
    assert note and validation_errors(new) == []
    h = new["payload"]["hypotheses"][0]
    assert h["cause"] == "link_congestion" and h["score"] == 0.7
    metrics = {e["metric"]: e["value"] for e in new["payload"]["observations"]}
    assert metrics == {"link_utilization_pct": 96.0, "queue_drops": 280.0}


def test_agent_crash_becomes_error_message():
    class Boom:
        name = "boom"

        def handle(self, msg):
            raise RuntimeError("x")

    bus = MessageBus("INC-1")
    bus.register(Boom())
    replies = bus.send(make_message("TASK", "orchestrator", "boom", "INC-1", {"action": "analyze"}))
    assert replies[0]["type"] == "ERROR" and replies[0]["payload"]["code"] == "agent_exception"
