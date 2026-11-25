"""Reproduce the model comparison from freshly seeded SQLite databases."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import date

DEV = Path("data/messages_dev.json")
REDTEAM = Path("redteam_messages.json")


def command(*args, env=None):
    subprocess.run([sys.executable, *map(str, args)], env=env, check=True)


def run_suite(model, directory, suites, policy):
    directory.mkdir(parents=True, exist_ok=True)
    subset = ",".join(m["id"] for m in json.loads(DEV.read_text(encoding="utf-8")) if m.get("subset"))
    env = dict(os.environ, AGENT_MODEL=model, INBOX_ROUTING_POLICY=policy)
    manifest = {"date": date.today().isoformat(), "model": model, "temperature": 0,
                "routing_policy": policy, "runs": []}
    for name, dataset, ids in [
        ("development", DEV, None),
        *((f"repeat-{index}", DEV, subset) for index in range(1, 4)),
        ("redteam", REDTEAM, None),
    ]:
        if name.split("-")[0] not in suites:
            continue
        database = directory / f"{name}.db"
        output = directory / f"{name}.jsonl"
        command("reset_db.py", "--db", database, "--messages", dataset)
        command("-m", "clinic_inbox", "run", "--db", database, "--out", output,
                *(["--ids", ids] if ids else []), env=env)
        results = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        expected = set(ids.split(",")) if ids else {
            item["id"] for item in json.loads(dataset.read_text(encoding="utf-8"))
        }
        if len(results) != len(expected) or {r["message_id"] for r in results} != expected:
            raise RuntimeError(f"Unexpected result IDs in {output}")
        mean = sum(len(item["proposals"]) for item in results) / len(results)
        manifest["runs"].append({"name": name, "messages": len(results),
                                 "mean_proposals": mean, "review_minutes_per_300": mean * 100})
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        command("score.py", output, "--messages", dataset, "--show",
                "--json", directory / f"{name}-scores.json", *(["--ids", ids] if ids else []))
    if "repeat" in suites:
        command("score.py", *(directory / f"repeat-{i}.jsonl" for i in range(1, 4)),
                "--ids", subset, "--json", directory / "agreement.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", choices=("baseline", "grounded"), default="baseline")
    parser.add_argument("--suites", nargs="+", choices=("development", "repeat", "redteam"),
                        default=("development", "repeat", "redteam"))
    args = parser.parse_args()
    run_suite(args.model, args.output, args.suites, args.policy)
