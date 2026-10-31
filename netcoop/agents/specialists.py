"""The three specialist analysis agents."""

from statistics import mean

from .. import knowledge, scoring
from .base import AnalysisAgent


def pearson(xs, ys):
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


class PerformanceAgent(AnalysisAgent):
    """Latency, jitter, packet loss, throughput, bandwidth and link utilisation."""
    name = "performance_agent"
    role = "telemetry_performance"
    owned = {"latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps", "bandwidth_mbps",
             "link_utilization_pct"}

    def answer_correlate_metrics(self, args):
        """Does latency rise and fall with utilisation over time? Yes -> congestion; no -> path issue."""
        x, y, device = args.get("x", "link_utilization_pct"), args.get("y", "latency_ms"), args.get("device")
        ys = self.series(device, y)
        if not ys:
            return None, None, [], f"{device}: no {y} time series to correlate"
        # load on this device or anywhere on its path upstream can explain its delay
        path, d = [device], self.upstream_of(device)
        while d and d not in path and len(path) < 16:
            path.append(d)
            d = self.upstream_of(d)
        best, most = None, 0
        for xdev in path:
            xs = self.series(xdev, x)
            if not xs:
                continue
            xmap = dict(xs)
            pairs = [(xmap[t], v) for t, v in ys if t in xmap]
            most = max(most, len(pairs))
            if len(pairs) < 4:
                continue
            r = pearson([a for a, _ in pairs], [b for _, b in pairs])
            if r is not None and (best is None or r > best[0]):
                best = (r, xdev, len(pairs))
        if best is None:
            return None, None, [], (f"only {most} paired sample(s) of {x} and {y} for {device}; need at least 4 "
                                    f"to test whether latency follows load")
        r, xdev, n = best
        pairs = range(n)
        ev = [{"metric": f"corr({x},{y})", "value": round(r, 2), "unit": "ratio", "device_id": device,
               "threshold": 0.6, "severity": "warning" if r >= 0.6 else "normal", "source": self.name,
               "interpretation": f"Pearson r over {len(pairs)} samples ({y} on {device} vs {x} on {xdev})"}]
        where = "its own link" if xdev == device else f"upstream device {xdev}"
        if r >= 0.6:
            return "link_congestion", "path_latency", ev, (f"latency on {device} tracks utilisation on {where} "
                                                          f"(r={r:.2f}, n={len(pairs)}): delay rises with load, the signature of congestion")
        if r <= 0.3:
            return "path_latency", "link_congestion", ev, (f"latency on {device} does not follow utilisation on any device "
                                                          f"on its path (best r={r:.2f}, n={len(pairs)}): the delay is not caused by local load")
        return None, None, ev, f"weak relationship between utilisation and latency (r={r:.2f}); inconclusive"

    def answer_baseline_connectivity(self, args):
        """Is the underlying network path healthy? If yes, a DNS/DHCP failure is the real cause."""
        devs = [d for d, info in self.telemetry["devices"].items() if info["type"] != "service"]
        bad, checked = [], 0
        for d in devs:
            for m in ("packet_loss_pct", "latency_ms"):
                info = self.telemetry["devices"][d]["metrics"].get(m)
                if info:
                    checked += 1
                    sev, _, _ = knowledge.assess(m, info["value"])
                    if sev != "normal":
                        bad.append(self.evidence(d, m, info))
        cause = args.get("cause")
        if checked == 0:
            return None, None, [], "no packet-loss or latency data to judge the network path"
        if not bad:
            return cause, None, [], (f"network path is healthy: packet loss and latency normal on all "
                                     f"{len(devs)} measured device(s), so the failure is in the service itself")
        share = len({e['device_id'] for e in bad}) / max(1, len(devs))
        if share >= 0.5:
            return None, cause, bad, (f"path problems on {share:.0%} of devices: the service failures are "
                                      f"likely a symptom of a wider network fault")
        return cause, None, bad, f"path problems limited to {len({e['device_id'] for e in bad})} device(s); service fault still most likely"


