"""Command line interface.

    python -m netcoop diagnose data/scenarios/link_congestion.json
    python -m netcoop diagnose data/scenarios/weak_wifi.json --trace --markdown
    python -m netcoop diagnose - < telemetry.json          # read from stdin
    python -m netcoop diagnose file.json --json report.json --log messages.json
    python -m netcoop scenarios                            # run all bundled scenarios
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .orchestrator import diagnose

ROOT = Path(__file__).resolve().parent.parent
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
C = {"b": "\033[1m", "dim": "\033[2m", "r": "\033[31m", "g": "\033[32m", "y": "\033[33m",
     "c": "\033[36m", "m": "\033[35m", "x": "\033[0m"} if USE_COLOR else {k: "" for k in "b dim r g y c m x".split()}

TYPE_COLOR = {"TASK": "dim", "TELEMETRY": "c", "FINDING": "g", "QUERY": "m", "RESPONSE": "m",
              "ERROR": "r", "REPORT": "b"}


def print_trace(messages):
    print(f"\n{C['b']}Inter-agent messages (netcoop-msg/1.0){C['x']}")
    for m in messages:
        p = m["payload"]
        what = {"TASK": lambda: p.get("action"),
                "TELEMETRY": lambda: p.get("summary"),
                "FINDING": lambda: f"[{p.get('status')}] {p.get('summary')}",
                "QUERY": lambda: f"{p.get('question')}: {p.get('reason')}",
                "RESPONSE": lambda: p.get("detail"),
                "ERROR": lambda: p.get("message"),
                "REPORT": lambda: "final report"}.get(m["type"], lambda: "")()
        col = C[TYPE_COLOR.get(m["type"], "x")]
        print(f"  {col}{m['type']:9}{C['x']} {m['sender']:>20} -> {m['recipient']:<20} {str(what)[:150]}")


def print_report(r):
    status_col = {"diagnosed": "y", "healthy": "g", "insufficient_evidence": "m", "error": "r"}.get(r["status"], "x")
    print(f"\n{C['b']}Incident {r['incident_id']}{C['x']}  status: {C[status_col]}{r['status']}{C['x']}")
    print(f"{C['b']}Root cause:{C['x']} {r['suspected_root_cause']}")
    print(f"{C['b']}Confidence:{C['x']} {r['confidence']:.2f} ({r['confidence_category']})")
    if r.get("affected_devices"):
        print(f"{C['b']}Affected:{C['x']}   {', '.join(r['affected_devices'])}")
    if r.get("supporting_evidence"):
        print(f"{C['b']}Evidence:{C['x']}")
        for e in r["supporting_evidence"][:8]:
            unit = "" if e["unit"] in ("count", "state", "bool", "ratio") else f" {e['unit']}"
            print(f"   - {e['device_id']}: {e['metric']} = {e['value']}{unit}  ({e['interpretation']}) [{e['source']}]")
    for q in r.get("inter_agent_queries") or []:
        print(f"{C['m']}Asked {q['to']}:{C['x']} {q['answer']}")
    if r.get("alternative_causes"):
        print(f"{C['b']}Alternatives:{C['x']} " + "; ".join(r["alternative_causes"]))
    print(f"{C['b']}Actions:{C['x']}")
    for i, a in enumerate(r.get("recommended_actions", []), 1):
        print(f"   {i}. {a}")
    if r.get("limitations"):
        print(f"{C['b']}Limitations:{C['x']}")
        for x in r["limitations"]:
            print(f"   - {x}")


def cmd_diagnose(args):
    raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8", errors="replace")
    narrator = None
    if args.llm:
        from .llm import narrator as make_narrator
        narrator = make_narrator()
    crash = [a if a.endswith("_agent") else f"{a}_agent" for a in (args.crash or [])]
    res = diagnose(raw, crash_agents=crash, narrator=narrator)
    r = res["report"]
    if args.trace:
        print_trace(res["messages"])
    if args.markdown:
        print("\n" + res["markdown"])
    elif args.raw:
        print(json.dumps(r, indent=2))
    else:
        print_report(r)
    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=2), encoding="utf-8")
        print(f"\nreport written to {args.json}")
    if args.log:
        Path(args.log).write_text(json.dumps(res["messages"], indent=2, default=str), encoding="utf-8")
        print(f"message log written to {args.log}")
    print(f"{C['dim']}{len(res['messages'])} messages, {len(res['rejected_messages'])} rejected, {res['elapsed_ms']} ms{C['x']}")
    return 0 if r["status"] != "error" else 2


def cmd_scenarios(_args):
    sys.path.insert(0, str(ROOT / "tools"))
    import run_scenarios
    return run_scenarios.main()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="netcoop", description="NetCoop AI: multi-agent network fault diagnosis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagnose", help="diagnose a telemetry file (JSON, NDJSON or CSV; '-' for stdin)")
    d.add_argument("input")
    d.add_argument("--trace", action="store_true", help="print every inter-agent message")
    d.add_argument("--markdown", action="store_true", help="print the Markdown report")
    d.add_argument("--raw", action="store_true", help="print the JSON report")
    d.add_argument("--json", metavar="FILE", help="write the JSON report to FILE")
    d.add_argument("--log", metavar="FILE", help="write the full message log to FILE")
    d.add_argument("--crash", nargs="*", metavar="AGENT", help="simulate a crash of these agents (robustness demo)")
    d.add_argument("--llm", action="store_true", help="add an LLM-written plain-English summary (needs an API key)")
    d.set_defaults(fn=cmd_diagnose)
    s = sub.add_parser("scenarios", help="run every bundled scenario and check the expected diagnosis")
    s.set_defaults(fn=cmd_scenarios)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
