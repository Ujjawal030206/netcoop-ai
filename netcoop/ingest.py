"""Telemetry normalisation: any supported input shape -> one canonical dataset.

Accepted inputs (detected automatically, can be mixed in one batch):
  * the organiser's generator record   {timestamp, device_id, metrics{...}, status}
  * a NetCoop batch                    {schema_version, topology, records[...], services{...}}
  * legacy flat records                {"host": "AP-3", "ts": 1730000000, "rtt": "0.25 s", ...}
  * a JSON list, NDJSON lines or CSV text of any of the above

Every change made to the data is recorded as a transformation or an issue, so
the final report can say exactly what was assumed. Nothing is silently dropped.
"""

import csv
import difflib
import io
import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone

from .catalog import METRICS, STATUS_WORDS, STEMS, UNIT_TOKENS

SUPPORTED_SCHEMA_MAJORS = {0, 1}
STALE_AFTER = timedelta(minutes=15)

DEVICE_KEYS = ("device_id", "deviceid", "device", "host", "hostname", "node", "nodeid",
               "node_id", "id", "name", "sourcedevice", "switch", "ap")
TIME_KEYS = ("timestamp", "ts", "time", "collected_at", "collectedat", "@timestamp",
             "datetime", "date", "observed_at", "observedat")
STATUS_KEYS = ("status", "state", "health", "device_status", "devicestatus", "oper_status",
               "operstatus")
META_KEYS = {"type", "device_type", "devicetype", "role", "segment", "location", "zone",
             "upstream", "uplink", "parent", "units", "schema_version", "schemaversion",
             "version", "site", "building", "vendor", "model", "metrics", "tags", "notes"}

# Metrics whose typical values are far above 1, so a bare 0..1 number is almost
# certainly a fraction someone forgot to scale.
FRACTION_PRONE = {"link_utilization_pct", "cpu_utilization_pct", "memory_utilization_pct",
                  "dhcp_pool_utilization_pct"}
# For these, lower is worse, so the "worst" sample is the minimum.
LOWER_IS_WORSE = {"rssi_dbm", "snr_db"}

TIER = {"internet": 0, "wan": 0, "isp": 0, "gateway": 0, "router": 0, "firewall": 0,
        "core": 1, "distribution": 2, "switch": 3, "access": 3, "ap": 4, "wireless": 4,
        "client": 5, "host": 5, "service": 1, "server": 2}


class IngestError(ValueError):
    """Raised when the input contains nothing usable at all."""


class _Log:
    def __init__(self):
        self.issues = []
        self.transformations = []

    def issue(self, level, code, message, **ctx):
        self.issues.append({"level": level, "code": code, "message": message,
                            **{k: v for k, v in ctx.items() if v is not None}})

    def transform(self, **t):
        self.transformations.append(t)


# --------------------------------------------------------------------------
# field names, values and units
# --------------------------------------------------------------------------

