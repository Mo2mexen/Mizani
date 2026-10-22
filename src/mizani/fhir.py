"""FHIR R4 records for a committed triage, written through wso2's fhir-mcp-server (H2).

Only committed triages are written. Each becomes a Patient (the synthetic label only), an
Encounter with status "triaged" and the SATS colour as its priority, and Observations: the
vital signs with their FHIR R4 vital-signs LOINC codes, plus the TEWS score and the remaining
SATS inputs under a local code system, since they have no standard code.
"""

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from mcp.client import Client

from mizani import audit
from mizani.db import db
from mizani.schemas import AuditEntry

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
SATS = "urn:mizani:sats-adult"  # local codes for SATS concepts without a standard code
CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"

# FHIR R4 vital signs profile (hl7.org/fhir/R4/observation-vitalsigns.html); glucose: LOINC
VITAL_CODES = {
    "respiratory_rate": ("9279-1", "Respiratory rate", "/min", "vital-signs"),
    "heart_rate": ("8867-4", "Heart rate", "/min", "vital-signs"),
    "temperature": ("8310-5", "Body temperature", "Cel", "vital-signs"),
    "spo2": ("2708-6", "Oxygen saturation in Arterial blood", "%", "vital-signs"),
    "glucose": ("15074-8", "Glucose [Moles/volume] in Blood", "mmol/L", "laboratory"),
}
SATS_INPUTS = {
    "avpu": "Level of consciousness (AVPU)",
    "mobility": "Mobility",
    "trauma": "Trauma in the past 48 hours",
    "ketonuria": "Urine ketones",
}


def _category(code: str) -> list[dict]:
    return [{"coding": [{"system": CATEGORY, "code": code}]}]


def patient(record: dict) -> dict:
    label = record["patient_label"]
    return {
        "resourceType": "Patient",
        "identifier": [{"system": "urn:mizani:patient-label", "value": label}],
        "name": [{"text": label}],
    }


def encounter(record: dict, patient_ref: str) -> dict:
    decision = record["decision"]
    return {
        "resourceType": "Encounter",
        "identifier": [{"system": "urn:mizani:triage-id", "value": record["id"]}],
        "status": "triaged",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "EMER",
            "display": "emergency",
        },
        "priority": {
            "coding": [{"system": f"{SATS}:colour", "code": record["colour"]}],
            "text": f"SATS {record['colour']} ({decision['action']} by {record['approver']})",
        },
        "subject": {"reference": patient_ref},
        "period": {"start": record["arrived_at"]},
    }


def _observation(code: dict, value: dict, refs: tuple[str, str], when: str, category: str):
    patient_ref, encounter_ref = refs
    return {
        "resourceType": "Observation",
        "status": "final",
        "category": _category(category),
        "code": code,
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
        "effectiveDateTime": when,
        **value,
    }


def observations(record: dict, patient_ref: str, encounter_ref: str) -> list[dict]:
    vitals, proposal = record["vitals"], record["proposal"]
    refs, when = (patient_ref, encounter_ref), record["committed_at"]
    out = []
    for field, (code, display, unit, category) in VITAL_CODES.items():
        if vitals.get(field) is not None:
            loinc = {"coding": [{"system": LOINC, "code": code, "display": display}]}
            value = {"valueQuantity": {"value": vitals[field], "unit": unit, "system": UCUM}}
            out.append(_observation(loinc, value, refs, when, category))
    if vitals.get("systolic_bp") is not None:  # BP panel with the systolic component we collect
        panel = {"coding": [{"system": LOINC, "code": "85354-9", "display": "Blood pressure"}]}
        systolic = {
            "code": {"coding": [{"system": LOINC, "code": "8480-6", "display": "Systolic"}]},
            "valueQuantity": {"value": vitals["systolic_bp"], "unit": "mm[Hg]", "system": UCUM},
        }
        out.append(_observation(panel, {"component": [systolic]}, refs, when, "vital-signs"))
    for field, text in SATS_INPUTS.items():
        if vitals.get(field) is not None:
            code = {"coding": [{"system": SATS, "code": field}], "text": text}
            value = {"valueString": str(vitals[field]).lower()}
            out.append(_observation(code, value, refs, when, "survey"))
    tews = proposal["tews"]
    out.append(
        _observation(
            {"coding": [{"system": SATS, "code": "tews"}], "text": "SATS adult TEWS"},
            {
                "valueInteger": tews["total"],
                "note": [
                    {"text": f"{c['field']}: {c['points']} ({c['source']})"}
                    for c in tews["components"]
                ]
                + [{"text": f"Proposed colour: {proposal['source']}"}],
            },
            refs,
            when,
            "survey",
        )
    )
    return out


def _created_id(result: Any) -> str:
    if result.is_error:
        raise RuntimeError(" ".join(c.text for c in result.content))
    data = result.structured_content or json.loads(result.content[0].text)
    data = data.get("result", data) if isinstance(data, dict) else data
    if isinstance(data, list):  # some servers return the created resource in a list
        data = data[0]
    return data["id"]


async def write_triage(client, record: dict) -> dict:
    """Write one committed triage through an MCP client connected to wso2's fhir-mcp-server.
    Returns the FHIR ids. Refuses anything that a nurse hasn't committed."""
    if record["status"] != "committed":
        raise ValueError("only committed triages are written to FHIR")
    created = {}
    pid = _created_id(
        await client.call_tool("create", {"type": "Patient", "payload": patient(record)})
    )
    created["Patient"] = pid
    enc = encounter(record, f"Patient/{pid}")
    eid = _created_id(await client.call_tool("create", {"type": "Encounter", "payload": enc}))
    created["Encounter"] = eid
    created["Observation"] = [
        _created_id(await client.call_tool("create", {"type": "Observation", "payload": obs}))
        for obs in observations(record, f"Patient/{pid}", f"Encounter/{eid}")
    ]
    return created


def fhir_url() -> str | None:
    """wso2 fhir-mcp-server for the agent's commit step; unset means no FHIR output."""
    return os.environ.get("MIZANI_FHIR_MCP_URL") or None


async def write_committed(triage_id: str, url: str, mizani_server: Any = None) -> dict:
    """The agent's commit step (H2): write a committed triage to FHIR and log the call.

    The write goes to the borrowed MCP server, so our server's middleware never sees it; this
    function adds its audit row itself. A failure is reported, not raised: the triage is already
    committed, and the nurse sees that the FHIR copy is missing."""
    from mizani.agent.mcp_tools import read_json  # the agent's reader of our MCP resources

    started, ids, error = time.perf_counter(), None, None
    try:
        timeline = await read_json(f"patient://{triage_id}/timeline", mizani_server)
        async with Client(url, mode="legacy") as client:  # wso2 runs MCP SDK 1.x
            ids = await write_triage(client, timeline["triage"])
    except Exception as exc:
        error = f"FHIR write failed: {exc}"
    audit.record(
        db(),
        AuditEntry(
            time=datetime.now(UTC),
            tool="fhir_create",
            inputs={"triage_id": triage_id, "server": url},
            outputs={"error": error} if error else ids,
            error=error is not None,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        ),
        triage_id=triage_id,
    )
    return {"error": error} if error else ids