class InfrastructureAgent(AnalysisAgent):
    """Device health: CPU, memory, queues, interface errors, flaps, temperature, status."""
    name = "infrastructure_agent"
    role = "infrastructure_health"
    owned = {"cpu_utilization_pct", "memory_utilization_pct", "queue_drops", "interface_errors",
             "crc_errors", "link_flaps", "temperature_c", "device_status"}

    def extra_observations(self):
        out = []
        for did, dev in self.telemetry["devices"].items():
            st = dev.get("status")
            if st:
                sev, _ = knowledge.STATUS_STRENGTH.get(st, ("normal", 0.0))
                out.append({"metric": "device_status", "value": st, "unit": "state", "device_id": did,
                            "threshold": None, "severity": sev, "source": self.name,
                            "interpretation": {"down": "Device reports critical / down status",
                                               "degraded": "Device reports warning / degraded status"}
                            .get(st, "Device reports healthy status")})
        return out

    def answer_device_detail(self, args):
        """Hardware failure or overload? Look at status, errors and CPU/memory together."""
        d = args.get("device")
        dev = self.telemetry["devices"].get(d)
        if not dev:
            return None, None, [], f"no telemetry for {d}"
        m = dev["metrics"]
        def sev(k):
            return knowledge.assess(k, m[k]["value"])[0] if k in m else None
        ev = [self.evidence(d, k, m[k]) for k in ("cpu_utilization_pct", "memory_utilization_pct",
                                                    "interface_errors", "crc_errors", "temperature_c") if k in m]
        errors_bad = sev("interface_errors") in ("warning", "critical") or sev("crc_errors") in ("warning", "critical")
        load_bad = sev("cpu_utilization_pct") == "critical" or sev("memory_utilization_pct") == "critical"
        down = dev.get("status") == "down"
        if down and errors_bad:
            return "hardware_failure", "device_overload", ev, (f"{d} is in critical state AND logging interface errors: "
                                                               f"load alone does not corrupt frames, so a hardware fault is more likely than overload")
        if load_bad and not errors_bad:
            return "device_overload", "hardware_failure", ev, (f"{d} has saturated CPU/memory but clean interfaces: "
                                                               f"consistent with overload rather than failed hardware")
        if errors_bad and not load_bad:
            return "interface_fault", "device_overload", ev, f"{d}: errors without CPU/memory pressure point to a link/port fault"
        return None, None, ev, f"{d}: device detail does not separate the hypotheses"

    def answer_error_trend(self, args):
        """Are interface errors growing over time while the link is not saturated?"""
        d = args.get("device")
        for metric in ("crc_errors", "interface_errors"):
            s = self.series(d, metric)
            if len(s) >= 3:
                first, last = s[0][1], s[-1][1]
                util = (self.telemetry["devices"].get(d, {}).get("metrics", {}).get("link_utilization_pct") or {}).get("value")
                ev = [{"metric": f"{metric}_trend", "value": round(last - first, 1), "unit": "count", "device_id": d,
                       "threshold": 0, "severity": "warning" if last > first else "normal", "source": self.name,
                       "interpretation": f"{metric} went {first:g} -> {last:g} over {len(s)} samples"}]
                if last > first and (util is None or util < 90):
                    return "interface_fault", "link_congestion", ev, (f"{metric} on {d} keep rising ({first:g} -> {last:g}) while the "
                                                                      f"link is not saturated: a physical-layer fault, not congestion")
                if last <= first:
                    return None, "interface_fault", ev, f"{metric} on {d} are flat or falling ({first:g} -> {last:g}); fault may be historical"
        d_metrics = self.telemetry["devices"].get(d, {}).get("metrics", {})
        if "crc_errors" in d_metrics and knowledge.assess("crc_errors", d_metrics["crc_errors"]["value"])[0] != "normal":
            return "interface_fault", None, [self.evidence(d, "crc_errors", d_metrics["crc_errors"])], \
                f"{d} shows CRC errors (only one sample, no trend): CRC errors are a physical-layer signature"
        return None, None, [], f"not enough error samples on {d} to establish a trend"


