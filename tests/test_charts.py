"""The chief doctor's charts: upload checks, the evaluation, activation with a chief's token,
the chart on every proposal and record, and going back."""

import json
from importlib.resources import files

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from mcp.client import Client
from test_agent_graph import NORMAL, FakeModel, person, reply
from test_api import committed_patient, events, intake, session, signed_in

from mizani import approval, audit, charts, store
from mizani.api import SESSION_COOKIE, create_app
from mizani.db import db
from mizani.evals import chart as chart_eval
from mizani.mcp_server import mcp
from mizani.rules.pack import parse_pack

pytestmark = pytest.mark.anyio
SATS = files("mizani.rules").joinpath("sats_adult.yaml").read_text(encoding="utf-8")
# A made-up chart for the tests: the adult SATS chart with chest pain raised to red
KENYA = SATS.replace("id: sats_adult", "id: sats_kenya").replace(
    "{id: chest_pain, name: Chest pain, colour: orange",
    "{id: chest_pain, name: Chest pain, colour: red",
)
CASES = """
- id: cp-01
  complaint: Chest pain since this morning.
  vitals: {respiratory_rate: 14, heart_rate: 80, systolic_bp: 120, temperature: 36.8,
           avpu: alert, mobility: walking, trauma: false}
  expected: {colour: red, signs: [chest_pain]}
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")
    charts._cache.clear()


def evaluated(chart_id: int) -> None:
    """Mark a chart evaluated with every safety check held, the way its evaluation does."""
    assert charts.start(db(), chart_id)
    charts.set_progress(db(), chart_id, "evaluated", None, {"safety_ok": True})


def bad(text: str) -> str:
    with pytest.raises(ValueError) as refused:
        parse_pack(text)
    return str(refused.value)


def test_an_uploaded_chart_is_checked_for_what_the_engine_needs():
    assert parse_pack(KENYA).id == "sats_kenya"
    assert "not valid YAML" in bad("id: [unclosed")
    assert "YAML mapping" in bad("- a list")
    assert "do not cite" in bad(
        KENYA.replace('source: "chart: Emergency signs"}', 'source: "my notes"}', 1)
    )
    assert "falls in 0 bands" in bad(
        KENYA.replace("{gte: 9, lte: 14, points: 0}", "{gte: 10, lte: 14, points: 0}")
    )
    assert "falls in 2 bands" in bad(
        KENYA.replace("{gte: 9, lte: 14, points: 0}", "{gte: 8, lte: 14, points: 0}")
    )
    assert "highest min_total down to 0" in bad(
        KENYA.replace("{min_total: 0, colour: green", "{min_total: 1, colour: green")
    )
    assert "gives no points" in bad(KENYA.replace("unresponsive: 3", "asleep: 3"))
    assert "repeat" in bad(KENYA.replace("{id: cardiac_arrest,", "{id: seizure_current,"))
    assert "investigations missing" in bad(KENYA.replace("  ecg: {", "  ekg: {"))
    assert "over" in bad("#" * 200_001)
    assert "lower-case" in bad(KENYA.replace("id: sats_kenya", "id: Kenya Chart"))


def test_upload_versions_a_chart_and_checks_its_test_patients():
    first = charts.upload(db(), "Dr Chief", KENYA, CASES)
    second = charts.upload(db(), "Dr Chief", KENYA, None)
    assert (first["label"], first["test_patients"], first["status"]) == (
        "sats_kenya, version 1",
        1,
        "uploaded",
    )
    assert (second["version"], second["test_patients"]) == (2, 0)
    db().execute("UPDATE chart SET test_patients = NULL")  # charts uploaded before the count
    assert charts.get(db(), first["id"])["test_patients"] == 1
    for cases, why in [
        ("- id: x\n  complaint: c\n  vitals: {}\n  expected: {colour: purple}", "expected.colour"),
        (
            "- id: x\n  complaint: c\n  vitals: {}\n  expected: {colour: red, signs: [made_up]}",
            "not on this chart",
        ),
        ("- id: x\n  complaint: c\n  expected: {colour: red}", "has no vitals"),
        ("[]", "a YAML list"),
        ("- id: [x]\n  complaint: c\n  vitals: {}\n  expected: {colour: red}", "could not be read"),
        ("[" * 50_000, "not valid YAML"),
    ]:
        with pytest.raises(charts.ChartError, match=why):
            charts.upload(db(), "Dr Chief", KENYA, cases)
    assert [r["tool"] for r in db().execute("SELECT tool FROM audit")] == ["chart_uploaded"] * 2


async def test_the_evaluation_reports_pass_rate_changes_and_safety(monkeypatch):
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    replies = iter([*(reply(ids=["chest_pain"]) for _ in range(chart_eval.RUNS)), reply()])
    monkeypatch.setattr(chart_eval, "chat_model", lambda choice=None: FakeModel(messages=replies))
    seen = []
    report = await chart_eval.evaluate(chart["id"], lambda done, total: seen.append((done, total)))
    assert report["mode"] == "pass rate" and report["pass_rate"] == 1.0
    assert report["changes"] == ["cp-01"]  # red under this chart, orange under the one in use
    (patient,) = report["patients"]
    assert (patient["colour"], patient["in_use_colour"]) == ("red", "orange")
    assert report["safety_ok"] and all(s["ok"] is not False for s in report["safety"])
    assert seen[-1] == (chart_eval.RUNS + 2, chart_eval.RUNS + 2)
    assert charts.override is None  # the process's chart and database are put back
    assert charts.active(db())[1] == "sats_adult, built in"


def edited(change) -> str:
    """The test chart with one change made to it as data."""
    data = yaml.safe_load(KENYA)
    change(data)
    return yaml.safe_dump(data)


def test_the_engine_checks_refuse_what_would_crash_scoring_or_tie_up_the_server():
    measured = edited(lambda d: d["tews"]["heart_rate"].update(bands=None, values={"80": 0}))
    assert "give its points as bands" in bad(measured)  # not a KeyError
    coded = edited(lambda d: d["tews"]["avpu"].update(values=None, bands=[{"lt": 1, "points": 0}]))
    assert "give its points as values" in bad(coded)
    assert "must be a range of at most" in bad(
        KENYA.replace("heart_rate: [20, 250]", "heart_rate: [0, 1000000000]")
    )
    gap = KENYA.replace(
        "{gte: 35, lte: 38.4, points: 0}",
        "{gte: 35, lte: 37.5, points: 0}\n      - {gte: 37.6, lte: 38.4, points: 0}",
    )
    assert "no band takes 37.5 or the values just above it" in bad(gap)
    overlap = KENYA.replace("{gte: 35, lte: 38.4, points: 0}", "{gte: 35, lte: 38.5, points: 0}")
    assert "two bands both take" in bad(overlap)
    no_bands = edited(lambda d: d["tews"]["temperature"].update(bands=[]))
    assert "give its points as bands" in bad(no_bands)  # not an IndexError
    no_row = edited(lambda d: d["tews"].pop("trauma"))
    assert "tews has no row for ['trauma']" in bad(no_row)  # not a KeyError at the first patient
    assert "not valid YAML" in bad("[" * 50_000)  # nested too deep for the parser


def test_a_check_that_did_not_foresee_a_chart_still_refuses_it(monkeypatch):
    def unforeseen(*args):
        raise TypeError("'<' not supported between instances of 'NoneType' and 'float'")

    monkeypatch.setattr("mizani.rules.pack._joins", unforeseen)
    assert "could not be checked (TypeError: '<' not supported" in bad(KENYA)
    with pytest.raises(charts.ChartError, match="could not be checked"):
        charts.upload(db(), "Dr Chief", KENYA, None)  # a refusal the page shows, not a 500


def test_activation_waits_for_the_evaluation_and_progress_never_touches_a_chart_in_use():
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    assert charts.start(db(), chart["id"]) and not charts.start(db(), chart["id"])  # one at a time
    with pytest.raises(charts.ChartError, match="its evaluation is still running"):
        charts.activate(db(), str(chart["id"]), "Dr Chief", "nonce-1")
    charts.set_progress(db(), chart["id"], "evaluated", None, {"safety_ok": True})
    charts.activate(db(), str(chart["id"]), "Dr Chief", "nonce-2")
    charts.set_progress(db(), chart["id"], "evaluating", "3 of 12 runs")  # a stray update
    assert charts.get(db(), chart["id"])["status"] == "active"
    other = charts.upload(db(), "Dr Chief", KENYA, CASES)
    evaluated(other["id"])
    assert charts.start(db(), other["id"])  # a run again forgets the old report at once
    assert charts.get(db(), other["id"])["report"] is None
    charts.set_progress(db(), other["id"], "evaluated", None, {"safety_ok": False})
    with pytest.raises(charts.ChartError, match="not every safety check"):
        charts.activate(db(), str(other["id"]), "Dr Chief", "nonce-3")


async def test_a_crashed_run_never_counts_as_agreement_and_blocks_activation(monkeypatch):
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    replies = iter([*(reply(ids=["chest_pain"]) for _ in range(chart_eval.RUNS)), reply()])
    monkeypatch.setattr(chart_eval, "chat_model", lambda choice=None: FakeModel(messages=replies))
    real, calls = chart_eval.colour_under, []

    def crash_twice(*args):
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("a band the engine could not use")
        return real(*args)

    monkeypatch.setattr(chart_eval, "colour_under", crash_twice)
    report = await chart_eval.evaluate(chart["id"])
    (patient,) = report["patients"]
    assert patient["agree"] == 0 and not report["safety_ok"]
    finished = next(s for s in report["safety"] if s["check"].startswith("Every run finished"))
    assert finished["ok"] is False and "a band the engine could not use" in finished["detail"]


async def chief_token(ref: str, who="Chief C", role="doctor", chief=True) -> str:
    return approval.issue_approval(f"chart:{ref}", person(who, role, chief), purpose="chart")


async def test_only_the_chief_doctor_activates_a_chart_that_passed_its_evaluation():
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    ref = str(chart["id"])
    async with Client(mcp) as client:

        async def activate(token):
            return await client.call_tool("activate_chart", {"chart": ref, "approval_token": token})

        not_evaluated = await activate(await chief_token(ref))
        assert "has not been evaluated yet" in not_evaluated.content[0].text
        evaluated(chart["id"])
        doctor = await activate(await chief_token(ref, "Doctor D", chief=False))
        assert "only the chief doctor may activate a triage chart" in doctor.content[0].text
        done = await activate(await chief_token(ref))
        assert not done.is_error and done.structured_content["in_use"] == "sats_kenya, version 1"
        sign = {"id": "chest_pain", "quote": "chest pain"}
        args = dict(patient_label="Test Patient 01", complaint="chest pain since morning",
                    vitals=NORMAL, discriminators=[sign])  # fmt: skip
        proposal = (await client.call_tool("propose_priority", args)).structured_content
    assert proposal["colour"] == "red" and proposal["chart"] == "sats_kenya, version 1"
    assert store.get(db(), proposal["triage_id"])["chart"] == "sats_kenya, version 1"
    assert charts.overview(db())["previous_label"] == "sats_adult, built in"


async def test_a_chart_without_a_waiting_patients_colour_is_refused():
    no_green = KENYA.replace("{min_total: 0, colour: green", "{min_total: 0, colour: yellow")
    no_green = no_green.replace(
        "  - {colour: green, label: Routine, target_minutes: 240, "
        'source: "manual p6 Table 3: green, < 4 hours"}\n',
        "",
    )
    chart = charts.upload(db(), "Dr Chief", no_green, None)
    evaluated(chart["id"])
    async with Client(mcp) as client:
        args = dict(
            patient_label="Test Patient 01", complaint="cough", vitals=NORMAL, discriminators=[]
        )
        p = (await client.call_tool("propose_priority", args)).structured_content
        token = approval.issue_approval(p["triage_id"], person("Nurse A"))
        await client.call_tool(
            "commit_triage", {"triage_id": p["triage_id"], "approval_token": token}
        )
        ref = str(chart["id"])
        refused = await client.call_tool(
            "activate_chart", {"chart": ref, "approval_token": await chief_token(ref)}
        )
    assert "patients still waiting are ['green']" in refused.content[0].text


async def test_the_chief_activates_and_goes_back_through_the_api(monkeypatch):
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    evaluated(chart["id"])
    app = create_app(
        FakeModel(messages=iter([reply(ids=["chest_pain"])] * 2)), mcp, InMemorySaver()
    )
    with signed_in(app, "Chief C", "doctor") as api:
        api.cookies.set(SESSION_COOKIE, session("Chief C", "doctor", True))
        assert api.get("/api/charts").json()["active"]["label"] == "sats_adult, built in"
        on = api.post(f"/api/charts/{chart['id']}/activate")
        assert on.status_code == 200 and on.json()["in_use"] == "sats_kenya, version 1"
        api.cookies.set(SESSION_COOKIE, session("Nurse A"))
        proposal = events(api, intake(api), until="proposal")[-1][1]["proposal"]
        assert (proposal["colour"], proposal["chart"]) == ("red", "sats_kenya, version 1")
        api.cookies.set(SESSION_COOKIE, session("Chief C", "doctor", True))
        back = api.post("/api/charts/rollback").json()
        assert back["in_use"] == "sats_adult, built in"
        again = api.post("/api/charts/rollback")
        assert again.status_code == 409
        rows = [r["tool"] for r in api.get("/api/audit").json()]
    assert rows.count("activate_chart") == 2 and "chart_uploaded" in rows


def test_a_recheck_after_a_chart_change_says_the_complaint_was_not_checked_again():
    renamed = SATS.replace("id: sats_adult", "id: sats_renamed").replace(
        "name: Chest pain,", "name: Pain in the chest,"
    )
    chart = charts.upload(db(), "Dr Chief", renamed, None)
    evaluated(chart["id"])
    app = create_app(FakeModel(messages=iter([reply(ids=["chest_pain"])])), mcp, InMemorySaver())
    with signed_in(app) as api:
        triage_id = committed_patient(api, intake(api))  # orange: chest pain, under the old chart
        api.cookies.set(SESSION_COOKIE, session("Chief C", "doctor", True))
        assert api.post(f"/api/charts/{chart['id']}/activate").status_code == 200
        api.cookies.set(SESSION_COOKIE, session("Nurse A"))
        again = api.post(f"/api/queue/{triage_id}/retriage", json={"vitals": NORMAL})
        proposal = events(api, again.json()["triage_id"], until="proposal")[-1][1]["proposal"]
    kinds = [f["kind"] for f in proposal["flags"]]
    assert "signs_not_checked" in kinds and "retriage" in kinds  # never a quiet green


def test_an_evaluation_that_cannot_start_leaves_the_chart_free_to_try_again(monkeypatch):
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)

    async def no_process(*args, **kwargs):
        raise OSError("no memory for a new process")

    monkeypatch.setattr("mizani.api.asyncio.create_subprocess_exec", no_process)
    app = create_app(FakeModel(messages=iter([])), mcp, InMemorySaver())
    with signed_in(app, "Chief C", "doctor") as api:
        api.cookies.set(SESSION_COOKIE, session("Chief C", "doctor", True))
        refused = api.post(f"/api/charts/{chart['id']}/evaluate")
    assert refused.status_code == 503 and "could not start" in refused.json()["detail"]
    after = charts.get(db(), chart["id"], full=False)
    assert after["status"] == "failed" and after["progress"].startswith("could not start")
    assert charts.start(db(), chart["id"])  # not stuck as "evaluating"


async def test_a_chart_token_is_used_only_by_an_activation_that_happens():
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    ref = str(chart["id"])
    token = await chief_token(ref)
    async with Client(mcp) as client:

        async def activate(token):
            return await client.call_tool("activate_chart", {"chart": ref, "approval_token": token})

        assert "has not been evaluated yet" in (await activate(token)).content[0].text
        evaluated(chart["id"])
        assert not (await activate(token)).is_error  # the refusal did not use it up
        # as if it were back to "evaluated", so only the token's single use can refuse it
        db().execute("UPDATE chart SET status = 'evaluated' WHERE id = ?", (chart["id"],))
        again = await activate(token)
    assert "already used" in again.content[0].text


async def test_a_chart_in_use_that_no_longer_loads_gives_way_to_the_built_in_one():
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    evaluated(chart["id"])
    charts.activate(db(), str(chart["id"]), "Dr Chief", "nonce-1")
    async with Client(mcp) as client:
        args = dict(
            patient_label="Test Patient 01", complaint="cough", vitals=NORMAL, discriminators=[]
        )
        p = (await client.call_tool("propose_priority", args)).structured_content
        token = approval.issue_approval(p["triage_id"], person("Nurse A"))
        await client.call_tool(
            "commit_triage", {"triage_id": p["triage_id"], "approval_token": token}
        )
        # An update adds a check this stored chart fails, and a waiting patient's colour is gone
        stricter = edited(lambda d: d["tews"].pop("trauma"))
        db().execute("UPDATE chart SET yaml = ? WHERE id = ?", (stricter, chart["id"]))
        db().execute("UPDATE triage SET colour = 'blue' WHERE id = ?", (p["triage_id"],))
        charts._cache.clear()  # as after a restart
        queue = json.loads((await client.read_resource("queue://current")).contents[0].text)
    assert charts.active(db())[1] == "sats_adult, built in"
    after = charts.get(db(), chart["id"])
    assert after["status"] == "failed" and "tews has no row for ['trauma']" in after["progress"]
    assert [r["tool"] for r in audit.entries(db())].count("chart_unloadable") == 1
    (entry,) = queue  # still listed, first and due now, instead of a crash
    assert (entry["colour"], entry["target_minutes"], entry["recheck_due"]) == ("blue", 0, True)


def test_the_fhir_record_names_the_chart_that_scored_the_triage():
    chart = charts.upload(db(), "Dr Chief", KENYA, None)
    name = parse_pack(KENYA).name
    assert charts.scale(db(), "sats_kenya, version 1") == ("sats_kenya", name)
    assert charts.scale(db(), "sats_adult, built in") == ("sats_adult", charts.built_in().name)
    for unknown in (None, "sats_kenya, version 9", "sats_kenya, version x", "other, built in"):
        assert charts.scale(db(), unknown) is None
    assert chart["label"] == "sats_kenya, version 1"
