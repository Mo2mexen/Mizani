import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import NORMAL, FakeModel, person, reply

from mizani import __version__, auth
from mizani.api import SESSION_COOKIE, create_app
from mizani.db import db
from mizani.mcp_server import mcp
from mizani.rules.pack import load_pack


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")


def session(who="Nurse A", role="nurse", chief=False):
    """A signed-in session for this person, opened directly (the sign-in itself: test_auth.py)."""
    return auth.open_session(db(), person(who, role, chief))


def signed_in(app, who="Nurse A", role="nurse", base_url="http://127.0.0.1"):
    """The page's client: signed in, and sending the header every change needs."""
    return TestClient(
        app,
        base_url=base_url,  # a name the API answers to
        headers={"X-Mizani": "1"},
        cookies={SESSION_COOKIE: session(who, role)},
    )


def client(*replies, who="Nurse A", role="nurse"):
    app = create_app(FakeModel(messages=iter(replies)), mcp, InMemorySaver())
    return signed_in(app, who, role)


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

        approve = {"action": "approve", "triage_id": proposal["triage_id"]}
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


def test_a_decision_takes_no_typed_name_and_an_override_needs_a_reason():
    with client(reply()) as api:
        run = intake(api)
        shown = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
        typed_name = {"nurse_name": "Someone Else", "action": "approve", "triage_id": shown}
        no_reason = {"action": "override", "colour": "red", "triage_id": shown}
        no_proposal = {"action": "approve"}
        assert api.post(f"/api/triage/{run}/decision", json=no_proposal).status_code == 422
        assert api.post(f"/api/triage/{run}/decision", json=typed_name).status_code == 422
        assert api.post(f"/api/triage/{run}/decision", json=no_reason).status_code == 422


def test_an_override_to_a_colour_off_the_chart_is_refused_and_the_run_keeps_waiting():
    with client(reply()) as api:
        run = intake(api)
        shown = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
        bad = {"action": "override", "colour": "purple", "reason": "x", "triage_id": shown}
        refused = api.post(f"/api/triage/{run}/decision", json=bad)
        assert (
            refused.status_code == 409
            and "not a colour of the chart in use" in refused.json()["detail"]
        )

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
        stale = {"action": "approve", "triage_id": first[-1][1]["proposal"]["triage_id"]}
        refused = api.post(f"/api/triage/{run}/decision", json=stale)
        assert refused.status_code == 409 and "proposal changed" in refused.json()["detail"]
        fresh = {**stale, "triage_id": proposal["triage_id"]}
        assert api.post(f"/api/triage/{run}/decision", json=fresh).json()["colour"] == "yellow"


def test_when_the_model_does_not_answer_the_nurse_still_gets_the_score_from_the_vitals():
    with client() as api:  # the fake model has no replies left
        run = intake(api)
        stream = events(api, run, until="proposal")
        steps = {data["name"]: data for kind, data, _ in stream if kind == "step"}
        assert steps["fallback"]["output"]["warning"].startswith("The model did not answer")
        proposal = stream[-1][1]["proposal"]
        assert proposal["colour"] == "green"
        assert "signs_not_checked" in [f["kind"] for f in proposal["flags"]]


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
        approve = {"action": "approve"}
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
    approve = {"action": "approve", "triage_id": shown}
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

        wrong = api.post(f"/api/queue/{triage_id}/handover/{draft_id + 1}/release")
        assert wrong.status_code == 409
        done = api.post(f"/api/queue/{triage_id}/handover/{draft_id}/release")
        assert done.status_code == 200 and done.json()["approver"] == "Nurse A"
        rows = api.get("/api/audit", params={"triage_id": triage_id}).json()
    released = [r for r in rows if r["tool"] == "release_handover" and not r["error"]]
    assert released[0]["approver"] == "Nurse A"
    assert released[0]["inputs"]["approval_token"] == "***"


def test_shadow_mode_records_the_nurses_colour_before_the_decision():
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        events(api, run, until="proposal")
        mine = {"colour": "yellow", "reason": "walked in"}
        assert api.post(f"/api/triage/{run}/assessment", json=mine).status_code == 201
        assert api.post(f"/api/triage/{run}/assessment", json=mine).status_code == 409
        report = api.get("/api/shadow").json()
    assert (report["assessed"], report["agreed"]) == (1, 0)
    assert report["disagreements"][0]["mizani_colour"] == "orange"


