import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
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

        approve = {"nurse_name": "Nurse A", "action": "approve", "triage_id": proposal["triage_id"]}
        done = api.post(f"/api/triage/{run}/decision", json=approve)
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
        shown = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
        no_name = {"nurse_name": " ", "action": "approve", "triage_id": shown}
        no_reason = {
            "nurse_name": "Nurse A",
            "action": "override",
            "colour": "red",
            "triage_id": shown,
        }
        no_proposal = {"nurse_name": "Nurse A", "action": "approve"}
        assert api.post(f"/api/triage/{run}/decision", json=no_proposal).status_code == 422
        assert api.post(f"/api/triage/{run}/decision", json=no_name).status_code == 422
        assert api.post(f"/api/triage/{run}/decision", json=no_reason).status_code == 422


def test_an_override_to_a_colour_off_the_chart_is_refused_and_the_run_keeps_waiting():
    with client(reply()) as api:
        run = intake(api)
        shown = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
        bad = {"nurse_name": "Nurse A", "action": "override", "colour": "purple", "reason": "x",
               "triage_id": shown}  # fmt: skip
        refused = api.post(f"/api/triage/{run}/decision", json=bad)
        assert refused.status_code == 409 and "not a SATS colour" in refused.json()["detail"]

        good = {**bad, "colour": "red", "reason": "pale"}
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

        # Approving the first proposal after the re-measure is refused, never moved to the new one
        stale = {"nurse_name": "Nurse A", "action": "approve",
                 "triage_id": first[-1][1]["proposal"]["triage_id"]}  # fmt: skip
        refused = api.post(f"/api/triage/{run}/decision", json=stale)
        assert refused.status_code == 409 and "proposal changed" in refused.json()["detail"]
        fresh = {**stale, "triage_id": proposal["triage_id"]}
        assert api.post(f"/api/triage/{run}/decision", json=fresh).json()["colour"] == "yellow"


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


def test_a_waiting_patient_is_retriaged_and_replaced_only_once_a_nurse_commits():
    worse = {"respiratory_rate": 30, "heart_rate": 135, "systolic_bp": 75}
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        first = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
        assert api.post(f"/api/queue/{first}/retriage", json={"vitals": worse}).status_code == 409
        approve = {"nurse_name": "Nurse A", "action": "approve"}
        api.post(f"/api/triage/{run}/decision", json={**approve, "triage_id": first})

        response = api.post(f"/api/queue/{first}/retriage", json={"vitals": worse})
        assert response.status_code == 202
        rerun = response.json()["triage_id"]
        twice = api.post(f"/api/queue/{first}/retriage", json={"vitals": worse})
        assert twice.json()["triage_id"] == rerun  # a double click reuses the open re-triage
        proposal = events(api, rerun, until="proposal")[-1][1]["proposal"]
        assert proposal["colour"] == "red"
        assert any(f["kind"] == "retriage" for f in proposal["flags"])
        assert [r["triage_id"] for r in api.get("/api/queue").json()] == [first]  # not yet

        done = api.post(
            f"/api/triage/{rerun}/decision", json={**approve, "triage_id": proposal["triage_id"]}
        )
        assert (done.status_code, done.json()["colour"]) == (200, "red")
        queue = api.get("/api/queue").json()
        assert [(r["triage_id"], r["colour"]) for r in queue] == [(proposal["triage_id"], "red")]
        stale = api.post(f"/api/queue/{first}/retriage", json={"vitals": worse})
        assert stale.status_code == 409
        assert api.post("/api/queue/nope/retriage", json={"vitals": worse}).status_code == 404


def committed_patient(api, run):
    shown = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
    approve = {"nurse_name": "Nurse A", "action": "approve", "triage_id": shown}
    return api.post(f"/api/triage/{run}/decision", json=approve).json()["triage_id"]


