"""Read-only access to the synthetic FHIR fixture."""
import json
from collections import defaultdict
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent / "data" / "clinic_bundle.json"


class Records:
    def __init__(self, path=BUNDLE):
        self.resources = defaultdict(dict)
        for entry in json.loads(Path(path).read_text(encoding="utf-8"))["entry"]:
            item = entry["resource"]
            self.resources[item["resourceType"]][item["id"]] = item
        self.names = {
            pid: self.name(patient) for pid, patient in self.resources["Patient"].items()
        }

    @staticmethod
    def name(resource):
        name = resource["name"][0]
        return " ".join([*name.get("given", []), name["family"]])

    def summary(self, patient_id):
        patient = self.resources["Patient"][patient_id]
        doctor_id = patient["generalPractitioner"][0]["reference"].split("/")[-1]
        return {
            "name": self.names[patient_id],
            "language": patient["communication"][0]["language"]["text"],
            "usual_doctor": self.name(self.resources["Practitioner"][doctor_id]),
            "usual_doctor_id": doctor_id,
        }

    def labs(self, patient_id):
        return [
            {
                "observation_id": obs["id"],
                "test": obs["code"]["coding"][0]["display"],
                "date": obs.get("effectiveDateTime"),
                "status": "available" if obs["status"] in {"final", "amended", "corrected"}
                and "valueQuantity" in obs else "pending",
            }
            for obs in self.resources["Observation"].values()
            if obs["subject"]["reference"] == f"Patient/{patient_id}"
        ]

    def medications(self, patient_id):
        return [
            {"medication": item["medicationCodeableConcept"]["text"], "date": item["authoredOn"]}
            for item in self.resources["MedicationRequest"].values()
            if item["subject"]["reference"] == f"Patient/{patient_id}" and item["status"] == "active"
        ]