def test_commit_writes_fhir_when_configured(monkeypatch):
    written = []

    async def fake_write(triage_id, url, server, scale):
        written.append((triage_id, url, scale))
        return {"Patient": "p1", "Encounter": "e1", "Observation": ["o1"]}

    monkeypatch.setenv("MIZANI_FHIR_MCP_URL", "http://fhir-mcp:8000/mcp/")
    monkeypatch.setattr("mizani.api.write_committed", fake_write)
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        triage_id = committed_patient(api, run)
        steps = [d for kind, d, _ in events(api, run, until="committed") if kind == "step"]
    pack = load_pack()  # the record names the rule pack that scored it
    assert written == [(triage_id, "http://fhir-mcp:8000/mcp/", (pack.id, pack.name))]
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


def test_a_handover_with_extra_lines_is_tidied_not_refused():
    wordy = AIMessage(
        content="- HR 80, alert. The patient has waited 1321 minutes. 20\n- Orange: chest pain\n"
        "chest pain since morning\nWaited 20 minutes"
    )
    with client(reply(ids=["chest_pain"]), wordy) as api:
        triage_id = committed_patient(api, intake(api))
        draft = api.post(f"/api/queue/{triage_id}/handover")
    assert draft.status_code == 201
    first, second, waited = draft.json()["text"].splitlines()
    assert (first, second) == ("HR 80, alert.", "Orange: chest pain chest pain since morning")
    assert waited.startswith("Waited 0 min")  # the record's wait, not the model's "20 minutes"


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


def test_the_signed_in_nurse_calls_a_patient_in_and_the_card_leaves_the_waiting_room():
    with client(reply(ids=["chest_pain"])) as api:
        triage_id = committed_patient(api, intake(api))
        typed_name = api.post(f"/api/queue/{triage_id}/called-in", json={"nurse_name": "X"})
        # a body is not read: the name is always the signed-in account's
        assert typed_name.status_code == 200 and typed_name.json()["approver"] == "Nurse A"
        assert api.get("/api/queue").json() == []
        again = api.post(f"/api/queue/{triage_id}/called-in")
        assert again.status_code == 409
        assert api.get(f"/api/queue/{triage_id}").json()["triage"]["called_in_by"] == "Nurse A"
        rows = api.get("/api/audit", params={"triage_id": triage_id}).json()
    (logged,) = [r for r in rows if r["tool"] == "record_called_in" and not r["error"]]
    assert logged["approver"] == "Nurse A" and logged["inputs"]["approval_token"] == "***"


def test_a_doctor_reads_the_released_handover_then_sees_the_patient():
    text = "RR 12, HR 80, alert; chest pain\nOrange: chest pain (SATS chart)"
    with client(reply(ids=["chest_pain"]), AIMessage(content=text)) as api:
        triage_id = committed_patient(api, intake(api))  # as Nurse A
        draft_id = api.post(f"/api/queue/{triage_id}/handover").json()["draft_id"]
        api.post(f"/api/queue/{triage_id}/handover/{draft_id}/release")

        api.cookies.set(SESSION_COOKIE, session("Doctor D", "doctor"))
        (waiting,) = api.get("/api/queue").json()
        (handover,) = api.get("/api/handovers").json()
        assert waiting["triage_id"] == handover["triage_id"] == triage_id
        assert handover["text"].startswith("RR 12") and handover["released_by"] == "Nurse A"
        decide = {"action": "approve", "triage_id": triage_id}
        assert api.post("/api/triage/x/decision", json=decide).status_code == 403
        seen = api.post(f"/api/queue/{triage_id}/called-in")
        assert seen.status_code == 200 and seen.json()["approver"] == "Doctor D"
        assert api.get("/api/queue").json() == [] and api.get("/api/handovers").json() == []


def test_a_handover_draft_says_so_when_the_model_does_not_answer():
    with client(reply(ids=["chest_pain"])) as api:  # no reply left for the draft
        triage_id = committed_patient(api, intake(api))
        draft = api.post(f"/api/queue/{triage_id}/handover")
    assert draft.status_code == 503 and "write the handover yourself" in draft.json()["detail"]


def test_the_second_look_is_a_setting(monkeypatch):
    monkeypatch.setenv("MIZANI_SECOND_LOOK", "1")
    took_back = AIMessage(
        "",
        tool_calls=[
            {
                "name": "match_discriminators",
                "args": {"discriminators": []},
                "id": "r",
                "type": "tool_call",
            }
        ],
    )
    with client(reply(ids=["chest_pain"]), took_back) as api:
        stream = events(api, intake(api), until="proposal")
    steps = [data["name"] for kind, data, _ in stream if kind == "step"]
    assert "review" in steps
    flags = [f["kind"] for f in stream[-1][1]["proposal"]["flags"]]
    assert "sign_withdrawn" in flags and stream[-1][1]["proposal"]["colour"] == "green"


