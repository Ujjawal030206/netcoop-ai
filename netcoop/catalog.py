"""Canonical telemetry vocabulary: every metric the agents understand, its unit,
its valid range, and which agent owns it.

All incoming telemetry is translated into these names and units by the
ingestion agent before any analysis happens, so the analysis agents never see
vendor field names, seconds-vs-milliseconds, or fractions-vs-percent.
"""

import math

# canonical name -> (unit, min, max, owning agent, description)
METRICS = {
    # performance
    "latency_ms":                ("ms",    0, 600_000, "performance",    "Round-trip latency"),
    "jitter_ms":                 ("ms",    0, 600_000, "performance",    "Latency variation"),
    "packet_loss_pct":           ("%",     0, 100,     "performance",    "Share of packets lost"),
    "throughput_mbps":           ("Mbps",  0, 1e7,     "performance",    "Observed throughput"),
    "bandwidth_mbps":            ("Mbps",  0, 1e7,     "performance",    "Link capacity"),
    "link_utilization_pct":      ("%",     0, 100,     "performance",    "Link utilisation"),
    # infrastructure
    "cpu_utilization_pct":       ("%",     0, 100,     "infrastructure", "Device CPU utilisation"),
    "memory_utilization_pct":    ("%",     0, 100,     "infrastructure", "Device memory utilisation"),
    "queue_drops":               ("count", 0, math.inf, "infrastructure", "Egress queue drops in the interval"),
    "interface_errors":          ("count", 0, math.inf, "infrastructure", "Interface input/output errors"),
    "crc_errors":                ("count", 0, math.inf, "infrastructure", "CRC / FCS errors"),
    "link_flaps":                ("count", 0, math.inf, "infrastructure", "Link up/down transitions"),
    "temperature_c":             ("degC",  -40, 150,   "infrastructure", "Device temperature"),
    # wireless + services (connectivity agent)
    "rssi_dbm":                  ("dBm",   -120, 0,    "connectivity",   "Wi-Fi received signal strength"),
    "snr_db":                    ("dB",    -20, 100,   "connectivity",   "Wi-Fi signal-to-noise ratio"),
    "retransmission_pct":        ("%",     0, 100,     "connectivity",   "Wi-Fi frame retransmission rate"),
    "connected_clients":         ("count", 0, math.inf, "connectivity",  "Clients associated / attached"),
    "dns_failure_pct":           ("%",     0, 100,     "connectivity",   "Share of failed DNS queries"),
    "dns_response_ms":           ("ms",    0, 600_000, "connectivity",   "DNS response time"),
    "dhcp_failure_pct":          ("%",     0, 100,     "connectivity",   "Share of failed DHCP requests"),
    "dhcp_pool_utilization_pct": ("%",     0, 100,     "connectivity",   "DHCP address pool in use"),
}