class ConnectivityAgent(AnalysisAgent):
    """Topology and scope, Wi-Fi radio health, DNS and DHCP services, missing telemetry."""
    name = "connectivity_agent"
    role = "connectivity_analysis"
    owned = {"rssi_dbm", "snr_db", "retransmission_pct", "connected_clients", "dns_failure_pct",
             "dns_response_ms", "dhcp_failure_pct", "dhcp_pool_utilization_pct",
             "telemetry_missing", "downstream_impact"}

    def _topo(self):
        return self.telemetry["topology"]["devices"]

    def _children(self):
        ch = {}
        for d, info in self._topo().items():
            if info.get("upstream"):
                ch.setdefault(info["upstream"], []).append(d)
        return ch

    def _descendants(self, d):
        ch, out, stack = self._children(), [], list(self._children().get(d, []))
        while stack:
            x = stack.pop()
            if x not in out:
                out.append(x)
                stack.extend(ch.get(x, []))
        return out

    def degraded(self):
        """Devices with any abnormal reading or a non-healthy status."""
        bad = set()
        for d, dev in self.telemetry["devices"].items():
            if dev.get("status") in ("down", "degraded"):
                bad.add(d)
            for m, info in dev["metrics"].items():
                if knowledge.assess(m, info["value"])[0] != "normal":
                    bad.add(d)
        return bad

    def severe(self):
        """Devices that look cut off: critical/down status or heavy packet loss."""
        out = set()
        for d, dev in self.telemetry["devices"].items():
            loss = dev["metrics"].get("packet_loss_pct")
            if dev.get("status") == "down" or (loss and knowledge.assess("packet_loss_pct", loss["value"])[0] == "critical"):
                out.add(d)
        return out

    def extra_observations(self):
        out = []
        topo = self.telemetry["topology"]
        for d in topo["no_telemetry"]:
            if topo["devices"].get(d, {}).get("type") in ("internet", "wan", "isp", "client", "host"):
                continue
            below = self._descendants(d)
            severe = [x for x in below if x in self.severe()]
            # silent children only count as impact if reporting children confirm a hard failure;
            # otherwise silence may just be a monitoring gap
            impacted = severe + ([x for x in below if x in topo["no_telemetry"]] if severe else [])
            out.append({"metric": "telemetry_missing", "value": True, "unit": "bool", "device_id": d,
                        "threshold": None, "severity": "critical" if impacted else "warning", "source": self.name,
                        "interpretation": "Device is in the topology but sent no telemetry (unreachable or monitoring gap)"})
            out.append({"metric": "downstream_impact", "value": len(impacted), "unit": "count", "device_id": d,
                        "threshold": 2, "severity": "critical" if len(impacted) >= 2 else "warning" if impacted else "normal",
                        "source": self.name,
                        "interpretation": f"{len(impacted)} of {len(below)} downstream device(s) degraded or silent"})
        return out

    def context(self, observations):
        topo = self.telemetry["topology"]
        bad = sorted(self.degraded())
        ctx = {"topology_available": topo["available"], "degraded_devices": bad,
               "devices_without_telemetry": topo["no_telemetry"]}
        if not topo["available"]:
            ctx["note"] = "no topology supplied: fault scope limited to the devices that reported"
            return ctx
        def chain(d):
            out = [d]
            while self._topo().get(out[-1], {}).get("upstream") and len(out) < 20:
                out.append(self._topo()[out[-1]]["upstream"])
            return out
        if len(bad) >= 2:
            chains = [chain(d) for d in bad]
            common = [x for x in chains[0] if all(x in c for c in chains[1:])]
            if common:
                ctx["fault_domain"] = common[0]
                ctx["note"] = f"all {len(bad)} degraded devices sit below {common[0]}"
        elif len(bad) == 1:
            ctx["fault_domain"] = bad[0]
        segs = sorted({self._topo().get(d, {}).get("segment") for d in bad} - {None})
        if segs:
            ctx["affected_segments"] = segs
        return ctx

    def answer_scope_check(self, args):
        """Wireless-only problem, or are wired neighbours hit too?"""
        d = args.get("device")
        topo = self._topo()
        bad = self.degraded()
        if args.get("cause") == "upstream_device_outage":
            below = self._descendants(d)
            hit = [x for x in below if x in self.severe() or x in self.telemetry["topology"]["no_telemetry"]]
            if below and len(hit) >= max(1, len(below) // 2):
                return "upstream_device_outage", None, [], f"{len(hit)}/{len(below)} devices below {d} are degraded or silent"
            return None, "upstream_device_outage", [], f"only {len(hit)}/{len(below)} devices below {d} affected"
        up = topo.get(d, {}).get("upstream")
        seg = topo.get(d, {}).get("segment")
        peers = [x for x, info in topo.items() if x != d and x in self.telemetry["devices"]
                 and info.get("medium") == "wired" and info.get("type") not in ("service",)
                 and ((up and info.get("upstream") == up) or (seg and info.get("segment") == seg) or x == up)]
        if not topo or not peers:
            return None, None, [], f"no wired neighbours of {d} with telemetry to compare against"
        wired_bad = [x for x in peers if x in bad]
        if not wired_bad:
            return "weak_wifi_signal", "link_congestion", [], (f"wired devices sharing {up or seg} with {d} are healthy "
                                                              f"({', '.join(peers[:4])}): the problem is confined to the radio side")
        return None, "weak_wifi_signal", [], (f"wired neighbours are degraded too ({', '.join(wired_bad[:4])}): "
                                              f"the fault is upstream of the access point, not the Wi-Fi signal")