def test_a_slow_handover_draft_states_the_wait_at_the_time_it_is_saved(monkeypatch):
    clock = [datetime.now(UTC)]
    monkeypatch.setattr("mizani.store.now", lambda: clock[0])

    def model():
        yield reply(ids=["chest_pain"])
        clock[0] += timedelta(minutes=20)  # the model takes its time over the draft
        yield AIMessage(content="RR 12, HR 80, alert; chest pain\nOrange: chest pain (SATS chart)")

    app = create_app(FakeModel(messages=model()), mcp, InMemorySaver())
    with signed_in(app) as api:
        triage_id = committed_patient(api, intake(api))
        draft = api.post(f"/api/queue/{triage_id}/handover")
    assert draft.status_code == 201
    assert draft.json()["text"].splitlines()[2].startswith("Waited 20 min since arrival")


def test_a_recheck_after_the_model_failed_still_says_the_signs_were_not_checked():
    with client() as api:  # no model replies: the intake falls back to the vitals alone
        triage_id = committed_patient(api, intake(api))
        response = api.post(
            f"/api/queue/{triage_id}/retriage", json={"vitals": {"heart_rate": 135}}
        )
        proposal = events(api, response.json()["triage_id"], until="proposal")[-1][1]["proposal"]
    kinds = [f["kind"] for f in proposal["flags"]]
    assert "signs_not_checked" in kinds and "retriage" in kinds


def test_a_recheck_on_a_stale_card_of_a_called_in_patient_is_refused_at_once():
    with client(reply(ids=["chest_pain"])) as api:
        triage_id = committed_patient(api, intake(api))
        api.post(f"/api/queue/{triage_id}/called-in")
        stale = api.post(f"/api/queue/{triage_id}/retriage", json={"vitals": NORMAL})
    assert stale.status_code == 409 and "still in the queue" in stale.json()["detail"]


def test_a_commit_that_cannot_reach_the_server_goes_back_to_the_nurse(monkeypatch):
    original, down = StructuredTool.ainvoke, [True]

    async def restarting(self, input, config=None, **kwargs):
        if self.name == "commit_triage" and down:
            down.clear()
            raise ConnectionError("mizani-mcp is restarting")
        return await original(self, input, config, **kwargs)

    monkeypatch.setattr(StructuredTool, "ainvoke", restarting)
    with client(reply(ids=["chest_pain"])) as api:
        run = intake(api)
        shown = events(api, run, until="proposal")[-1][1]["proposal"]["triage_id"]
        approve = {"action": "approve", "triage_id": shown}
        refused = api.post(f"/api/triage/{run}/decision", json=approve)
        assert refused.status_code == 409 and "did not answer" in refused.json()["detail"]
        done = api.post(f"/api/triage/{run}/decision", json=approve)  # still waiting for her
    assert (done.status_code, done.json()["colour"]) == (200, "orange")


def test_after_an_api_restart_a_reconnecting_page_resumes_at_the_right_event():
    partial = {k: v for k, v in NORMAL.items() if k != "heart_rate"}
    saver = InMemorySaver()
    before = create_app(FakeModel(messages=iter([reply()])), mcp, saver)
    with signed_in(before) as api:
        run = intake(api, vitals=partial)
        first = events(api, run, until="proposal")
        api.post(f"/api/triage/{run}/vitals", json={"vitals": {"heart_rate": 135}})
        second = events(api, run, until="proposal", last_event_id=first[-1][2])
    # The API restarts and rebuilds the run from its checkpoint; the page reconnects with the
    # id of the last event it got before the re-measure.
    after = create_app(FakeModel(messages=iter([])), mcp, saver)
    with signed_in(after) as api:
        resumed = events(api, run, until="proposal", last_event_id=first[-1][2])
    assert [(kind, i) for kind, _, i in resumed] == [(kind, i) for kind, _, i in second]
    assert resumed[-1][1]["proposal"] == second[-1][1]["proposal"]


def test_the_api_answers_only_to_this_machines_names(monkeypatch):
    with client() as api:
        assert api.get("/api/colours").status_code == 200
        rebound = api.get("/api/queue", headers={"host": "evil.example"})
    assert rebound.status_code == 400  # DNS rebinding: a page elsewhere cannot reach it
    monkeypatch.setenv("MIZANI_ALLOWED_HOSTS", "127.0.0.1,triage.ward.local")
    app = create_app(FakeModel(messages=iter([])), mcp, InMemorySaver())
    with signed_in(app, base_url="http://triage.ward.local") as api:
        assert api.get("/api/colours").status_code == 200  # a ward server adds its own name


