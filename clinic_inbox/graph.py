"""LangGraph workflow, with an isolated interrupt and transactional resume."""
import json
import os
import re
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from score import reply_flags

from .db import database, decide, now
from .prompts import SYSTEM_PROMPT
from .screen import warning_signs
from .tools import URGENT_REPLY, make_tools, route

UNTRUSTED_AUTHORITY = re.compile(
    r"\b(?:ignore (?:your|all|previous)|system (?:note|instructions?)|developer override|"
    r"without (?:asking|approval)|mark (?:it|as) approved|cancel (?:all|every)|"
    r"bypass (?:review|approval)|verified as p\d+)\b", re.IGNORECASE,
)


class InboxState(MessagesState):
    proposals: list[dict]
    warnings: list[str]
    tool_calls: int
    proposal_ids: list[int]
    decision: dict


def build_graph(path, message, records, checkpointer, model_factory):
    message_id, patient = message["message_id"], message["patient_id"]
    today = message["sent"][:10]
    tools = make_tools(path, records, patient, today)
    by_name = {item.name: item for item in tools}
    grounded_fallback = os.environ.get("INBOX_ROUTING_POLICY", "grounded") == "grounded"
    bound = None

    def screen(state):
        return {"warnings": warning_signs(message["text"])}

    def urgent(state):
        return {"proposals": [route("urgent_nurse"), {"kind": "reply", "text": URGENT_REPLY}]}

    def agent(state):
        nonlocal bound
        if bound is None:
            bound = model_factory().bind_tools(tools)
        stamp = date.fromisoformat(today).strftime("%Y-%m-%d (%A)")
        dialog = [
            SystemMessage(content=f"{SYSTEM_PROMPT}\nMessage date: {stamp}."),
            HumanMessage(content=message["text"]),
            *state["messages"],
        ]
        return {"messages": [bound.invoke(dialog)]}

    def tool_node(state):
        proposals = list(state["proposals"])
        replies = []
        calls = state["messages"][-1].tool_calls
        for call in calls:
            result = by_name[call["name"]].invoke(call["args"])
            if isinstance(result, dict) and "kind" in result:
                proposals.append(result)
                result = {key: value for key, value in result.items() if key != "queue_hint"}
            replies.append(ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                name=call["name"], tool_call_id=call["id"],
            ))
        return {"messages": replies, "proposals": proposals, "tool_calls": state["tool_calls"] + len(calls)}

    def after_agent(state):
        response = state["messages"][-1]
        if response.invalid_tool_calls:
            raise ValueError("Model returned a malformed tool call")
        return "tools" if response.tool_calls else "record"

    def record(state):
        proposals = state["proposals"]
        routes = [item for item in proposals if item["kind"] == "route"]
        appointments = [p for p in proposals if p["kind"] == "appointment"]
        replies = [p for p in proposals if p["kind"] == "reply" and p["text"] != URGENT_REPLY]
        routing_fallback = len(routes) != 1
        grounded_appointment = grounded_fallback and not routes and len(appointments) == 1
        if routing_fallback:
            queue = "front_desk"
            if any(p["queue"] == "urgent_nurse" for p in routes):
                queue = "urgent_nurse"
            elif grounded_fallback and not routes and not appointments and len(replies) == 1:
                queue = replies[0].get("queue_hint", queue)
            selected = route(queue)
        else:
            selected = routes[0]
        if selected["queue"] == "urgent_nurse":
            final = [selected, {"kind": "reply", "text": URGENT_REPLY}]
        else:
            final = [selected]
            authority_override = UNTRUSTED_AUTHORITY.search(message["text"])
            if (len(appointments) == 1 and (not routing_fallback or grounded_appointment)
                    and selected["queue"] == "front_desk" and not authority_override):
                final.extend(appointments)
            final.extend({"kind": "reply", "text": reply["text"]} for reply in replies[:1])
        with database(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT proposal_id FROM proposals WHERE message_id=? ORDER BY proposal_id", (message_id,)
            ).fetchall()
            if existing:
                return {"proposal_ids": [row[0] for row in existing], "proposals": final}
            ids = []
            for proposal in final:
                flags = ["from_safety_screen"] if state["warnings"] else []
                if proposal.get("action") == "cancel":
                    flags.append("cancellation")
                if proposal["kind"] == "reply":
                    flags.extend(reply_flags(proposal["text"], patient, records.names, {}))
                if routing_fallback and proposal["kind"] == "route":
                    flags.append("routing_needs_review")
                if proposal["kind"] == "route" and UNTRUSTED_AUTHORITY.search(message["text"]):
                    flags.append("possible_authority_override")
                cursor = connection.execute(
                    """INSERT INTO proposals (message_id,patient_id,kind,payload,source,flags,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (message_id, patient, proposal["kind"], json.dumps(proposal),
                     "safety_screen" if state["warnings"] else "agent", json.dumps(flags), now()),
                )
                ids.append(cursor.lastrowid)
            connection.execute("UPDATE messages SET status='awaiting_review' WHERE message_id=?", (message_id,))
        return {"proposal_ids": ids, "proposals": final}

    def review(state):
        return {"decision": interrupt({"proposal_ids": state["proposal_ids"]})}

    def execute(state):
        decide(path, message_id, state["decision"])
        return {}

    graph = StateGraph(InboxState)
    for name, node in {
        "screen": screen, "urgent": urgent, "agent": agent, "tools": tool_node,
        "record": record, "review": review, "execute": execute,
    }.items():
        graph.add_node(name, node)
    graph.add_edge(START, "screen")
    graph.add_conditional_edges("screen", lambda state: "urgent" if state["warnings"] else "agent",
                                {"urgent": "urgent", "agent": "agent"})
    graph.add_edge("urgent", "record")
    graph.add_conditional_edges("agent", after_agent, {"tools": "tools", "record": "record"})
    graph.add_edge("tools", "agent")
    graph.add_edge("record", "review")
    graph.add_edge("review", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=checkpointer)


def config(message_id):
    return {"configurable": {"thread_id": message_id}, "recursion_limit": 24}
