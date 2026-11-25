"""Run and review synthetic clinic inbox messages."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .db import database
from .fhir import Records
from .graph import build_graph, config


def model_factory(model_id):
    def create():
        if not model_id:
            raise RuntimeError("Set AGENT_MODEL before processing routine messages")
        return init_chat_model(model_id, temperature=0)
    return create


def run(path, output, ids, model_id, create_model):
    records = Records()
    with database(path) as connection:
        messages = connection.execute(
            "SELECT * FROM messages WHERE status='new' ORDER BY sent,message_id"
        ).fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as checkpoints, output.open("w", encoding="utf-8") as stream:
        for message in messages:
            message_id = message["message_id"]
            if ids and message_id not in ids:
                continue
            result = {"message_id": message_id, "model": model_id, "error": None, "tool_calls": 0,
                      "proposals": []}
            graph = None
            try:
                graph = build_graph(path, message, records, checkpoints, create_model)
                state = graph.invoke({"messages": [], "proposals": [], "tool_calls": 0}, config(message_id))
                if not state.get("__interrupt__"):
                    raise RuntimeError("Message did not reach staff review")
                result.update(tool_calls=state["tool_calls"], proposals=state["proposals"])
            except Exception as error:
                result["error"] = type(error).__name__
                if graph is not None:
                    try:
                        result["tool_calls"] = graph.get_state(config(message_id)).values.get("tool_calls", 0)
                    except sqlite3.Error:
                        pass
                with database(path) as connection:
                    connection.execute(
                        "UPDATE messages SET status='error',error=? WHERE message_id=?",
                        (result["error"], message_id),
                    )
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()


def no_model():
    raise RuntimeError("Review cannot call a model")


def resume(path, checkpoints, records, message, decision):
    message_id = message["message_id"]
    graph = build_graph(path, message, records, checkpoints, no_model)
    thread = config(message_id)
    if graph.get_state(thread).next != ("review",):
        raise ValueError("Message has no active review checkpoint")
    graph.invoke(Command(resume=decision), thread)


def review(path, reviewer):
    records = Records()
    with database(path) as connection:
        pending = connection.execute(
            """SELECT p.*,m.text FROM proposals p JOIN messages m USING(message_id)
               WHERE p.status='pending' AND m.status='awaiting_review'
               ORDER BY p.message_id,p.proposal_id"""
        ).fetchall()
        messages = {row["message_id"]: row for row in connection.execute(
            "SELECT * FROM messages WHERE status='awaiting_review'"
        )}
    if not pending:
        print("No proposals awaiting review.")
        return
    with SqliteSaver.from_conn_string(str(path)) as checkpoints:
        for message_id, message in messages.items():
            print(f"\n{message_id} — {message['text']}")
            decision = {"reviewer": reviewer, "approve": [], "reject": []}
            for item in pending:
                if item["message_id"] != message_id:
                    continue
                flags = ", ".join(json.loads(item["flags"])) or "none"
                print(f"Proposal {item['proposal_id']}: {item['payload']}\nFlags: {flags}")
                while True:
                    answer = input("Approve [a], reject [r], or quit [q]? ").strip().lower()
                    if answer == "q":
                        return
                    if answer in {"a", "r"}:
                        decision["approve" if answer == "a" else "reject"].append(item["proposal_id"])
                        break
            resume(path, checkpoints, records, message, decision)
            with database(path) as connection:
                for row in connection.execute(
                    """SELECT proposal_id, status FROM proposals WHERE message_id=? ORDER BY proposal_id""",
                    (message_id,),
                ):
                    print(f"Proposal {row['proposal_id']}: {row['status']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--db", type=Path, required=True)
    runner.add_argument("--out", type=Path, required=True)
    runner.add_argument("--ids")
    reviewer = commands.add_parser("review")
    reviewer.add_argument("--db", type=Path, required=True)
    reviewer.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("Database does not exist. Run reset_db.py first.")
    if args.command == "run":
        if args.out.resolve() == args.db.resolve():
            parser.error("Results and database must be different files.")
        load_dotenv()
        model_id = os.environ.get("AGENT_MODEL", "")
        ids = {value.strip() for value in args.ids.split(",")} if args.ids else None
        run(args.db, args.out, ids, model_id or "unconfigured", model_factory(model_id))
    else:
        if not args.reviewer.strip():
            parser.error("Reviewer name is required.")
        review(args.db, args.reviewer.strip())


if __name__ == "__main__":
    main()
