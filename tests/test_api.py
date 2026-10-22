import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import NORMAL, FakeModel, reply

from mizani.api import create_app
from mizani.mcp_server import mcp
from mizani.rules.pack import load_pack


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")


def client(*replies):
    app = create_app(FakeModel(messages=iter(replies)), mcp, InMemorySaver())
    return TestClient(app)


def events(api, run_id, until, last_event_id=None):
    """Read the run's server-sent events up to and including the first `until` event."""
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    out, fields = [], {}
    with api.stream("GET", f"/api/triage/{run_id}/stream", headers=headers) as response:
        for line in response.iter_lines():
            if line:
                key, _, value = line.partition(": ")
                fields[key] = value
                continue
            if "event" not in fields:  # keep-alive comment
                fields = {}
                continue
            out.append((fields["event"], json.loads(fields["data"]), fields.get("id")))
            if fields["event"] == until:
                return out
            fields = {}
    return out


def intake(api, vitals=NORMAL, complaint="chest pain since morning"):
    body = {"patient_label": "Test Patient 01", "complaint": complaint, "vitals": vitals}
    response = api.post("/api/intake", json=body)
    assert response.status_code == 202
    return response.json()["triage_id"]


def test_intake_streams_the_steps_then_the_proposal_and_a_named_nurse_commits():
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        stream = events(api, run, until="proposal")
        steps = [data["name"] for kind, data, _ in stream if kind == "step"]
        assert steps[:2] == ["extract", "validate_vitals"] and "propose_priority" in steps
        proposal = stream[-1][1]["proposal"]
        assert (proposal["colour"], proposal["status"]) == ("orange", "pending")

        done = api.post(
            f"/api/triage/{run}/decision", json={"nurse_name": "Nurse A", "action": "approve"}
        )
        assert done.status_code == 200
        assert (done.json()["colour"], done.json()["approver"]) == ("orange", "Nurse A")

        assert events(api, run, until="committed")[-1][0] == "committed"
        (queued,) = api.get("/api/queue").json()
        assert queued["triage_id"] == proposal["triage_id"]
        assert queued["recheck_due"] is False and queued["target_minutes"] == 10
        rows = api.get("/api/audit", params={"triage_id": proposal["triage_id"]}).json()
        assert rows[-1]["tool"] == "commit_triage" and rows[-1]["approver"] == "Nurse A"
        assert api.get("/api/audit/verify").json() == {"intact": True, "first_broken_row": None}


def test_a_decision_needs_a_nurse_name_and_an_override_needs_a_reason():
    with client(reply()) as api:
        run = intake(api)
        events(api, run, until="proposal")
        no_name = {"nurse_name": " ", "action": "approve"}
        no_reason = {"nurse_name": "Nurse A", "action": "override", "colour": "red"}
        assert api.post(f"/api/triage/{run}/decision", json=no_name).status_code == 422
        assert api.post(f"/api/triage/{run}/decision", json=no_reason).status_code == 422


def test_an_override_to_a_colour_off_the_chart_is_refused_and_the_run_keeps_waiting():
    with client(reply()) as api:
        run = intake(api)
        events(api, run, until="proposal")
        bad = {"nurse_name": "Nurse A", "action": "override", "colour": "purple", "reason": "x"}
        refused = api.post(f"/api/triage/{run}/decision", json=bad)
        assert refused.status_code == 409 and "not a SATS colour" in refused.json()["detail"]

        good = {"nurse_name": "Nurse A", "action": "override", "colour": "red", "reason": "pale"}
        done = api.post(f"/api/triage/{run}/decision", json=good)
        assert (done.status_code, done.json()["colour"]) == (200, "red")
        again = api.post(f"/api/triage/{run}/decision", json=good)
        assert again.status_code == 409


def test_remeasured_vitals_stream_a_new_proposal():
    partial = {k: v for k, v in NORMAL.items() if k != "heart_rate"}
    with client(reply()) as api:
        run = intake(api, vitals=partial)
        first = events(api, run, until="proposal")
        assert any(f["kind"] == "incomplete" for f in first[-1][1]["proposal"]["flags"])

        response = api.post(f"/api/triage/{run}/vitals", json={"vitals": {"heart_rate": 135}})
        assert response.status_code == 202
        second = events(api, run, until="proposal", last_event_id=first[-1][2])
        assert second[0][1]["name"] == "remeasure"
        proposal = second[-1][1]["proposal"]
        assert proposal["colour"] == "yellow"
        assert not any(f["kind"] == "incomplete" for f in proposal["flags"])


def test_a_model_failure_ends_the_stream_with_an_error():
    with client() as api:  # the fake model has no replies left
        run = intake(api)
        kind, data, _ = events(api, run, until="error")[-1]
        assert kind == "error" and data["message"].startswith("The agent stopped")


def test_unknown_runs_and_colours():
    with client() as api:
        assert api.get("/api/triage/nope/stream").status_code == 404
        colours = api.get("/api/colours").json()
    expected = [(c.colour, c.target_minutes) for c in load_pack().colours]
    assert [(c["colour"], c["target_minutes"]) for c in colours] == expected
    assert all(c["source"] for c in colours)