def _tokens(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    return [t for t in re.split(r"[^a-zA-Z0-9%]+", name.lower()) if t]


_PREFIXES = ("avg", "average", "mean", "median", "cur", "current", "max", "peak",
             "p95", "p99", "total", "device", "port", "if")


def map_field(name, field_mapper=None):
    """Return (canonical metric, unit declared by the name, method) or (None, None, None)."""
    toks = _tokens(name)
    if not toks:
        return None, None, None
    name_unit = None
    if len(toks) > 1 and toks[-1] in UNIT_TOKENS:
        name_unit = UNIT_TOKENS[toks[-1]]
        toks = toks[:-1]
    ratio_hint = bool({"rate", "ratio", "fraction"} & set(toks))
    stem = "".join(toks)
    candidates = [stem]
    for p in _PREFIXES:
        if stem.startswith(p) and len(stem) > len(p) + 2:
            candidates.append(stem[len(p):])
    def result(canonical, method):
        unit_hint = name_unit
        if unit_hint is None and ratio_hint and METRICS[canonical][0] == "%":
            unit_hint = "ratio?"
        return canonical, unit_hint, method

    for c in candidates:
        if c in STEMS:
            return result(STEMS[c], "canonical" if str(name) == STEMS[c] else "alias")
    close = difflib.get_close_matches(stem, STEMS.keys(), n=1, cutoff=0.84)
    if close:
        return result(STEMS[close[0]], "fuzzy")
    if field_mapper:
        guess = field_mapper(str(name))
        if guess in METRICS:
            return result(guess, "llm")
    return None, None, None


_MISSING = {"", "na", "n/a", "null", "none", "nan", "-", "--", "unknown", "?", "missing"}
_NUM_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*([a-zA-Z%°/]*)$")


def parse_value(v):
    """-> (number or None, unit from the value or None, problem code or None)."""
    if isinstance(v, dict):
        inner = next((v[k] for k in ("value", "val", "v", "reading") if k in v), None)
        num, u, prob = parse_value(inner)
        declared = v.get("unit") or v.get("units")
        if declared and num is not None:
            u = UNIT_TOKENS.get(str(declared).strip().lower().replace("°", "deg"), u)
        return num, u, prob
    if v is None:
        return None, None, "missing"
    if isinstance(v, bool):
        return None, None, "invalid_type"
    if isinstance(v, (int, float)):
        if math.isnan(v) or math.isinf(v):
            return None, None, "not_a_number"
        return float(v), None, None
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in _MISSING:
            return None, None, "missing"
        m = _NUM_RE.match(s.replace(",", ""))
        if not m:
            return None, None, "unparseable"
        num = float(m.group(1))
        tok = m.group(2).lower().replace("°", "deg")
        if tok and tok not in UNIT_TOKENS:
            return None, None, "unknown_unit"
        return num, UNIT_TOKENS.get(tok) if tok else None, None
    return None, None, "invalid_type"


def convert(metric, value, src_unit):
    """Convert to the canonical unit. -> (value, rule text or None, assumed?, error or None)."""
    target = METRICS[metric][0]
    if src_unit in (None, target):
        if target == "%" and metric in FRACTION_PRONE and 0 < value <= 1.0:
            return value * 100, "bare 0-1 value read as a fraction -> %", True, None
        if metric == "rssi_dbm" and value > 0:
            return value / 2 - 100, "positive signal read as quality % -> dBm (q/2 - 100)", True, None
        return value, None, False, None
    if src_unit == "ratio?":
        if 0 <= value <= 1.0:
            return value * 100, "'rate' field with 0-1 value read as a fraction -> %", True, None
        return value, None, False, None
    table = {
        ("ms", "s"): (lambda x: x * 1000, "s -> ms (x1000)"),
        ("ms", "us"): (lambda x: x / 1000, "us -> ms (/1000)"),
        ("%", "ratio"): (lambda x: x * 100, "ratio -> % (x100)"),
        ("Mbps", "bps"): (lambda x: x / 1e6, "bps -> Mbps"),
        ("Mbps", "kbps"): (lambda x: x / 1e3, "kbps -> Mbps"),
        ("Mbps", "Gbps"): (lambda x: x * 1e3, "Gbps -> Mbps"),
        ("dBm", "%"): (lambda x: x / 2 - 100, "signal quality % -> dBm (q/2 - 100)"),
        ("degC", "degF"): (lambda x: (x - 32) * 5 / 9, "degF -> degC"),
        ("count", "%"): (None, None),
    }
    fn, rule = table.get((target, src_unit), (None, None))
    if fn is None:
        return None, None, False, f"unit '{src_unit}' is incompatible with {metric} ({target})"
    return fn(value), rule, src_unit == "%" and target == "dBm", None


def parse_time(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        x = float(v)
        if x > 1e11:      # epoch milliseconds
            x /= 1000
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_time(float(s))
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%d/%m/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                t = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _norm_status(v):
    if v is None:
        return None
    return STATUS_WORDS.get(str(v).strip().lower(), "unknown")


def _infer_type(device_id):
    d = device_id.upper()
    toks = set(re.split(r"[^A-Z0-9]+", d))
    if toks & {"DNS", "DHCP", "SVC"}:
        return "service"
    if toks & {"AP", "WAP", "WIFI", "WLAN"} or re.search(r"(^|_)AP\d", d):
        return "ap"
    if toks & {"CORE"}:
        return "core"
    if toks & {"DIST", "AGG"}:
        return "distribution"
    if toks & {"RTR", "ROUTER", "GW", "GATEWAY", "FW", "WAN", "ISP"}:
        return "router"
    if toks & {"SW", "SWITCH", "ACC"} or re.search(r"(^|_)SW", d):
        return "switch"
    if toks & {"PC", "HOST", "CLIENT", "LAB", "LAPTOP"}:
        return "client"
    return "unknown"


# --------------------------------------------------------------------------
# input shapes
# --------------------------------------------------------------------------

def _parse_text(text, log):
    text = text.strip().lstrip("﻿")
    if not text:
        raise IngestError("input is empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        first_err = e
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and lines[0].lstrip().startswith("{"):
        out = []
        for i, ln in enumerate(lines, 1):
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError as e:
                log.issue("error", "malformed_json_line", f"line {i} is not valid JSON ({e.msg}); skipped")
        if out:
            log.issue("info", "ndjson", f"read {len(out)} NDJSON records")
            return out
    if lines and ("," in lines[0]) and not lines[0].lstrip().startswith(("{", "[")):
        rows = list(csv.DictReader(io.StringIO(text)))
        if rows:
            log.issue("info", "csv", f"read {len(rows)} CSV rows")
            return rows
    raise IngestError(f"input is not valid JSON, NDJSON or CSV: {first_err.msg} "
                      f"(line {first_err.lineno}, column {first_err.colno})")


def _lower_keys(d):
    return {str(k).lower(): k for k in d}


def _pick(d, keys):
    lk = _lower_keys(d)
    for k in keys:
        if k in lk:
            return lk[k]
    return None


def _split_container(raw, log):
    """-> (records, topology, services, meta)."""
    meta = {}
    if isinstance(raw, list):
        return raw, None, None, meta
    if not isinstance(raw, dict):
        raise IngestError(f"expected a JSON object or list, got {type(raw).__name__}")
    for key in ("records", "telemetry", "samples", "data", "devices_telemetry", "measurements"):
        if isinstance(raw.get(key), list):
            meta = {k: v for k, v in raw.items() if k != key}
            return raw[key], raw.get("topology"), raw.get("services"), meta
    if "topology" in raw or "services" in raw:
        meta = {k: v for k, v in raw.items() if k not in ("topology", "services")}
        return [], raw.get("topology"), raw.get("services"), meta
    return [raw], None, None, meta


def _schema_check(meta, log):
    ver = meta.get("schema_version", meta.get("version"))
    penalty = 0.0
    if ver is None:
        return None, penalty
    m = re.match(r"v?(\d+)", str(ver))
    if not m:
        log.issue("warning", "bad_schema_version", f"schema_version '{ver}' is not a version number")
        return str(ver), 0.05
    major = int(m.group(1))
    if major not in SUPPORTED_SCHEMA_MAJORS:
        log.issue("warning", "unsupported_schema_version",
                  f"schema_version {ver} is newer than supported (0.x, 1.x); parsed best-effort "
                  f"by field-name mapping")
        penalty = 0.1
    elif major == 0:
        log.issue("info", "legacy_schema", f"legacy schema_version {ver}: flat fields mapped to 1.x names")
    return str(ver), penalty


def _parse_topology(topo, log):
    devices = {}
    if not topo:
        return devices, []
    if not isinstance(topo, dict):
        log.issue("warning", "bad_topology", "topology is not an object; ignored")
        return devices, []
    for d in topo.get("devices") or topo.get("nodes") or []:
        if not isinstance(d, dict):
            continue
        key = _pick(d, DEVICE_KEYS)
        if key is None:
            log.issue("warning", "topology_device_without_id", "a topology device has no id; ignored")
            continue
        did = str(d[key])
        devices[did] = {
            "type": str(d.get("type") or d.get("device_type") or d.get("role") or _infer_type(did)).lower(),
            "segment": d.get("segment") or d.get("zone") or d.get("location"),
            "upstream": d.get("upstream") or d.get("parent") or d.get("uplink"),
            "medium": d.get("medium"),
        }
    links = []
    for ln in topo.get("links") or topo.get("edges") or []:
        if isinstance(ln, dict):
            a = ln.get("a") or ln.get("source") or ln.get("from") or ln.get("src")
            b = ln.get("b") or ln.get("target") or ln.get("to") or ln.get("dst")
        elif isinstance(ln, (list, tuple)) and len(ln) == 2:
            a, b = ln
        else:
            continue
        if a and b:
            links.append((str(a), str(b)))
    # Orient undirected links by device tier when no parent was given.
    for a, b in links:
        for x in (a, b):
            devices.setdefault(x, {"type": _infer_type(x), "segment": None, "upstream": None, "medium": None})
        ta, tb = TIER.get(devices[a]["type"], 3), TIER.get(devices[b]["type"], 3)
        child, parent = (b, a) if ta < tb else (a, b) if tb < ta else (None, None)
        if child and not devices[child]["upstream"]:
            devices[child]["upstream"] = parent
    for did, d in devices.items():
        if d["upstream"] and d["upstream"] not in devices:
            devices[d["upstream"]] = {"type": _infer_type(d["upstream"]), "segment": None,
                                      "upstream": None, "medium": None}
    return devices, links


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------

def normalize(raw, now=None, field_mapper=None):
    """Normalise raw telemetry. Raises IngestError only if nothing is usable."""
    log = _Log()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        raw = _parse_text(raw, log)

    records, topo, services, meta = _split_container(raw, log)
    schema_version, penalty = _schema_check(meta, log)
    batch_units = meta.get("units") if isinstance(meta.get("units"), dict) else {}
    topo_devices, links = _parse_topology(topo, log)

    # Services block -> pseudo-device records, with the service name as a field prefix.
    if isinstance(services, dict):
        for svc, block in services.items():
            if isinstance(block, dict):
                rec = {"device_id": block.get("device_id") or f"SVC_{str(svc).upper()}",
                       "type": "service", "timestamp": block.get("timestamp"),
                       "_prefix": str(svc).lower(),
                       "metrics": {k: v for k, v in block.items() if k not in ("device_id", "timestamp")}}
                records = list(records) + [rec]

    devices = {}
    total = rejected = 0
    counts = {"assumed": 0, "fuzzy": 0, "out_of_range": 0, "no_time": 0, "bad_value": 0}
    for i, rec in enumerate(records):
        total += 1
        if not isinstance(rec, dict):
            rejected += 1
            log.issue("error", "record_not_object", f"record #{i} is a {type(rec).__name__}, not an object; rejected")
            continue
        dkey = _pick(rec, DEVICE_KEYS)
        if dkey is None or rec.get(dkey) in (None, ""):
            rejected += 1
            log.issue("error", "missing_device_id", f"record #{i} has no device identifier; rejected")
            continue
        did = str(rec[dkey]).strip()
        tkey = _pick(rec, TIME_KEYS)
        ts = parse_time(rec.get(tkey)) if tkey else None
        if ts is None:
            counts["no_time"] += 1
            log.issue("warning", "missing_timestamp" if not tkey or rec.get(tkey) in (None, "") else "bad_timestamp",
                      f"record #{i} ({did}) has no usable timestamp; treated as current", device_id=did)
        skey = _pick(rec, STATUS_KEYS)
        status = _norm_status(rec.get(skey)) if skey else None
        if status == "unknown":
            log.issue("warning", "unknown_status", f"{did}: status '{rec.get(skey)}' not recognised", device_id=did)

        if isinstance(rec.get("metrics"), dict):
            fields = rec["metrics"]
        elif "metrics" in rec and rec["metrics"] is not None:
            log.issue("error", "bad_metrics_block", f"record #{i} ({did}): 'metrics' is not an object", device_id=did)
            fields = {}
        else:
            lk_skip = {dkey, tkey, skey, "_prefix"}
            fields = {k: v for k, v in rec.items()
                      if k not in lk_skip and str(k).lower() not in META_KEYS}
        units = dict(batch_units)
        if isinstance(rec.get("units"), dict):
            units.update(rec["units"])

        metrics = {}
        prefix = rec.get("_prefix")
        for fname, fval in fields.items():
            canonical, name_unit, method = (None, None, None)
            if prefix:
                canonical, name_unit, method = map_field(f"{prefix}_{fname}", field_mapper)
                if canonical and not canonical.startswith(prefix):
                    canonical = None
            if not canonical:
                canonical, name_unit, method = map_field(fname, field_mapper)
            if not canonical:
                log.issue("info", "unknown_field", f"{did}: field '{fname}' has no known meaning; ignored",
                          device_id=did, field=str(fname))
                continue
            num, value_unit, prob = parse_value(fval)
            if prob:
                if prob != "missing":
                    counts["bad_value"] += 1
                log.issue("warning" if prob == "missing" else "error",
                          "missing_value" if prob == "missing" else f"bad_value_{prob}",
                          f"{did}: {fname}={fval!r} unusable ({prob.replace('_', ' ')}); ignored",
                          device_id=did, field=str(fname))
                continue
            declared = units.get(fname) or units.get(canonical)
            declared = UNIT_TOKENS.get(str(declared).lower()) if declared else None
            src_unit = value_unit or declared or name_unit
            value, rule, assumed, err = convert(canonical, num, src_unit)
            if err:
                counts["bad_value"] += 1
                log.issue("error", "incompatible_unit", f"{did}: {fname}: {err}; ignored",
                          device_id=did, field=str(fname))
                continue
            lo, hi = METRICS[canonical][1], METRICS[canonical][2]
            if value < lo or value > hi:
                if METRICS[canonical][0] == "%" and hi < value <= hi * 1.001:
                    value = hi
                else:
                    counts["out_of_range"] += 1
                    log.issue("error", "out_of_range",
                              f"{did}: {canonical}={round(value, 3)} outside valid range [{lo}, {hi}]; ignored",
                              device_id=did, field=str(fname))
                    continue
            if assumed:
                counts["assumed"] += 1
                log.issue("warning", "unit_assumed", f"{did}: {fname}={fval!r}: {rule}", device_id=did, field=str(fname))
            if method == "fuzzy":
                counts["fuzzy"] += 1
                log.issue("warning", "fuzzy_field_match", f"{did}: field '{fname}' matched to {canonical} by similarity",
                          device_id=did, field=str(fname))
            if method != "canonical" or rule:
                log.transform(device_id=did, field=str(fname), raw=fval, canonical=canonical,
                              value=round(value, 4), unit=METRICS[canonical][0],
                              rule=rule or f"renamed ({method})")
            if canonical in metrics:
                log.issue("warning", "duplicate_field", f"{did}: two fields map to {canonical}; kept the first",
                          device_id=did, field=str(fname))
                continue
            metrics[canonical] = value

        dev = devices.setdefault(did, {"id": did, "samples": [], "status": None, "meta": {}})
        for k in ("type", "device_type", "segment", "upstream", "parent", "uplink", "medium", "location", "zone"):
            if rec.get(k):
                dev["meta"][k] = rec[k]
        dev["samples"].append({"timestamp": ts, "metrics": metrics, "status": status})

    # Topology defaults for devices only seen in records.
    times = [s["timestamp"] for d in devices.values() for s in d["samples"] if s["timestamp"]]
    now = now or datetime.now(timezone.utc)
    newest = max(times) if times else now
    stale_count = 0
    out_devices = {}
    for did, dev in devices.items():
        topo_d = topo_devices.get(did, {})
        m = dev["meta"]
        dtype = str(m.get("type") or m.get("device_type") or topo_d.get("type") or _infer_type(did)).lower()
        info = {
            "id": did,
            "type": dtype,
            "segment": m.get("segment") or m.get("zone") or m.get("location") or topo_d.get("segment"),
            "upstream": m.get("upstream") or m.get("parent") or m.get("uplink") or topo_d.get("upstream"),
            "medium": m.get("medium") or topo_d.get("medium") or ("wireless" if dtype in ("ap", "wireless") else "wired"),
        }
        fresh = []
        for s in dev["samples"]:
            t = s["timestamp"] or newest
            if t > now + timedelta(minutes=5):
                log.issue("warning", "future_timestamp", f"{did}: timestamp {t.isoformat()} is in the future (clock skew?)",
                          device_id=did)
            if newest - t > STALE_AFTER:
                s["stale"] = True
                stale_count += 1
            else:
                fresh.append(s)
        stale_only = not fresh
        if stale_only and dev["samples"]:
            log.issue("warning", "stale_data", f"{did}: all samples are older than {int(STALE_AFTER.total_seconds() // 60)} "
                      f"min before the newest data; used but down-weighted", device_id=did)
            fresh = dev["samples"]
        elif any(s.get("stale") for s in dev["samples"]):
            log.issue("info", "stale_samples_dropped", f"{did}: stale samples excluded", device_id=did)
        fresh.sort(key=lambda s: s["timestamp"] or newest)
        agg = {}
        for metric in {k for s in fresh for k in s["metrics"]}:
            series = [((s["timestamp"] or newest).isoformat(), s["metrics"][metric]) for s in fresh if metric in s["metrics"]]
            vals = [v for _, v in series]
            worst = min(vals) if metric in LOWER_IS_WORSE else max(vals)
            agg[metric] = {"value": round(statistics.median(vals), 2), "worst": round(worst, 2),
                           "samples": len(vals), "unit": METRICS[metric][0], "series": series}
        statuses = [s["status"] for s in fresh if s["status"]]
        out_devices[did] = {**info, "status": statuses[-1] if statuses else None,
                            "stale": stale_only, "metrics": agg,
                            "last_seen": max((s["timestamp"] for s in dev["samples"] if s["timestamp"]),
                                             default=None)}
        if out_devices[did]["last_seen"]:
            out_devices[did]["last_seen"] = out_devices[did]["last_seen"].isoformat()

    for did, td in topo_devices.items():
        td.setdefault("medium", None)
    no_telemetry = sorted(d for d in topo_devices if d not in out_devices)
    for d in no_telemetry:
        if topo_devices[d]["type"] not in ("internet", "wan", "isp", "client", "host"):
            log.issue("warning", "no_telemetry", f"{d} is in the topology but sent no telemetry", device_id=d)

    usable = [d for d in out_devices.values() if d["metrics"] or d["status"]]
    if not usable:
        raise IngestError("no usable telemetry: " + "; ".join(i["message"] for i in log.issues[:5])
                          if log.issues else "no usable telemetry records found")

    score = 1.0 - penalty
    score -= min(0.3, 0.1 * rejected)
    score -= min(0.15, 0.03 * counts["assumed"])
    score -= min(0.1, 0.03 * counts["fuzzy"])
    score -= min(0.2, 0.05 * (counts["out_of_range"] + counts["bad_value"]))
    score -= min(0.15, 0.05 * stale_count)
    score -= min(0.1, 0.02 * counts["no_time"])
    score = round(max(0.3, score), 2)

    topology = {}
    for did in set(topo_devices) | set(out_devices):
        src = out_devices.get(did) or {}
        td = topo_devices.get(did, {})
        dtype = src.get("type") or td.get("type") or _infer_type(did)
        topology[did] = {"type": dtype,
                         "segment": src.get("segment") or td.get("segment"),
                         "upstream": src.get("upstream") or td.get("upstream"),
                         "medium": src.get("medium") or td.get("medium")
                         or ("wireless" if dtype in ("ap", "wireless") else "wired")}

    return {
        "schema": "netcoop-telemetry/1.0",
        "incident_id": meta.get("incident_id"),
        "source_schema_version": schema_version,
        "devices": out_devices,
        "topology": {"available": bool(topo_devices) or any(d["upstream"] for d in topology.values()),
                     "devices": topology, "links": [list(x) for x in links], "no_telemetry": no_telemetry},
        "quality": {"score": score, "records_total": total, "records_accepted": total - rejected,
                    "records_rejected": rejected, "issues": log.issues,
                    "transformations": log.transformations},
        "external_findings": meta.get("external_findings") or [],
    }
