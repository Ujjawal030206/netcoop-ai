"""Robustness check: regenerate every scenario with many random seeds and score
hundreds of random records from the organisers' generator.

    python tools/stress_test.py [seeds] [records_per_type]
"""

import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_scenarios as g  # noqa: E402
from netcoop.orchestrator import diagnose  # noqa: E402

EXPECT = {"normal": "normal", "latency_spike": "path_latency", "hardware_failure": "hardware_failure"}


def scenario_sets(seed):
    random.seed(seed)
    hostel = ["HOSTEL_SW_01", "HOSTEL_AP_01", "HOSTEL_AP_02", "HOSTEL_PC_01"]
    return {
        "normal": (g.batch("n", {}), "normal"),
        "link_congestion": (g.batch("c", {"HOSTEL_DIST_SW_01": g.congestion_core, **{d: g.downstream_slow for d in hostel}}), "link_congestion"),
        "weak_wifi": (g.batch("w", {"HOSTEL_AP_02": g.weak_wifi}), "weak_wifi_signal"),
        "interface_fault": (g.batch("i", {"DEPT_SW_01": g.interface_fault, "DEPT_PC_01": g.lossy_client}), "interface_fault"),
        "dns_failure": (g.batch("d", {"SVC_DNS_01": g.dns_down}), "dns_failure"),
        "dhcp_failure": (g.batch("h", {"SVC_DHCP_01": g.dhcp_down}), "dhcp_failure"),
        "device_overload": (g.batch("o", {"CAMPUS_CORE_01": g.overload}), "device_overload"),
        "wan_latency": (g.batch("l", {"INTERNET_GW": g.wan_latency, **{d: g.wan_downstream for d in ("CAMPUS_CORE_01", "HOSTEL_PC_01", "DEPT_PC_01")}}), "path_latency"),
        "upstream_outage": (g.batch("u", {d: g.outage_child for d in hostel}, skip=("HOSTEL_DIST_SW_01",), statuses={d: "Critical" for d in hostel}), "upstream_device_outage"),
        "conflicting": (g.batch("x", {"HOSTEL_SW_01": g.congestion_access, "HOSTEL_AP_01": g.mild_wifi, "HOSTEL_PC_01": g.downstream_slow}), "link_congestion"),
        "interop": (g.interop_messy(), "link_congestion"),
    }


def main(seeds=30, per_type=100):
    totals, misses = Counter(), Counter()
    for seed in range(seeds):
        for name, (data, want) in scenario_sets(seed).items():
            got = diagnose(data)["report"].get("root_cause_id")
            totals[name] += 1
            if got != want:
                misses[(name, got)] += 1
    random.seed(1234)
    org_tot, org_miss, statuses = Counter(), Counter(), Counter()
    for kind, want in EXPECT.items():
        for _ in range(per_type):
            r = diagnose(g.organiser_record(kind))["report"]
            org_tot[kind] += 1
            statuses[(kind, r["status"], r["confidence_category"])] += 1
            if r.get("root_cause_id") != want:
                org_miss[(kind, r.get("root_cause_id") or r["status"])] += 1
    ok = sum(totals.values()) - sum(misses.values())
    print(f"campus scenarios: {ok}/{sum(totals.values())} correct over {seeds} seeds")
    for (name, got), n in misses.most_common():
        print(f"   {name}: {n} x {got}")
    ok2 = sum(org_tot.values()) - sum(org_miss.values())
    print(f"organiser generator records: {ok2}/{sum(org_tot.values())} correct")
    for (kind, got), n in org_miss.most_common():
        print(f"   {kind}: {n} x {got}")
    for (kind, st, cat), n in sorted(statuses.items()):
        print(f"   {kind:17} {st:22} {cat:12} {n}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
