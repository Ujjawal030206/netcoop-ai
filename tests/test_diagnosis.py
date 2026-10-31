import json
from pathlib import Path

import pytest

from netcoop.orchestrator import diagnose
from netcoop.protocol import validation_errors

DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"
MANIFEST = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
REQUIRED = {"incident_id", "status", "suspected_root_cause", "confidence", "affected_devices",
            "supporting_evidence", "alternative_causes", "recommended_actions", "evidence_sources", "limitations"}


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_bundled_scenarios(name):
    exp = MANIFEST[name]
    r = diagnose((DIR / name).read_text(encoding="utf-8"))["report"]
    assert REQUIRED <= set(r)
    assert r["status"] == exp["status"]
    if exp.get("cause"):
        assert r["root_cause_id"] == exp["cause"]
    if exp.get("device"):
        assert exp["device"] in r["affected_devices"]
    assert 0 <= r["confidence"] <= 0.95
    if r["status"] == "diagnosed":
        assert r["supporting_evidence"] and r["recommended_actions"]


def test_every_logged_message_is_valid_protocol():
    res = diagnose((DIR / "interop_legacy_mixed.json").read_text(encoding="utf-8"))
    for m in res["messages"]:
        assert validation_errors(m) == [], m["type"]
    # the malformed external finding was rejected, the legacy 0.9 one accepted
    assert len(res["rejected_messages"]) == 1
    assert "campus_nms_probe" in res["report"]["evidence_sources"]


def test_multiple_agents_contribute_and_talk():
    res = diagnose((DIR / "link_congestion.json").read_text(encoding="utf-8"))
    types = [m["type"] for m in res["messages"]]
    assert types.count("FINDING") >= 4 and "QUERY" in types and "RESPONSE" in types
    assert len(res["report"]["evidence_sources"]) >= 2


def test_agent_crash_still_produces_report_with_lower_confidence():
    raw = (DIR / "weak_wifi.json").read_text(encoding="utf-8")
    full = diagnose(raw)["report"]
    crashed = diagnose(raw, crash_agents=["infrastructure_agent"])["report"]
    assert crashed["root_cause_id"] == full["root_cause_id"]
    assert crashed["confidence"] < full["confidence"]
    assert any("infrastructure_agent" in x for x in crashed["limitations"])


def test_sparse_data_does_not_claim_a_cause():
    r = diagnose((DIR / "insufficient_data.json").read_text(encoding="utf-8"))["report"]
    assert r["status"] == "insufficient_evidence" and r["root_cause_id"] is None
    assert r["confidence"] <= 0.3


def test_same_input_same_answer():
    raw = (DIR / "interface_fault.json").read_text(encoding="utf-8")
    a, b = diagnose(raw)["report"], diagnose(raw)["report"]
    for k in ("root_cause_id", "confidence", "affected_devices"):
        assert a[k] == b[k]


def test_early_warning_catches_creep_below_threshold_but_not_noise():
    import random
    import sys
    sys.path.insert(0, str(DIR.parent.parent / "tools"))
    import generate_scenarios as g

    def creep(i, m):
        m["latency_ms"] = [8, 9, 8, 10, 9, 70][i]

    random.seed(3)
    r = diagnose(g.batch("creep", {"HOSTEL_PC_01": creep}))["report"]
    assert r["status"] == "healthy"            # 70 ms is under the threshold: no fault is claimed
    assert any("HOSTEL_PC_01" in w for w in r["early_warnings"])
    for seed in range(10):
        random.seed(seed)
        assert diagnose(g.batch("n", {}))["report"]["early_warnings"] == []


def test_counterfactual_explains_dependence_on_the_key_reading():
    r = diagnose((DIR / "link_congestion.json").read_text(encoding="utf-8"))["report"]
    assert r["counterfactual"].startswith("Had ") and "link_congestion" in r["counterfactual"]
