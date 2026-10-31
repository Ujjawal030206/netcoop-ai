"""Ablation: how much do collaboration and follow-up questions add?

Compares, on the same generated scenarios (30 seeds by default):
  * each specialist agent on its own   (its best local hypothesis, only its own metrics)
  * all agents combined, no follow-up questions
  * the full system (combined + follow-up QUERY/RESPONSE)

Two suites: the standard campus scenarios, and deliberately ambiguous ones in
which the readings sit near the thresholds so that levels alone cannot decide.

    python tools/ablation.py [seeds]
"""

import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_scenarios as g  # noqa: E402
from netcoop.orchestrator import diagnose  # noqa: E402
from stress_test import scenario_sets  # noqa: E402

SPECIALISTS = ("performance_agent", "infrastructure_agent", "connectivity_agent")
ORDER = [f"{a} alone" for a in SPECIALISTS] + ["combined, no follow-up questions", "full system"]
SHORT = ["perf", "infra", "conn", "no-FU", "full"]


def local_answer(messages, agent):
    for m in messages:
        if m["type"] == "FINDING" and m["sender"] == agent:
            p = m["payload"]
            if p["hypotheses"]:
                return p["hypotheses"][0]["cause"]
            return "normal" if p["status"] == "no_anomalies" else None
    return None


def suite(name, seeds):
    for seed in range(seeds):
        if name == "standard":
            yield from scenario_sets(seed).items()
        else:
            random.seed(1000 + seed)
            yield from g.ambiguous_sets().items()


def run(suite_name, seeds):
    correct, per_scenario, conf = Counter(), defaultdict(Counter), defaultdict(list)
    total = 0
    for name, (data, want) in suite(suite_name, seeds):
        total += 1
        full = diagnose(data)
        flat = diagnose(data, follow_up=False)
        answers = {"full system": full["report"].get("root_cause_id"),
                   "combined, no follow-up questions": flat["report"].get("root_cause_id")}
        conf["full system"].append(full["report"]["confidence"])
        conf["combined, no follow-up questions"].append(flat["report"]["confidence"])
        for a in SPECIALISTS:
            answers[f"{a} alone"] = local_answer(full["messages"], a)
        for k, v in answers.items():
            correct[k] += v == want
            per_scenario[name][k] += v == want
    print(f"{'configuration':36} accuracy   mean confidence   ({total} diagnoses)")
    for k in ORDER:
        c = f"{statistics.mean(conf[k]):.2f}" if conf[k] else "  -"
        print(f"{k:36} {correct[k] / total:6.1%}   {c:>15}   {correct[k]}/{total}")
    print(f"\n{'scenario':22}" + "".join(f"{s:>7}" for s in SHORT) + f"   (correct out of {seeds})")
    for name, c in per_scenario.items():
        print(f"{name:22}" + "".join(f"{c[k]:>7}" for k in ORDER))


def main(seeds=30):
    for name in ("standard", "ambiguous"):
        print(f"\n=== {name} scenarios ===")
        run(name, seeds)


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:2]))
