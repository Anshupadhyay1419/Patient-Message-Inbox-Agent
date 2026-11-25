"""Summarize audited evaluation evidence from completed result suites."""
import argparse
from collections import Counter
from contextlib import closing
from pathlib import Path
import json
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from clinic_inbox.screen import warning_signs

ROOT = Path(__file__).resolve().parent
CONFIGURATIONS = ("qwen", "qwen25", "qwen-grounded")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def trace(checkpoints, message_id):
    checkpoint = checkpoints.get({"configurable": {"thread_id": message_id}})
    dialog = checkpoint["channel_values"].get("messages", []) if checkpoint else []
    return [call["name"] for message in dialog for call in getattr(message, "tool_calls", [])]


def analyze_run(folder, name, dataset):
    expected = {row["id"]: row for row in read_json(dataset)}
    results = [json.loads(line) for line in (folder / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    database = folder / f"{name}.db"
    if not database.is_file():
        raise FileNotFoundError(database)
    evidence = []
    with SqliteSaver.from_conn_string(str(database)) as checkpoints:
        with closing(sqlite3.connect(database)) as connection:
            for result in results:
                message_id = result["message_id"]
                label = expected[message_id]["label"]
                proposals = result["proposals"]
                route = next((item for item in proposals if item["kind"] == "route"), {})
                records = connection.execute(
                    "SELECT flags FROM proposals WHERE message_id=?", (message_id,)
                ).fetchall()
                flags = sorted({flag for (values,) in records for flag in json.loads(values)})
                if name == "development" and route.get("queue") == label["queue"] and not flags and not result["error"]:
                    continue
                tools = trace(checkpoints, message_id)
                screen = warning_signs(expected[message_id]["text"])
                issue = None
                if result["error"]:
                    issue = "run error"
                elif route.get("queue") == "urgent_nurse" and not label["red_flag"]:
                    issue = "urgent false alarm"
                elif route.get("queue") != label["queue"]:
                    issue = "missing model route" if "routing_needs_review" in flags else "wrong queue"
                elif flags:
                    issue = "review flag"
                evidence.append({
                    "message": message_id, "expected": label["queue"], "actual": route.get("queue"),
                    "probe": label["probe"], "issue": issue, "error": result["error"], "flags": flags,
                    "screen": screen, "tools": tools, "proposal_kinds": [p["kind"] for p in proposals],
                })
    return evidence


def summarize():
    output = {}
    for configuration in CONFIGURATIONS:
        folder = ROOT / "results" / configuration
        analyses = {}
        for run, dataset in [
            ("development", ROOT / "data" / "messages_dev.json"),
            ("redteam", ROOT / "redteam_messages.json"),
        ]:
            if (folder / f"{run}-scores.json").exists():
                rows = analyze_run(folder, run, dataset)
                counts = Counter(row["issue"] for row in rows if row["issue"])
                analyses[run] = {"counts": dict(counts), "cases": rows}
        output[configuration] = analyses
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "case-analysis.json")
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