def test_the_model_drafts_a_handover_and_only_a_named_nurse_releases_it():
    text = "RR 12, HR 80, alert; chest pain\nOrange: chest pain (SATS chart)"
    with client(reply(ids=["chest_pain"]), AIMessage(content=text)) as api:
        triage_id = committed_patient(api, intake(api))
        draft = api.post(f"/api/queue/{triage_id}/handover")
        assert draft.status_code == 201 and draft.json()["status"] == "awaiting_release"
        # The third line, the waiting time, comes from the queue record, not from the model
        assert draft.json()["text"].splitlines()[2].startswith("Waited 0 min since arrival")
        draft_id = draft.json()["draft_id"]
        assert api.get(f"/api/queue/{triage_id}").json()["handovers"][0]["released_by"] is None

        wrong = api.post(f"/api/queue/{triage_id}/handover/{draft_id + 1}/release",
                         json={"nurse_name": "Nurse B"})  # fmt: skip
        assert wrong.status_code == 409
        done = api.post(f"/api/queue/{triage_id}/handover/{draft_id}/release",
                        json={"nurse_name": "Nurse B"})  # fmt: skip
        assert done.status_code == 200 and done.json()["approver"] == "Nurse B"
        rows = api.get("/api/audit", params={"triage_id": triage_id}).json()
    released = [r for r in rows if r["tool"] == "release_handover" and not r["error"]]
    assert released[0]["approver"] == "Nurse B"
    assert released[0]["inputs"]["approval_token"] == "***"


def test_shadow_mode_records_the_nurses_colour_before_the_decision():
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        events(api, run, until="proposal")
        mine = {"nurse_name": "Nurse C", "colour": "yellow", "reason": "walked in"}
        assert api.post(f"/api/triage/{run}/assessment", json=mine).status_code == 201
        assert api.post(f"/api/triage/{run}/assessment", json=mine).status_code == 409
        report = api.get("/api/shadow").json()
    assert (report["assessed"], report["agreed"]) == (1, 0)
    assert report["disagreements"][0]["mizani_colour"] == "orange"


def test_commit_writes_fhir_when_configured(monkeypatch):
    written = []

    async def fake_write(triage_id, url, server):
        written.append((triage_id, url))
        return {"Patient": "p1", "Encounter": "e1", "Observation": ["o1"]}

    monkeypatch.setenv("MIZANI_FHIR_MCP_URL", "http://fhir-mcp:8000/mcp/")
    monkeypatch.setattr("mizani.api.write_committed", fake_write)
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        triage_id = committed_patient(api, run)
        steps = [d for kind, d, _ in events(api, run, until="committed") if kind == "step"]
    assert written == [(triage_id, "http://fhir-mcp:8000/mcp/")]
    assert steps[-1]["name"] == "fhir_create" and steps[-1]["output"]["Encounter"] == "e1"


def test_the_built_nurse_page_is_served_beside_the_api(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<title>Mizani</title>")
    monkeypatch.setenv("MIZANI_WEB_DIR", str(tmp_path))
    with client() as api:
        page = api.get("/")
        colours = api.get("/api/colours")
    assert page.status_code == 200 and "<title>Mizani</title>" in page.text
    assert colours.status_code == 200 and colours.json()  # the API still wins under /api


def test_a_handover_with_a_wrong_number_is_redrafted_once_then_refused():
    made_up = AIMessage(content="SpO2 97%\nOrange: chest pain")
    fixed = AIMessage(content="HR 80\nOrange: chest pain")
    with client(reply(ids=["chest_pain"]), made_up, fixed, made_up, made_up) as api:
        triage_id = committed_patient(api, intake(api))
        assert api.post(f"/api/queue/{triage_id}/handover").status_code == 201
        refused = api.post(f"/api/queue/{triage_id}/handover")
    assert refused.status_code == 409 and "97 not in the patient's record" in refused.text


def test_an_overridden_handover_cites_the_nurse_not_the_rejected_proposal():
    from mizani.api import _why

    record = {
        "approver": "Nurse A",
        "decision": {"action": "override", "colour": "orange", "reason": "looks unwell"},
        "proposal": {"colour": "green", "source": "TEWS 1 -> green"},
    }
    assert _why(record).startswith("Nurse override by Nurse A: looks unwell")
    record["decision"] = {"action": "approve", "colour": None, "reason": None}
    assert _why(record) == "TEWS 1 -> green"
