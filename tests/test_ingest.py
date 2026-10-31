import pytest

from netcoop.ingest import IngestError, map_field, normalize, parse_value


def metrics(data, device):
    return {k: v["value"] for k, v in data["devices"][device]["metrics"].items()}


def test_organiser_generator_format_is_accepted_unchanged():
    rec = {"timestamp": "2026-10-14T07:05:49.619791Z", "device_id": "SW_CAMPUS_4",
           "metrics": {"cpu_utilization_pct": 99.9, "link_utilization_pct": 100.0, "packet_loss_pct": 28.51,
                       "latency_ms": 926.46, "queue_drops": 13416, "interface_errors": 382},
           "status": "Critical"}
    d = normalize(rec)
    assert metrics(d, "SW_CAMPUS_4")["latency_ms"] == 926.46
    assert d["devices"]["SW_CAMPUS_4"]["status"] == "down"
    assert d["quality"]["score"] == 1.0
    assert d["quality"]["transformations"] == []


@pytest.mark.parametrize("name,expected", [
    ("latency_ms", "latency_ms"), ("rtt", "latency_ms"), ("latencyMs", "latency_ms"), ("avg_rtt", "latency_ms"),
    ("linkUtil", "link_utilization_pct"), ("cpuLoad", "cpu_utilization_pct"), ("egressDrops", "queue_drops"),
    ("ifInErrors", "interface_errors"), ("signalStrength", "rssi_dbm"), ("pktLossRate", "packet_loss_pct"),
    ("dns_failure_rate", "dns_failure_pct"), ("latancy_ms", "latency_ms"),
])
def test_field_names_map_to_canonical_metrics(name, expected):
    assert map_field(name)[0] == expected


def test_unknown_field_is_not_guessed():
    assert map_field("fanSpeedRpm")[0] is None


@pytest.mark.parametrize("raw,num,unit", [
    ("250ms", 250.0, "ms"), ("0.25 s", 0.25, "s"), ("94%", 94.0, "%"), ("-71 dBm", -71.0, "dBm"),
    ({"value": 3, "unit": "ms"}, 3.0, "ms"), (12, 12.0, None),
])
def test_values_with_units_are_parsed(raw, num, unit):
    assert parse_value(raw)[:2] == (num, unit)


@pytest.mark.parametrize("raw", ["N/A", None, "", float("nan"), True, "fast"])
def test_bad_values_are_flagged_not_used(raw):
    assert parse_value(raw)[0] is None


def test_units_are_converted_and_assumptions_reported():
    d = normalize([{"host": "AP_1", "ts": 1730000000, "rtt": "0.25 s", "linkUtil": 0.93, "pktLossRate": 0.02}])
    m = metrics(d, "AP_1")
    assert m["latency_ms"] == 250
    assert m["link_utilization_pct"] == 93
    assert m["packet_loss_pct"] == 2
    codes = [i["code"] for i in d["quality"]["issues"]]
    assert "unit_assumed" in codes
    assert d["quality"]["score"] < 1


def test_out_of_range_and_missing_device_are_rejected_with_reasons():
    d = normalize([{"device_id": "SW_1", "cpu_utilization_pct": 140, "latency_ms": 5},
                   {"latency_ms": 900}])
    assert "cpu_utilization_pct" not in metrics(d, "SW_1")
    codes = {i["code"] for i in d["quality"]["issues"]}
    assert {"out_of_range", "missing_device_id"} <= codes
    assert d["quality"]["records_rejected"] == 1


def test_epoch_seconds_milliseconds_and_iso_times():
    d = normalize([{"device_id": "A", "ts": 1730000000, "latency_ms": 1},
                   {"device_id": "B", "time": 1730000000000, "latency_ms": 1},
                   {"device_id": "C", "timestamp": "2024-10-27T03:33:20Z", "latency_ms": 1}])
    assert len({d["devices"][x]["last_seen"] for x in "ABC"}) == 1


def test_stale_samples_are_excluded():
    d = normalize([{"device_id": "A", "timestamp": "2026-10-31T09:00:00Z", "latency_ms": 900},
                   {"device_id": "A", "timestamp": "2026-10-31T10:00:00Z", "latency_ms": 10}])
    assert metrics(d, "A")["latency_ms"] == 10


def test_ndjson_with_a_broken_line_and_csv():
    nd = '{"device_id": "A", "latency_ms": 5}\n{broken\n{"device_id": "B", "latency_ms": 7}\n'
    d = normalize(nd)
    assert set(d["devices"]) == {"A", "B"}
    assert any(i["code"] == "malformed_json_line" for i in d["quality"]["issues"])
    d = normalize("device_id,latency_ms,cpu\nSW_1,12,40\nSW_2,15,35\n")
    assert metrics(d, "SW_2")["cpu_utilization_pct"] == 35


def test_newer_schema_version_is_parsed_best_effort():
    d = normalize({"schema_version": "3.0", "records": [{"device_id": "A", "latency_ms": 5}]})
    assert any(i["code"] == "unsupported_schema_version" for i in d["quality"]["issues"])
    assert d["quality"]["score"] < 1


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2, 3]", '{"records": [{"latency_ms": 5}]}'])
def test_unusable_input_raises_clear_error(raw):
    with pytest.raises(IngestError):
        normalize(raw)
