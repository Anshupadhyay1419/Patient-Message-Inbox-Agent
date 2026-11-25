"""Patient-scoped reads and structured proposals for the inbox agent."""
from datetime import date
from typing import Literal

from langchain_core.tools import tool

from .db import InvalidProposal, appointment_facts, database

Queue = Literal["front_desk", "nurse", "clinician", "admin", "urgent_nurse"]
URGENT_REPLY = (
    "Your message has been flagged for urgent nurse review. If you think this may be "
    "an emergency, contact your local emergency services or go to the nearest emergency department now."
)
REPLIES = {
    "appointment": "We have received your appointment request. The front desk will follow up to confirm.",
    "front_desk": "The front desk will review your message and follow up.",
    "nurse": "A nurse will review your question and follow up.",
    "clinician": "A clinician will review your request and follow up.",
    "admin": "Our administrative team will review your request and follow up.",
}


def make_tools(path, records, patient, today):
    @tool
    def patient_summary() -> dict:
        """Read the verified patient's name, preferred language and usual doctor."""
        return records.summary(patient)

    @tool
    def get_appointments() -> list[dict]:
        """List only the verified patient's appointments, including status."""
        with database(path) as connection:
            return [
                dict(row) for row in connection.execute(
                    """SELECT appointment_id, practitioner_id, service, start, "end", status
                       FROM appointments WHERE patient_id=? ORDER BY start""", (patient,)
                )
            ]

    @tool
    def search_slots(
        date_from: str, date_to: str, time_of_day: Literal["any", "morning", "afternoon"] = "any",
        practitioner_id: str | None = None,
    ) -> list[dict]:
        """Find free slots in an inclusive YYYY-MM-DD range, optionally by doctor and time of day."""
        if date.fromisoformat(date_from) > date.fromisoformat(date_to):
            raise InvalidProposal("Invalid date range")
        with database(path) as connection:
            rows = connection.execute(
                """SELECT slot_id, practitioner_id, start, "end" FROM slots
                   WHERE status='free' AND SUBSTR(start,1,10) BETWEEN ? AND ?
                   AND (? IS NULL OR practitioner_id=?) ORDER BY start, practitioner_id""",
                (max(date_from, today), date_to, practitioner_id, practitioner_id),
            )
            return [
                dict(row) for row in rows
                if time_of_day == "any"
                or (int(row["start"][11:13]) < 12) == (time_of_day == "morning")
            ][:30]

    @tool
    def get_lab_status() -> list[dict]:
        """Read the verified patient's lab names, dates and pending/available status."""
        return records.labs(patient)

    @tool
    def get_active_medications() -> list[dict]:
        """List the verified patient's active medication names and request dates."""
        return records.medications(patient)

    @tool
    def propose_appointment_change(
        action: Literal["book", "reschedule", "cancel"],
        appointment_id: str | None = None,
        slot_id: str | None = None,
    ) -> dict:
        """Propose one explicit calendar request. Use IDs from the appointment and slot tools."""
        if bool(appointment_id) != (action != "book") or bool(slot_id) != (action != "cancel"):
            raise InvalidProposal("Appointment or slot ID is missing or unexpected")
        proposal = {"kind": "appointment", "action": action}
        if appointment_id:
            proposal["appointment_id"] = appointment_id
        if slot_id:
            proposal["slot_id"] = slot_id
        with database(path) as connection:
            appointment_facts(connection, patient, proposal, today)
        return proposal

    @tool
    def draft_reply(
        template: Literal["appointment", "appointment_status", "lab_status", "front_desk", "nurse", "clinician", "admin", "urgent"],
        record_id: str | None = None,
    ) -> dict:
        """Draft a reviewed reply from a template. For status templates provide one ID from the matching read tool."""
        text = REPLIES.get(template)
        if template == "urgent":
            text = URGENT_REPLY
        elif template == "lab_status":
            lab = next((row for row in records.labs(patient) if row["observation_id"] == record_id), None)
            if lab is None:
                raise InvalidProposal("Lab record is unavailable for this patient")
            text = f"The requested result is {lab['status']}. The clinic will follow up if needed."
        elif template == "appointment_status":
            appointment = next(
                (row for row in get_appointments.invoke({}) if row["appointment_id"] == record_id), None
            )
            if appointment is None:
                raise InvalidProposal("Appointment is unavailable for this patient")
            day, time = appointment["start"][:10], appointment["start"][11:16]
            if appointment["status"] == "booked":
                text = f"Our calendar shows an existing appointment on {day} at {time} (clinic local time)."
            else:
                text = "The appointment you asked about is listed as inactive. The front desk can help."
        proposal = {"kind": "reply", "text": text}
        if template == "lab_status":
            proposal["queue_hint"] = "nurse"
        return proposal

    @tool
    def route_to_staff(queue: Queue) -> dict:
        """Propose exactly one route to the most appropriate staff queue. Use urgent_nurse for warning signs."""
        return route(queue)

    return [
        patient_summary, get_appointments, search_slots, get_lab_status, get_active_medications,
        propose_appointment_change, draft_reply, route_to_staff,
    ]


def route(queue):
    reasons = {
        "front_desk": "Appointment or general triage",
        "nurse": "Result status or nursing question",
        "clinician": "Prescription or clinical decision",
        "admin": "Administrative request",
        "urgent_nurse": "Possible urgent warning sign",
    }
    return {
        "kind": "route", "queue": queue, "priority": "urgent" if queue == "urgent_nurse" else "routine",
        "reason": reasons[queue],
    }
