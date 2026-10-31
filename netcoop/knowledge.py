"""Thresholds and the fault catalogue, loaded from config/ so they can be tuned
or extended without code changes."""

import json
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@lru_cache(maxsize=None)
def thresholds():
    data = json.loads((CONFIG_DIR / "thresholds.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=None)
def signatures():
    data = json.loads((CONFIG_DIR / "fault_signatures.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def assess(metric, value):
    """Grade one reading. -> (severity, strength 0-1, threshold used).

    strength is 0 for a normal value, 0.6 at the warning threshold, 0.9 at the
    critical threshold, and approaches 1.0 at twice the critical distance.
    """
    t = thresholds().get(metric)
    if t is None or value is None:
        return "normal", 0.0, None
    w, c = t["warning"], t["critical"]
    if t["direction"] == "low":                 # mirror so larger always means worse
        value, w, c = -value, -w, -c
    if value < w:
        return "normal", 0.0, t["warning"]
    if value < c:
        return "warning", round(0.6 + 0.3 * (value - w) / (c - w), 3), t["warning"]
    span = max(abs(c - w), 1e-9)
    return "critical", round(min(1.0, 0.9 + 0.1 * (value - c) / (2 * span)), 3), t["critical"]


STATUS_STRENGTH = {"down": ("critical", 1.0), "degraded": ("warning", 0.6),
                   "healthy": ("normal", 0.0), "unknown": ("normal", 0.0)}

INTERPRET = {
    "latency_ms": ("Normal latency", "Elevated network latency", "Severe network latency"),
    "jitter_ms": ("Normal jitter", "Elevated jitter", "Severe jitter (real-time traffic affected)"),
    "packet_loss_pct": ("No significant packet loss", "Packet loss present", "Heavy packet loss"),
    "link_utilization_pct": ("Link utilisation within capacity", "High link utilisation", "Link saturated"),
    "cpu_utilization_pct": ("CPU normal", "High CPU utilisation", "CPU saturated"),
    "memory_utilization_pct": ("Memory normal", "High memory utilisation", "Memory exhausted"),
    "queue_drops": ("No significant queue drops", "Elevated egress queue drops", "Heavy egress queue drops"),
    "interface_errors": ("Interface errors negligible", "Interface errors rising", "High interface error count"),
    "crc_errors": ("CRC errors negligible", "CRC errors present (possible cabling issue)", "High CRC errors (physical-layer fault)"),
    "link_flaps": ("Link stable", "Link flapped", "Link flapping repeatedly"),
    "temperature_c": ("Temperature normal", "Device running hot", "Device overheating"),
    "rssi_dbm": ("Wi-Fi signal adequate", "Weak Wi-Fi signal", "Very weak Wi-Fi signal"),
    "snr_db": ("Wi-Fi SNR adequate", "Low Wi-Fi SNR", "Very low Wi-Fi SNR (interference / distance)"),
    "retransmission_pct": ("Retransmissions normal", "Elevated Wi-Fi retransmissions", "Excessive Wi-Fi retransmissions"),
    "dns_failure_pct": ("DNS resolving normally", "DNS failures present", "DNS resolution failing"),
    "dns_response_ms": ("DNS response time normal", "Slow DNS responses", "DNS responses timing out"),
    "dhcp_failure_pct": ("DHCP leases normal", "DHCP failures present", "DHCP lease requests failing"),
    "dhcp_pool_utilization_pct": ("DHCP pool has headroom", "DHCP pool nearly full", "DHCP pool exhausted"),
}


def interpret(metric, severity):
    names = INTERPRET.get(metric)
    if not names:
        return f"{metric} {severity}"
    return names[{"normal": 0, "warning": 1, "critical": 2}[severity]]
