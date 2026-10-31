"""Evidence scoring against the fault catalogue (documented in README, "How
confidence is calculated").

For one fault and one device:
  primary   = 0.5 * strongest primary metric + 0.5 * mean of available primary metrics
              (a candidate only if min_primary_abnormal primary metrics, default 1, are at least 'warning')
  support   = mean strength of supporting metrics on the device or anything below it
  base      = 0.75 * primary + 0.25 * support          (primary alone if no supporting data)
  contra    = strongest contradicting metric; a critical value counts fully, a warning 40%
              (with contradict_upstream, also checked on every device on the path upstream)
  coverage  = share of primary metrics that had data
  score     = (base - 0.4 * contra) * (0.75 + 0.25 * coverage), clamped to 0..1
Strength of a reading comes from knowledge.assess: 0 normal, 0.6 at warning, 0.9 at critical.
"""

from statistics import mean

from . import knowledge

PSEUDO = {"device_status", "telemetry_missing", "downstream_impact"}


def strength(ev):
    """Strength 0-1 of one evidence item (recomputed from its value so every agent agrees)."""
    m, v = ev["metric"], ev["value"]
    if m == "device_status":
        return knowledge.STATUS_STRENGTH.get(str(v), ("normal", 0.0))[1]
    if m == "telemetry_missing":
        return 1.0 if v is True else 0.0
    if m == "downstream_impact":
        n = float(v or 0)
        return 0.0 if n <= 0 else 0.6 if n < 2 else min(1.0, 0.85 + 0.05 * n)
    try:
        return knowledge.assess(m, float(v))[1]
    except (TypeError, ValueError):
        return 0.0


class EvidenceIndex:
    """All observations, indexed by (device, metric), plus the topology tree."""

    def __init__(self, observations, topology=None):
        self.items = {}
        for ev in observations:
            key = (ev.get("device_id"), ev["metric"])
            if key not in self.items or strength(ev) > strength(self.items[key]):
                self.items[key] = ev
        topo = (topology or {}).get("devices", {}) if isinstance(topology, dict) else {}
        self.types = {d: info.get("type") for d, info in topo.items()}
        self.children, self.parent = {}, {}
        for d, info in topo.items():
            up = info.get("upstream")
            if up:
                self.children.setdefault(up, []).append(d)
                self.parent[d] = up
        self.devices = sorted({d for d, _ in self.items if d})

    def get(self, device, metric):
        return self.items.get((device, metric))

    def s(self, device, metric):
        ev = self.get(device, metric)
        return None if ev is None else strength(ev)

    def descendants(self, device):
        out, stack = [], list(self.children.get(device, []))
        while stack:
            d = stack.pop()
            if d not in out:
                out.append(d)
                stack.extend(self.children.get(d, []))
        return out

    def ancestors(self, device):
        out, d = [], self.parent.get(device)
        while d and d not in out and len(out) < 32:
            out.append(d)
            d = self.parent.get(d)
        return out

    def network_mean(self, metric):
        vals = [strength(ev) for (d, m), ev in self.items.items()
                if m == metric and self.types.get(d) != "service"]
        return mean(vals) if vals else None


def evaluate(sig, device, idx, allowed=None):
    """Score one fault signature on one device. -> dict or None if not a candidate."""
    def ok(m):
        return allowed is None or m in allowed

    prim = [(m, idx.s(device, m)) for m in sig["primary"] if ok(m) and idx.s(device, m) is not None]
    if not prim or sum(1 for _, v in prim if v >= 0.6) < sig.get("min_primary_abnormal", 1):
        return None
    pv = [v for _, v in prim]
    primary = 0.5 * max(pv) + 0.5 * mean(pv)
    coverage = len(prim) / len(sig["primary"])

    scope = [device] + idx.descendants(device)
    sup, sup_ev = [], []
    for m in sig["supporting"]:
        if not ok(m):
            continue
        vals = [(idx.s(d, m), d) for d in scope if idx.s(d, m) is not None]
        if vals:
            best, d = max(vals)
            sup.append(best)
            if best >= 0.6:
                sup_ev.append(idx.get(d, m))
    base = 0.75 * primary + 0.25 * mean(sup) if sup else primary

    contra, contra_ev = 0.0, []
    is_service = idx.types.get(device) == "service"
    for m in sig["contradicting"]:
        if not ok(m):
            continue
        where = [device] + (idx.ancestors(device) if sig.get("contradict_upstream") else [])
        found = [(idx.s(d, m), d) for d in where if idx.s(d, m) is not None]
        v, at = max(found) if found else (None, None)
        if v is None and is_service:
            v = idx.network_mean(m)
        if v is None:
            continue
        eff = v if v >= 0.9 else 0.4 * v
        if eff > contra:
            contra = eff
        if v >= 0.6 and at and idx.get(at, m):
            contra_ev.append(idx.get(at, m))

    score = max(0.0, min(1.0, (base - 0.4 * contra) * (0.75 + 0.25 * coverage)))
    affected = [device] + sorted({e["device_id"] for e in sup_ev if e.get("device_id") and e["device_id"] != device})
    return {
        "device": device,
        "score": round(score, 3),
        "primary": round(primary, 3),
        "support": round(mean(sup), 3) if sup else None,
        "contra": round(contra, 3),
        "coverage": round(coverage, 2),
        "evidence": [idx.get(device, m) for m, v in prim if v >= 0.6] + sup_ev,
        "contradicting_evidence": contra_ev,
        "affected_devices": affected,
        "missing_primary": [m for m in sig["primary"] if ok(m) and idx.s(device, m) is None],
    }


def rank(idx, allowed=None, causes=None):
    """Best-scoring device per fault. -> list of (cause, result) sorted by score, high first."""
    out = []
    for cause, sig in knowledge.signatures().items():
        if causes and cause not in causes:
            continue
        if allowed is not None and not (set(sig["primary"]) & allowed):
            continue
        best = None
        for d in idx.devices:
            r = evaluate(sig, d, idx, allowed)
            if r and (best is None or r["score"] > best["score"]):
                best = r
        if best:
            # other devices showing the same fault with at least half the strength
            others = []
            for d in idx.devices:
                if d == best["device"]:
                    continue
                r = evaluate(sig, d, idx, allowed)
                if r and r["score"] >= 0.5 * best["score"]:
                    others.append(d)
            best["affected_devices"] = sorted(set(best["affected_devices"]) | set(others),
                                              key=lambda x: (x != best["device"], x))
            out.append((cause, best))
    out.sort(key=lambda x: -x[1]["score"])
    return out
