from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def test_scenarios_listed():
    r = client.get("/api/scenarios")
    assert r.status_code == 200 and any(s["file"] == "weak_wifi.json" for s in r.json())


def test_diagnose_scenario_and_raw_telemetry():
    r = client.post("/api/diagnose", json={"scenario": "weak_wifi.json"}).json()
    assert r["report"]["root_cause_id"] == "weak_wifi_signal" and r["messages"]
    raw = {"device_id": "SW_CAMPUS_4", "status": "Critical",
           "metrics": {"cpu_utilization_pct": 99.9, "link_utilization_pct": 100, "packet_loss_pct": 20,
                       "latency_ms": 800, "queue_drops": 9000, "interface_errors": 300}}
    r = client.post("/api/diagnose", json={"telemetry": raw}).json()
    assert r["report"]["root_cause_id"] == "hardware_failure"


def test_bad_requests_are_rejected_cleanly():
    assert client.post("/api/diagnose", json={"scenario": "../server.py"}).status_code == 404
    assert client.post("/api/diagnose", json={}).status_code == 400
    r = client.post("/api/diagnose", json={"telemetry": "{broken"}).json()
    assert r["report"]["status"] == "error"
