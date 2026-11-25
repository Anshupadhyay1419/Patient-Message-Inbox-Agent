"""Evaluate the teaching screen independently from the chat model."""
import argparse
import json
from pathlib import Path

from clinic_inbox.screen import warning_signs


def evaluate(path):
    messages = json.loads(path.read_text(encoding="utf-8"))
    report = {"messages": len(messages), "tp": 0, "fn": [], "fp": []}
    for message in messages:
        predicted = bool(warning_signs(message["text"]))
        actual = message["label"]["red_flag"]
        if actual and predicted:
            report["tp"] += 1
        elif actual:
            report["fn"].append(message["id"])
        elif predicted:
            report["fp"].append(message["id"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", type=Path, nargs="+")
    for dataset in parser.parse_args().datasets:
        print(f"{dataset}: {json.dumps(evaluate(dataset))}")
