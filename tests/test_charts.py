"""The chief doctor's charts: upload checks, the evaluation, activation with a chief's token,
the chart on every proposal and record, and going back."""

from importlib.resources import files

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from mcp.client import Client
from test_agent_graph import NORMAL, FakeModel, person, reply
from test_api import events, intake, session, signed_in

from mizani import approval, charts, store
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
    for cases, why in [
        ("- id: x\n  complaint: c\n  vitals: {}\n  expected: {colour: purple}", "expected.colour"),
        (
            "- id: x\n  complaint: c\n  vitals: {}\n  expected: {colour: red, signs: [made_up]}",
            "not on this chart",
        ),
        ("- id: x\n  complaint: c\n  expected: {colour: red}", "has no vitals"),
        ("[]", "a YAML list"),
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


async def chief_token(ref: str, who="Chief C", role="doctor", chief=True) -> str:
    return approval.issue_approval(f"chart:{ref}", person(who, role, chief), purpose="chart")


async def test_only_the_chief_doctor_activates_a_chart_that_passed_its_evaluation():
    chart = charts.upload(db(), "Dr Chief", KENYA, CASES)
    ref = str(chart["id"])
    async with Client(mcp) as client:

        async def activate(token):
            return await client.call_tool("activate_chart", {"chart": ref, "approval_token": token})

        not_evaluated = await activate(await chief_token(ref))
        assert "not every safety check" in not_evaluated.content[0].text
        charts.set_progress(db(), chart["id"], "evaluated", None, {"safety_ok": True})
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
    charts.set_progress(db(), chart["id"], "evaluated", None, {"safety_ok": True})
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
    charts.set_progress(db(), chart["id"], "evaluated", None, {"safety_ok": True})
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
