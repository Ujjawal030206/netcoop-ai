"""Run every scenario in data/scenarios/ and compare with the expected result in manifest.json.

    python tools/run_scenarios.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from netcoop.orchestrator import diagnose  # noqa: E402

DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"


def main():
    manifest = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
    passed = 0
    print(f"{'scenario':34} {'expected':24} {'got':24} {'conf':>5}  result")
    for name, exp in manifest.items():
        res = diagnose((DIR / name).read_text(encoding="utf-8"))
        r = res["report"]
        ok = r["status"] == exp["status"] and (exp.get("cause") is None or r.get("root_cause_id") == exp["cause"]) \
            and (not exp.get("device") or exp["device"] in r.get("affected_devices", []))
        passed += ok
        print(f"{name[:34]:34} {str(exp.get('cause') or exp['status'])[:24]:24} "
              f"{str(r.get('root_cause_id') or r['status'])[:24]:24} {r['confidence']:5.2f}  {'PASS' if ok else 'FAIL'}")
    print(f"\n{passed}/{len(manifest)} scenarios diagnosed as expected")
    return 0 if passed == len(manifest) else 1


if __name__ == "__main__":
    sys.exit(main())
