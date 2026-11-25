"""SQLite operations for clinic proposals and approved actions."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class InvalidProposal(ValueError):
    """Proposal does not match the current clinic state."""


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database(path):
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def appointment_facts(connection, patient, proposal, today):
    action = proposal["action"]
    appointment = slot = None
    if action in {"cancel", "reschedule"}:
        appointment = connection.execute(
            "SELECT * FROM appointments WHERE appointment_id=? AND patient_id=? AND status='booked'",
            (proposal.get("appointment_id"), patient),
        ).fetchone()
        if appointment is None or appointment["start"][:10] < today:
            raise InvalidProposal("Appointment is unavailable for this patient")
    if action in {"book", "reschedule"}:
        slot = connection.execute(
            "SELECT * FROM slots WHERE slot_id=? AND status='free'", (proposal.get("slot_id"),)
        ).fetchone()
        if slot is None or slot["start"][:10] < today:
            raise InvalidProposal("Requested slot is unavailable")
        overlap = connection.execute(
            """SELECT 1 FROM appointments WHERE patient_id=? AND status='booked'
               AND start < ? AND "end" > ? AND appointment_id <> ?""",
            (patient, slot["end"], slot["start"], appointment["appointment_id"] if appointment else ""),
        ).fetchone()
        if overlap:
            raise InvalidProposal("Patient already has an overlapping appointment")
    return appointment, slot


def apply_appointment(connection, patient, proposal, today):
    appointment, slot = appointment_facts(connection, patient, proposal, today)
    if appointment:
        connection.execute("UPDATE slots SET status='free' WHERE slot_id=?", (appointment["slot_id"],))
    if slot:
        connection.execute("UPDATE slots SET status='busy' WHERE slot_id=?", (slot["slot_id"],))
    if proposal["action"] == "cancel":
        connection.execute(
            "UPDATE appointments SET status='cancelled' WHERE appointment_id=?",
            (appointment["appointment_id"],),
        )
    elif proposal["action"] == "reschedule":
        connection.execute(
            'UPDATE appointments SET practitioner_id=?, slot_id=?, start=?, "end"=? WHERE appointment_id=?',
            (slot["practitioner_id"], slot["slot_id"], slot["start"], slot["end"], appointment["appointment_id"]),
        )
    else:
        numbers = connection.execute(
            "SELECT MAX(CAST(SUBSTR(appointment_id, 2) AS INTEGER)) FROM appointments"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO appointments VALUES (?, ?, ?, ?, ?, ?, ?, 'booked')",
            (f"A{(numbers or 0) + 1:02d}", patient, slot["practitioner_id"], slot["slot_id"],
             "Consultation", slot["start"], slot["end"]),
        )


def decide(path, message_id, decision):
    reviewer = decision["reviewer"].strip()
    approve, reject = decision["approve"], decision["reject"]
    decisions = set(approve) | set(reject)
    with database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        message = connection.execute(
            "SELECT * FROM messages WHERE message_id=?", (message_id,)
        ).fetchone()
        if message["status"] == "done":
            return
        pending = connection.execute(
            "SELECT * FROM proposals WHERE message_id=? AND status='pending' ORDER BY proposal_id",
            (message_id,),
        ).fetchall()
        if (
            message["status"] != "awaiting_review" or not reviewer or not pending
            or len(decisions) != len(approve) + len(reject)
            or decisions != {row["proposal_id"] for row in pending}
        ):
            raise ValueError("Review must decide each pending proposal exactly once")
        patient = message["patient_id"]
        for row in pending:
            proposal_id = row["proposal_id"]
            payload = json.loads(row["payload"])
            approved = proposal_id in approve
            status = "applied" if approved else "rejected"
            result = {"status": "done" if approved else "rejected"}
            timestamp = now()
            if approved:
                try:
                    if row["patient_id"] != patient:
                        raise InvalidProposal("Proposal belongs to a different patient")
                    if row["kind"] == "appointment":
                        apply_appointment(connection, patient, payload, message["sent"][:10])
                    elif row["kind"] == "reply":
                        connection.execute(
                            "INSERT INTO outbox (proposal_id,patient_id,text,approved_by,at) VALUES (?,?,?,?,?)",
                            (proposal_id, patient, payload["text"], reviewer, timestamp),
                        )
                    else:
                        connection.execute(
                            """INSERT INTO staff_queue
                               (proposal_id,message_id,patient_id,queue,priority,reason,at)
                               VALUES (?,?,?,?,?,?,?)""",
                            (proposal_id, message_id, patient, payload["queue"], payload["priority"],
                             payload["reason"], timestamp),
                        )
                except InvalidProposal as error:
                    status = "failed"
                    result = {"status": status, "reason": str(error)}
            connection.execute("UPDATE proposals SET status=? WHERE proposal_id=?", (status, proposal_id))
            connection.execute(
                """INSERT INTO audit_log (at,proposal_id,reviewer,decision,flags_shown,result)
                   VALUES (?,?,?,?,?,?)""",
                (timestamp, proposal_id, reviewer, "approved" if approved else "rejected",
                 row["flags"], json.dumps(result)),
            )
        connection.execute("UPDATE messages SET status='done' WHERE message_id=?", (message_id,))
