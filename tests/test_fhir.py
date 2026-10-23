import json
from types import SimpleNamespace

import pytest

from mizani.fhir import encounter, observations, patient, write_triage
from mizani.rules.engine import propose
from mizani.rules.pack import load_pack
from mizani.schemas import SignClaim, Vitals

pytestmark = pytest.mark.anyio

VITALS = Vitals(
    respiratory_rate=24,
    heart_rate=118,
    systolic_bp=95,
    temperature=38.9,
    spo2=93,
    avpu="alert",
    mobility="with_help",
    trauma=False,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def committed(status="committed"):
    chest_pain = [SignClaim(id="chest_pain", quote="chest pain")]
    proposal = propose("t1", VITALS, chest_pain, load_pack(), "chest pain")
    return {
        "id": "t1",
        "patient_label": "Test Patient 01",
        "status": status,
        "colour": "red",
        "approver": "Nurse A",
        "decision": {"action": "approve", "colour": None, "reason": None},
        "arrived_at": "2026-10-22T10:00:00+00:00",
        "committed_at": "2026-10-22T10:05:00+00:00",
        "vitals": json.loads(VITALS.model_dump_json()),
        "proposal": json.loads(proposal.model_dump_json()),
    }


class FakeFhirMcp:
    def __init__(self):
        self.created = []

    async def call_tool(self, name, args):
        assert name == "create"
        self.created.append(args)
        rid = f"{args['type'].lower()}-{len(self.created)}"
        return SimpleNamespace(is_error=False, structured_content={"id": rid}, content=[])


def codes(resources):
    return {c["code"] for r in resources for c in r["code"]["coding"]}


def test_patient_carries_only_the_synthetic_label():
    p = patient(committed())
    assert p["name"] == [{"text": "Test Patient 01"}] and "birthDate" not in p


def test_encounter_is_triaged_with_the_sats_colour_and_approver():
    e = encounter(committed(), "Patient/p1")
    assert e["status"] == "triaged" and e["class"]["code"] == "EMER"
    assert e["priority"]["coding"][0]["code"] == "red" and "Nurse A" in e["priority"]["text"]


def test_vital_signs_use_the_fhir_r4_loinc_codes():
    obs = observations(committed(), "Patient/p1", "Encounter/e1")
    assert {"9279-1", "8867-4", "8310-5", "2708-6", "85354-9", "tews"} <= codes(obs)
    bp = next(o for o in obs if o["code"]["coding"][0]["code"] == "85354-9")
    assert bp["component"][0]["code"]["coding"][0]["code"] == "8480-6"
    assert all(o["subject"] == {"reference": "Patient/p1"} for o in obs)


def test_tews_observation_keeps_every_source():
    obs = observations(committed(), "Patient/p1", "Encounter/e1")
    tews = next(o for o in obs if o["code"]["coding"][0]["code"] == "tews")
    assert tews["valueInteger"] == 8
    assert all("sats_adult" in n["text"] for n in tews["note"][:-1])


async def test_write_triage_creates_patient_then_encounter_then_observations():
    client = FakeFhirMcp()
    ids = await write_triage(client, committed())
    types = [c["type"] for c in client.created]
    assert types[:2] == ["Patient", "Encounter"] and set(types[2:]) == {"Observation"}
    assert client.created[1]["payload"]["subject"] == {"reference": f"Patient/{ids['Patient']}"}
    assert len(ids["Observation"]) == len(types) - 2


async def test_pending_triages_are_never_written():
    with pytest.raises(ValueError, match="only committed"):
        await write_triage(FakeFhirMcp(), committed(status="pending"))