CLOUD = {
    "location": "cloud",
    "base_url": "https://gpu.example/v1",
    "model": "qwen3.5:9b",
    "api_key": "sk-secret-123",
}


def test_the_admin_moves_the_model_without_a_restart_and_the_key_never_comes_back(monkeypatch):
    built = []

    def chat_model(choice=None):  # the model the next patient gets; it names chest pain
        built.append(choice)
        return FakeModel(messages=iter([reply(ids=["chest_pain"])]))

    monkeypatch.setattr("mizani.api.chat_model", chat_model)
    with client(who="Admin E", role="admin") as api:  # starts on a model with no replies
        now = api.get("/api/admin/model").json()
        assert (now["location"], now["has_key"]) == ("this_server", False)
        unconfirmed = api.post("/api/admin/model", json=CLOUD)
        assert unconfirmed.status_code == 422 and "leave this hospital" in unconfirmed.text
        plain = {**CLOUD, "base_url": "http://gpu.example/v1", "leaves_hospital_confirmed": True}
        assert api.post("/api/admin/model", json=plain).status_code == 422  # https only
        for odd in (
            "http://ollama:11434/api/pull?x=",
            "https://u:p@gpu.example/v1",
            "https://g/v1#x",
        ):
            odd_test = {"location": "hospital", "base_url": odd, "model": "m"}
            refused = api.post("/api/admin/model/test", json=odd_test)
            assert refused.status_code == 422 and "without ?, #" in refused.text
        saved = api.post("/api/admin/model", json={**CLOUD, "leaves_hospital_confirmed": True})
        assert saved.json()["location"] == "cloud" and saved.json()["has_key"] is True
        assert "sk-secret-123" not in saved.text + api.get("/api/admin/model").text
        assert (built[-1]["model"], built[-1]["api_key"]) == ("qwen3.5:9b", "sk-secret-123")
        blank_key = {**CLOUD, "api_key": "", "leaves_hospital_confirmed": True}
        api.post("/api/admin/model", json=blank_key)
        assert built[-1]["api_key"] == "sk-secret-123"  # kept for the same address

        api.cookies.set(SESSION_COOKIE, session("Nurse A"))  # the next patient: the new model
        proposal = events(api, intake(api), until="proposal")[-1][1]["proposal"]
        assert proposal["colour"] == "orange"  # chest pain, named by the new model
        rows = api.get("/api/audit").json()

        api.cookies.set(SESSION_COOKIE, session("Admin E", "admin"))
        back = api.post("/api/admin/model", json={"location": "this_server"}).json()
        assert back["location"] == "this_server" and built[-1] is None
    changes = [r for r in rows if r["tool"] == "model_changed"]
    assert changes[0]["approver"] == "Admin E" and changes[0]["inputs"]["has_key"] is True
    assert changes[0]["outputs"] == {"complaint_text_leaves_the_hospital": True}
    assert "sk-secret-123" not in json.dumps(rows)


def test_test_connection_times_one_short_call_and_says_why_it_failed(monkeypatch):
    answers = iter([AIMessage("ready", response_metadata={"model_name": "qwen-test"})])
    monkeypatch.setattr("mizani.api.chat_model", lambda choice=None: FakeModel(messages=answers))
    hospital = {"location": "hospital", "base_url": "http://10.0.0.5:11434/v1", "model": "q"}
    with client(who="Admin E", role="admin") as api:
        ok = api.post("/api/admin/model/test", json=hospital).json()
        failed = api.post("/api/admin/model/test", json=hospital).json()  # no answer left
        nothing_saved = api.get("/api/admin/model").json()["location"]
    assert ok["ok"] and (ok["model"], ok["reply"]) == ("qwen-test", "ready")
    assert ok["seconds"] >= 0 and failed["ok"] is False and failed["error"]
    assert nothing_saved == "this_server"


def test_health_answers_ok_when_the_database_can_be_read():
    with client() as api:
        r = api.get("/api/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok", "version": __version__})


def test_health_is_503_when_the_database_cannot_be_read(tmp_path, monkeypatch):
    with client() as api:
        monkeypatch.setenv("MIZANI_DB", str(tmp_path))  # a folder, not a database file
        r = api.get("/api/health")
    assert r.status_code == 503
