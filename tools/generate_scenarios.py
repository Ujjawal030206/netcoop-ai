"""Generate the synthetic campus scenarios in data/scenarios/.

Extends the organisers' Colab generator (same field names, same value ranges
for normal / latency_spike / hardware_failure) with a campus topology, several
samples per device, and the remaining fault types from the problem statement.

    python tools/generate_scenarios.py          # deterministic (seed 7)
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "scenarios"
T0 = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
N = 6  # samples per device, one per minute

TOPOLOGY = {
    "devices": [
        {"id": "INTERNET_GW",        "type": "router",       "segment": "core",         "upstream": None},
        {"id": "CAMPUS_CORE_01",     "type": "core",         "segment": "core",         "upstream": "INTERNET_GW"},
        {"id": "HOSTEL_DIST_SW_01",  "type": "distribution", "segment": "hostel",       "upstream": "CAMPUS_CORE_01"},
        {"id": "HOSTEL_SW_01",       "type": "switch",       "segment": "hostel_A",     "upstream": "HOSTEL_DIST_SW_01"},
        {"id": "HOSTEL_AP_01",       "type": "ap",           "segment": "hostel_A",     "upstream": "HOSTEL_SW_01", "medium": "wireless"},
        {"id": "HOSTEL_AP_02",       "type": "ap",           "segment": "hostel_A",     "upstream": "HOSTEL_SW_01", "medium": "wireless"},
        {"id": "HOSTEL_PC_01",       "type": "client",       "segment": "hostel_A",     "upstream": "HOSTEL_SW_01", "medium": "wired"},
        {"id": "DEPT_SW_01",         "type": "switch",       "segment": "dept_cse",     "upstream": "CAMPUS_CORE_01"},
        {"id": "DEPT_PC_01",         "type": "client",       "segment": "dept_cse",     "upstream": "DEPT_SW_01", "medium": "wired"},
        {"id": "LECTURE_HALL_AP_01", "type": "ap",           "segment": "lecture_hall", "upstream": "DEPT_SW_01", "medium": "wireless"},
        {"id": "SVC_DNS_01",         "type": "service",      "segment": "core",         "upstream": "CAMPUS_CORE_01"},
        {"id": "SVC_DHCP_01",        "type": "service",      "segment": "core",         "upstream": "CAMPUS_CORE_01"},
    ]
}
DEVICES = {d["id"]: d for d in TOPOLOGY["devices"]}


def u(a, b, nd=2):
    return round(random.uniform(a, b), nd)


def ramp(a, b, i):
    """Value moving from a to b across the samples, with a little noise."""
    return a + (b - a) * i / (N - 1) + random.uniform(-0.03, 0.03) * abs(b - a)


def healthy(dev_id):
    t = DEVICES[dev_id]["type"]
    if t in ("router", "core", "distribution", "switch"):
        return {"cpu_utilization_pct": u(15, 40), "memory_utilization_pct": u(35, 60),
                "link_utilization_pct": u(25, 55), "latency_ms": u(2, 12), "jitter_ms": u(0.5, 3),
                "packet_loss_pct": u(0, 0.2), "queue_drops": random.randint(0, 5),
                "interface_errors": random.randint(0, 2), "crc_errors": random.randint(0, 1),
                "link_flaps": 0, "temperature_c": u(38, 50, 1)}
    if t == "ap":
        return {"cpu_utilization_pct": u(15, 35), "memory_utilization_pct": u(30, 50),
                "link_utilization_pct": u(20, 45), "rssi_dbm": u(-62, -52, 1), "snr_db": u(30, 40, 1),
                "retransmission_pct": u(2, 6), "connected_clients": random.randint(10, 35),
                "latency_ms": u(5, 15), "jitter_ms": u(1, 5), "packet_loss_pct": u(0, 0.3)}
    if t == "client":
        return {"latency_ms": u(5, 20), "jitter_ms": u(1, 5), "packet_loss_pct": u(0, 0.3)}
    if dev_id == "SVC_DNS_01":
        return {"dns_failure_pct": u(0, 1), "dns_response_ms": u(10, 40)}
    if dev_id == "SVC_DHCP_01":
        return {"dhcp_failure_pct": u(0, 1), "dhcp_pool_utilization_pct": u(45, 70)}
    return {}


def status_for(metrics, base="Healthy"):
    return base


def batch(name, faults, skip=(), statuses=None, description=""):
    """faults: {device_id: fn(i, metrics) -> None} mutating the healthy metrics per sample."""
    records = []
    for i in range(N):
        ts = (T0 + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        for dev_id in DEVICES:
            if dev_id in skip:
                continue
            m = healthy(dev_id)
            if dev_id in faults:
                faults[dev_id](i, m)
            m = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()}
            st = (statuses or {}).get(dev_id, "Healthy")
            records.append({"timestamp": ts, "device_id": dev_id, "metrics": m, "status": st})
    return {"schema_version": "1.1", "incident_id": f"INC-{name.upper().replace('_', '-')}",
            "source": "netcoop-generator", "description": description,
            "topology": TOPOLOGY, "records": records}


# --------------------------------------------------------------------------
# fault injectors
# --------------------------------------------------------------------------

def congestion_core(i, m):
    m["link_utilization_pct"] = min(99.5, ramp(62, 97, i))
    m["queue_drops"] = int(ramp(20, 320, i))
    m["latency_ms"] = ramp(25, 260, i)
    m["jitter_ms"] = ramp(5, 55, i)
    m["packet_loss_pct"] = ramp(0.2, 3.5, i)


def downstream_slow(i, m):
    m["latency_ms"] = ramp(30, 270, i)
    m["jitter_ms"] = ramp(5, 50, i)
    m["packet_loss_pct"] = ramp(0.2, 3, i)


def weak_wifi(i, m):
    m["rssi_dbm"] = u(-83, -77, 1)
    m["snr_db"] = u(10, 16, 1)
    m["retransmission_pct"] = u(24, 36)
    m["latency_ms"] = u(70, 160)
    m["jitter_ms"] = u(25, 55)
    m["packet_loss_pct"] = u(2, 5)


def interface_fault(i, m):
    m["interface_errors"] = int(ramp(25, 420, i))
    m["crc_errors"] = int(ramp(12, 160, i))
    m["link_flaps"] = random.choice([2, 3, 4])
    m["packet_loss_pct"] = u(2.5, 6)
    m["latency_ms"] = u(15, 40)


def lossy_client(i, m):
    m["packet_loss_pct"] = u(2, 5)


def dns_down(i, m):
    m["dns_failure_pct"] = u(35, 60)
    m["dns_response_ms"] = u(1500, 3000)


def dhcp_down(i, m):
    m["dhcp_pool_utilization_pct"] = u(99, 100)
    m["dhcp_failure_pct"] = u(30, 50)


def overload(i, m):
    m["cpu_utilization_pct"] = u(96, 99.5)
    m["memory_utilization_pct"] = u(93, 98)
    m["queue_drops"] = random.randint(60, 120)
    m["latency_ms"] = u(40, 120)


def wan_latency(i, m):
    m["latency_ms"] = u(180, 360)
    m["jitter_ms"] = u(40, 75)
    m["link_utilization_pct"] = u(40, 55)
    m["packet_loss_pct"] = u(0.2, 0.9)


def wan_downstream(i, m):
    m["latency_ms"] = u(185, 370)
    m["jitter_ms"] = u(35, 70)


def outage_child(i, m):
    m["packet_loss_pct"] = u(60, 100)
    m["latency_ms"] = u(800, 2000)


def mild_wifi(i, m):
    m["rssi_dbm"] = u(-71, -68, 1)
    m["retransmission_pct"] = u(11, 14)
    m["latency_ms"] = u(150, 260)
    m["jitter_ms"] = u(30, 50)


def congestion_access(i, m):
    m["link_utilization_pct"] = min(99.5, ramp(70, 97, i))
    m["queue_drops"] = int(ramp(40, 260, i))
    m["latency_ms"] = ramp(40, 240, i)
    m["jitter_ms"] = ramp(8, 45, i)


# --------------------------------------------------------------------------
# organiser generator (verbatim value ranges) and messy/legacy variants
# --------------------------------------------------------------------------

def organiser_record(fault_type, device=None, ts=None):
    data = {"timestamp": ts or "2026-10-31T10:05:00Z",
            "device_id": device or f"SW_CAMPUS_{random.randint(1, 10)}", "metrics": {}}
    if fault_type == "latency_spike":
        data["metrics"] = {"cpu_utilization_pct": u(40, 70), "link_utilization_pct": u(60, 85),
                           "packet_loss_pct": u(0.1, 1.5), "latency_ms": u(150, 400),
                           "jitter_ms": u(30, 80), "queue_drops": random.randint(0, 50)}
        data["status"] = "Warning"
    elif fault_type == "hardware_failure":
        data["metrics"] = {"cpu_utilization_pct": 99.9, "link_utilization_pct": 100.0,
                           "packet_loss_pct": u(15, 30), "latency_ms": u(500, 1000),
                           "queue_drops": random.randint(5000, 15000), "interface_errors": random.randint(100, 500)}
        data["status"] = "Critical"
    else:
        data["metrics"] = {"cpu_utilization_pct": u(10, 35), "link_utilization_pct": u(20, 50),
                           "packet_loss_pct": 0.0, "latency_ms": u(5, 20), "jitter_ms": u(1, 5), "queue_drops": 0}
        data["status"] = "Healthy"
    return data


def interop_messy():
    """Hostel congestion again, but sent by a legacy collector in a different dialect."""
    recs = []
    for i in range(N):
        epoch = int((T0 + timedelta(minutes=i)).timestamp())
        recs.append({"host": "HOSTEL_DIST_SW_01", "ts": epoch,
                     "linkUtil": round(min(0.995, ramp(0.62, 0.97, i)), 3),     # fraction, not %
                     "egressDrops": int(ramp(20, 320, i)),
                     "rtt": f"{ramp(0.025, 0.26, i):.3f} s",                     # seconds with unit
                     "Jitter": {"value": round(ramp(5, 55, i), 1), "unit": "ms"},
                     "pktLossRate": round(ramp(0.002, 0.035, i), 4),             # 0-1 rate
                     "cpuLoad": f"{u(20, 40)}%", "ifInErrors": random.randint(0, 2),
                     "state": "degraded"})
        recs.append({"hostname": "HOSTEL_SW_01", "time": epoch * 1000,          # epoch milliseconds
                     "latency_s": round(ramp(0.03, 0.27, i), 3), "jitterMs": round(ramp(5, 50, i), 1),
                     "packet_loss_percent": round(ramp(0.2, 3, i), 2), "crc": 0, "status": "OK"})
        recs.append({"node": "HOSTEL_PC_01", "timestamp": (T0 + timedelta(minutes=i)).isoformat(),
                     "avgRtt": f"{ramp(30, 270, i):.0f}ms", "loss": round(ramp(0.2, 3, i), 2)})
    recs.append({"ts": int(T0.timestamp()), "rtt": "0.4 s"})                      # no device id -> rejected
    recs.append({"host": "HOSTEL_AP_01", "ts": int(T0.timestamp()), "signalStrength": "N/A",
                 "cpuLoad": 140, "rtt": "12ms", "fanSpeedRpm": 4200})            # missing, out of range, unknown
    return {
        "schema_version": "0.9",
        "incident_id": "INC-INTEROP",
        "description": "Legacy collector: camelCase names, seconds, fractions, epoch times, unit strings, "
                       "a record without a device id, an out-of-range CPU value, plus findings from two "
                       "external agents (one valid legacy 0.9 message, one malformed).",
        "topology": {"nodes": [{"name": d["id"], "role": d["type"], "zone": d["segment"]} for d in TOPOLOGY["devices"]],
                     "edges": [{"source": d["upstream"], "target": d["id"]} for d in TOPOLOGY["devices"] if d["upstream"]]},
        "records": recs,
        "external_findings": [
            {"version": "0.9", "from": "campus_nms_probe", "to": "correlation_agent", "kind": "finding",
             "sent_at": int(T0.timestamp()) + 300,
             "data": {"issue": "Uplink congestion on hostel distribution", "conf": 0.7,
                      "devices": ["HOSTEL_DIST_SW_01"], "readings": {"ifUtil": "96%", "outDiscards": 280}}},
            {"protocol": "netcoop-msg/1.0", "msg_id": "ext-broken-1", "type": "FINDING",
             "sender": "third_party_wifi_probe", "recipient": "correlation_agent",
             "timestamp": "2026-10-31T10:05:00Z",
             "payload": {"agent_role": "external", "hypotheses": [{"cause": "weak_wifi_signal", "score": 1.7}]}},
        ],
    }


# --------------------------------------------------------------------------
# deliberately ambiguous cases: readings alone sit near the thresholds, so the
# answer depends on time-series and scope evidence (used by tools/ablation.py)
# --------------------------------------------------------------------------

def amb_congestion_switch(i, m):
    m["link_utilization_pct"] = ramp(70, 88, i)        # warning only, never critical
    m["queue_drops"] = int(ramp(15, 60, i))            # mostly below warning
    m["latency_ms"] = ramp(20, 90, i)


def amb_congestion_victim(i, m):
    m["latency_ms"] = ramp(30, 280, i)                 # delay rises with the switch's load
    m["jitter_ms"] = ramp(6, 55, i)


def amb_wan_gateway(i, m):
    m["link_utilization_pct"] = u(80, 87)              # warning-level load, but flat
    m["latency_ms"] = u(150, 360)                      # delay jumps around independently of load
    m["jitter_ms"] = u(35, 70)
    m["queue_drops"] = random.randint(20, 55)


def amb_wifi_ap(i, m):
    m["rssi_dbm"] = u(-72, -68, 1)                     # borderline signal
    m["retransmission_pct"] = u(11, 15)
    m["latency_ms"] = u(120, 220)
    m["jitter_ms"] = u(30, 50)


def amb_wifi_switch(i, m):
    m["link_utilization_pct"] = u(81, 87)              # busy uplink, but wired PC stays fine
    m["queue_drops"] = random.randint(40, 70)


def ambiguous_sets():
    hostel = ["HOSTEL_AP_01", "HOSTEL_AP_02", "HOSTEL_PC_01"]
    return {
        "ambiguous_congestion": (batch("amb_c", {"HOSTEL_SW_01": amb_congestion_switch,
                                                 **{d: amb_congestion_victim for d in hostel}}), "link_congestion"),
        "ambiguous_wan": (batch("amb_w", {"INTERNET_GW": amb_wan_gateway,
                                          **{d: wan_downstream for d in ("CAMPUS_CORE_01", "DEPT_PC_01")}}), "path_latency"),
        "ambiguous_wifi": (batch("amb_f", {"HOSTEL_AP_02": amb_wifi_ap, "HOSTEL_SW_01": amb_wifi_switch}), "weak_wifi_signal"),
    }


def main(seed=7):
    random.seed(seed)
    OUT.mkdir(parents=True, exist_ok=True)
    hostel_down = ["HOSTEL_SW_01", "HOSTEL_AP_01", "HOSTEL_AP_02", "HOSTEL_PC_01"]
    scenarios = {
        "normal": (batch("normal", {}, description="All devices healthy."),
                   {"status": "healthy", "cause": "normal"}),
        "link_congestion": (batch("link_congestion",
                                  {"HOSTEL_DIST_SW_01": congestion_core, **{d: downstream_slow for d in hostel_down}},
                                  statuses={"HOSTEL_DIST_SW_01": "Warning"},
                                  description="Hostel distribution uplink saturates; latency rises with load downstream."),
                            {"status": "diagnosed", "cause": "link_congestion", "device": "HOSTEL_DIST_SW_01"}),
        "weak_wifi": (batch("weak_wifi", {"HOSTEL_AP_02": weak_wifi},
                            description="One hostel AP has poor signal; wired PC on the same switch is fine."),
                      {"status": "diagnosed", "cause": "weak_wifi_signal", "device": "HOSTEL_AP_02"}),
        "interface_fault": (batch("interface_fault", {"DEPT_SW_01": interface_fault, "DEPT_PC_01": lossy_client},
                                  statuses={"DEPT_SW_01": "Warning"},
                                  description="CSE department switch port: CRC and interface errors rising, link flapping."),
                            {"status": "diagnosed", "cause": "interface_fault", "device": "DEPT_SW_01"}),
        "dns_failure": (batch("dns_failure", {"SVC_DNS_01": dns_down},
                              description="DNS resolver failing while the network path is healthy."),
                        {"status": "diagnosed", "cause": "dns_failure", "device": "SVC_DNS_01"}),
        "dhcp_failure": (batch("dhcp_failure", {"SVC_DHCP_01": dhcp_down},
                               description="DHCP pool exhausted; new clients cannot get addresses."),
                         {"status": "diagnosed", "cause": "dhcp_failure", "device": "SVC_DHCP_01"}),
        "device_overload": (batch("device_overload", {"CAMPUS_CORE_01": overload},
                                  statuses={"CAMPUS_CORE_01": "Warning"},
                                  description="Core switch CPU and memory exhausted, interfaces clean."),
                            {"status": "diagnosed", "cause": "device_overload", "device": "CAMPUS_CORE_01"}),
        "wan_latency": (batch("wan_latency", {"INTERNET_GW": wan_latency,
                                              **{d: wan_downstream for d in ("CAMPUS_CORE_01", "HOSTEL_PC_01", "DEPT_PC_01")}},
                              description="Latency spike on the upstream path with no local congestion."),
                        {"status": "diagnosed", "cause": "path_latency", "device": "INTERNET_GW"}),
        "upstream_outage": (batch("upstream_outage", {d: outage_child for d in hostel_down},
                                  skip=("HOSTEL_DIST_SW_01",),
                                  statuses={d: "Critical" for d in hostel_down},
                                  description="Hostel distribution switch silent; everything below it unreachable."),
                            {"status": "diagnosed", "cause": "upstream_device_outage", "device": "HOSTEL_DIST_SW_01"}),
        "conflicting_wifi_vs_congestion": (
            batch("conflicting", {"HOSTEL_SW_01": congestion_access, "HOSTEL_AP_01": mild_wifi,
                                  "HOSTEL_PC_01": downstream_slow},
                  description="Hostel AP shows a borderline signal, but the access switch is saturated and the wired "
                              "PC is slow too: agents must decide between Wi-Fi and congestion."),
            {"status": "diagnosed", "cause": "link_congestion", "device": "HOSTEL_SW_01"}),
        "interop_legacy_mixed": (interop_messy(),
                                 {"status": "diagnosed", "cause": "link_congestion", "device": "HOSTEL_DIST_SW_01"}),
        "organiser_normal": (organiser_record("normal", "SW_CAMPUS_3"), {"status": "healthy", "cause": "normal"}),
        "organiser_latency_spike": (organiser_record("latency_spike", "SW_CAMPUS_7"),
                                    {"status": "diagnosed", "cause": "path_latency"}),
        "organiser_hardware_failure": (organiser_record("hardware_failure", "SW_CAMPUS_4"),
                                       {"status": "diagnosed", "cause": "hardware_failure", "device": "SW_CAMPUS_4"}),
        "insufficient_data": ({"incident_id": "INC-SPARSE", "records": [
                                  {"device_id": "LIBRARY_PC_07", "timestamp": "2026-10-31T10:00:00Z",
                                   "metrics": {"latency_ms": 210, "cpu_utilization_pct": None}},
                                  {"device_id": "LIBRARY_AP_02", "timestamp": "2026-10-31T09:20:00Z",
                                   "metrics": {"connected_clients": 18}}]},
                              {"status": "insufficient_evidence", "cause": None}),
    }
    manifest = {}
    for name, (data, expect) in scenarios.items():
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        manifest[f"{name}.json"] = {**expect, "description": data.get("description") or name.replace("_", " ")}
    # Malformed inputs: one unparseable file, one partially bad batch (NDJSON with a broken line).
    (OUT / "malformed_json.txt").write_text('{"device_id": "SW_1", "metrics": {"latency_ms": 250,,}', encoding="utf-8")
    manifest["malformed_json.txt"] = {"status": "error", "cause": None,
                                      "description": "Broken JSON: must fail with a clear message, not a crash."}
    lines = [json.dumps(organiser_record("hardware_failure", "SW_CAMPUS_9", f"2026-10-31T10:0{i}:00Z")) for i in range(5)]
    lines.insert(2, '{"device_id": "SW_CAMPUS_9", "metrics": {broken')
    (OUT / "organiser_stream.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest["organiser_stream.ndjson"] = {"status": "diagnosed", "cause": "hardware_failure", "device": "SW_CAMPUS_9",
                                           "description": "Organiser-format NDJSON stream (5 samples) with one corrupted line."}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {len(manifest)} scenarios to {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