# Field-name stems (lower-case, separators removed) -> canonical metric.
# A unit suffix is split off first, so "latency_s", "latencyMs" and "rtt_ms"
# all reach the stem table as "latency" / "rtt".
STEMS = {
    "latency": "latency_ms", "rtt": "latency_ms", "roundtrip": "latency_ms",
    "roundtriptime": "latency_ms", "delay": "latency_ms", "ping": "latency_ms",
    "avgrtt": "latency_ms", "avglatency": "latency_ms", "responsetime": "latency_ms",
    "jitter": "jitter_ms", "delayvariation": "jitter_ms", "pdv": "jitter_ms",
    "packetloss": "packet_loss_pct", "loss": "packet_loss_pct", "pktloss": "packet_loss_pct",
    "lossrate": "packet_loss_pct", "droprate": "packet_loss_pct", "packetlossrate": "packet_loss_pct",
    "pktlossrate": "packet_loss_pct",
    "throughput": "throughput_mbps", "tput": "throughput_mbps", "goodput": "throughput_mbps",
    "bandwidth": "bandwidth_mbps", "capacity": "bandwidth_mbps", "linkspeed": "bandwidth_mbps",
    "speed": "bandwidth_mbps",
    "linkutilization": "link_utilization_pct", "linkutil": "link_utilization_pct",
    "utilization": "link_utilization_pct", "util": "link_utilization_pct",
    "bandwidthutilization": "link_utilization_pct", "ifutil": "link_utilization_pct",
    "interfaceutilization": "link_utilization_pct", "linkload": "link_utilization_pct",
    "cpuutilization": "cpu_utilization_pct", "cpu": "cpu_utilization_pct",
    "cpuload": "cpu_utilization_pct", "cpuusage": "cpu_utilization_pct", "cpuutil": "cpu_utilization_pct",
    "memoryutilization": "memory_utilization_pct", "memory": "memory_utilization_pct",
    "mem": "memory_utilization_pct", "memutil": "memory_utilization_pct",
    "memusage": "memory_utilization_pct", "memoryusage": "memory_utilization_pct",
    "queuedrops": "queue_drops", "drops": "queue_drops", "egressdrops": "queue_drops",
    "outputdrops": "queue_drops", "qdrops": "queue_drops", "egressqueuedrops": "queue_drops",
    "txdrops": "queue_drops", "outdiscards": "queue_drops", "ifoutdiscards": "queue_drops",
    "interfaceerrors": "interface_errors", "ifaceerrors": "interface_errors",
    "iferrors": "interface_errors", "errors": "interface_errors", "inerrors": "interface_errors",
    "ifinerrors": "interface_errors", "porterrors": "interface_errors",
    "crcerrors": "crc_errors", "crc": "crc_errors", "fcserrors": "crc_errors",
    "linkflaps": "link_flaps", "flaps": "link_flaps", "flapcount": "link_flaps",
    "temperature": "temperature_c", "temp": "temperature_c",
    "rssi": "rssi_dbm", "signal": "rssi_dbm", "signalstrength": "rssi_dbm",
    "wifisignal": "rssi_dbm", "signallevel": "rssi_dbm",
    "snr": "snr_db", "signaltonoise": "snr_db", "signalnoiseratio": "snr_db",
    "retransmission": "retransmission_pct", "retransmissions": "retransmission_pct",
    "retransmissionrate": "retransmission_pct", "retries": "retransmission_pct",
    "retryrate": "retransmission_pct", "retrans": "retransmission_pct",
    "clients": "connected_clients", "connectedclients": "connected_clients",
    "clientcount": "connected_clients", "associatedclients": "connected_clients",
    "dnsfailure": "dns_failure_pct", "dnsfailures": "dns_failure_pct",
    "dnsfailurerate": "dns_failure_pct", "dnserrorrate": "dns_failure_pct", "dnsfail": "dns_failure_pct",
    "dnsresponse": "dns_response_ms", "dnslatency": "dns_response_ms",
    "dnsresponsetime": "dns_response_ms", "dnsrtt": "dns_response_ms",
    "dhcpfailure": "dhcp_failure_pct", "dhcpfailures": "dhcp_failure_pct",
    "dhcpfailurerate": "dhcp_failure_pct", "dhcpfail": "dhcp_failure_pct",
    "dhcppoolutilization": "dhcp_pool_utilization_pct", "dhcppool": "dhcp_pool_utilization_pct",
    "dhcppoolusage": "dhcp_pool_utilization_pct", "dhcpleaseutilization": "dhcp_pool_utilization_pct",
}

# Unit tokens that may appear as a field-name suffix or after a number in a
# string value ("250ms", "0.94 ratio", "-71 dBm"), mapped to a unit family.
UNIT_TOKENS = {
    "ms": "ms", "msec": "ms", "millis": "ms", "milliseconds": "ms",
    "s": "s", "sec": "s", "secs": "s", "seconds": "s",
    "us": "us", "usec": "us", "microseconds": "us",
    "pct": "%", "percent": "%", "percentage": "%", "%": "%",
    "ratio": "ratio", "frac": "ratio", "fraction": "ratio",
    "bps": "bps", "kbps": "kbps", "mbps": "Mbps", "gbps": "Gbps",
    "dbm": "dBm", "db": "dB",
    "count": "count", "cnt": "count", "total": "count",
    "c": "degC", "degc": "degC", "celsius": "degC", "f": "degF", "degf": "degF",
}

STATUS_WORDS = {
    "healthy": "healthy", "ok": "healthy", "up": "healthy", "normal": "healthy",
    "green": "healthy", "good": "healthy", "online": "healthy", "active": "healthy",
    "warning": "degraded", "warn": "degraded", "degraded": "degraded", "minor": "degraded",
    "yellow": "degraded", "amber": "degraded", "major": "degraded",
    "critical": "down", "crit": "down", "down": "down", "red": "down", "failed": "down",
    "fail": "down", "offline": "down", "unreachable": "down", "dead": "down",
}


def owner(metric: str) -> str:
    return METRICS[metric][3]


def unit(metric: str) -> str:
    return METRICS[metric][0]
