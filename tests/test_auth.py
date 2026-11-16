"""Accounts, sign-in, sessions and the role check on every endpoint."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeModel, person

from mizani import admin, approval, audit, auth
from mizani.api import SESSION_COOKIE, create_app, first_sign_in
from mizani.db import db
from mizani.mcp_server import mcp

CHEAP = {"n": 2**4, "r": 1, "p": 1}  # tests only: the real cost is checked once, below


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "mizani.db"))
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "test-secret")
    monkeypatch.setattr(auth, "SCRYPT", CHEAP)
    auth._spare.clear()


def new_account(username="amina", full_name="Amina Otieno", role="nurse", password=None):
    """An account as an admin makes it; with `password`, its owner has chosen it already."""
    account, otp = auth.create_account(db(), "Admin", username, full_name, role)
    if password:
        session, _ = auth.sign_in(db(), username, otp)
        account = auth.change_password(db(), account, otp, password, session)
    return account, otp


def events(tool):
    return [e for e in audit.entries(db()) if e["tool"] == tool]


def later(minutes):
    return lambda: datetime.now(UTC) + timedelta(minutes=minutes)


def test_the_real_scrypt_cost_hashes_and_checks(monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT", {"n": 2**14, "r": 8, "p": 5})
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$16384$8$5$")
    assert auth.password_matches("correct horse battery", stored)
    assert not auth.password_matches("correct horse batterY", stored)
    assert stored != auth.hash_password("correct horse battery")  # a salt per hash


def test_an_unusable_hash_never_matches():
    assert not auth.password_matches("", "!") and not auth.password_matches("x", "scrypt$1")


def test_a_one_time_password_must_be_replaced_before_anything_else():
    account, otp = new_account()
    assert account.must_change and len(otp) == 14 and otp.count("-") == 2
    session, signed = auth.sign_in(db(), "Amina", otp)  # user names ignore case
    assert signed.must_change
    with pytest.raises(auth.AuthError, match="at least 10"):
        auth.change_password(db(), signed, otp, "short", session)
    with pytest.raises(auth.AuthError, match="user name"):
        auth.change_password(db(), signed, otp, "amina-is-my-name", session)
    with pytest.raises(auth.AuthError, match="current password"):
        auth.change_password(db(), signed, "wrong", "a fine long password", session)
    changed = auth.change_password(db(), signed, otp, "a fine long password", session)
    assert not changed.must_change
    with pytest.raises(auth.AuthError, match="Wrong user name or password"):
        auth.sign_in(db(), "amina", otp)  # the one-time password is gone
    assert auth.sign_in(db(), "amina", "a fine long password")[1].id == account.id


def test_five_wrong_passwords_lock_the_account_for_15_minutes(monkeypatch):
    new_account(password="a fine long password")
    for _ in range(auth.MAX_MISSES):
        with pytest.raises(auth.AuthError, match="Wrong user name or password"):
            auth.sign_in(db(), "amina", "guess")
    with pytest.raises(auth.AuthError, match="locked for 15 more minutes") as locked:
        auth.sign_in(db(), "amina", "a fine long password")  # even the right one
    assert locked.value.status == 429
    monkeypatch.setattr(auth, "now", later(16))
    assert auth.sign_in(db(), "amina", "a fine long password")[1].username == "amina"
    reasons = [e["outputs"]["reason"] for e in events("sign_in_refused")]
    assert reasons[-2:] == ["wrong password, locked for 15 minutes", "locked"]


def test_guesses_sent_at_the_same_moment_all_count_and_their_lock_holds(monkeypatch):
    account, _ = new_account(password="a fine long password")
    real = auth.password_matches

    def four_guesses_meanwhile(password, stored):
        """While this password is being checked, other requests guess wrong four times."""
        for _ in range(auth.MAX_MISSES - 1):
            auth._count_miss(db(), account.id)
        return real(password, stored)

    monkeypatch.setattr(auth, "password_matches", four_guesses_meanwhile)
    with pytest.raises(auth.AuthError, match="Wrong user name or password"):
        auth.sign_in(db(), "amina", "guess")  # the fifth wrong one in all: none was lost
    monkeypatch.setattr(auth, "password_matches", real)
    with pytest.raises(auth.AuthError, match="locked"):
        auth.sign_in(db(), "amina", "a fine long password")

    monkeypatch.setattr(auth, "now", later(16))
    auth.sign_in(db(), "amina", "a fine long password")  # the lock is over; the count starts again

    def locked_meanwhile(password, stored):
        """The right password, while five wrong ones sent at the same moment lock the account."""
        for _ in range(auth.MAX_MISSES):
            auth._count_miss(db(), account.id)
        return real(password, stored)

    monkeypatch.setattr(auth, "password_matches", locked_meanwhile)
    with pytest.raises(auth.AuthError, match="locked") as refused:
        auth.sign_in(db(), "amina", "a fine long password")
    assert refused.value.status == 429


def test_an_unknown_name_gets_the_same_answer_and_is_not_logged():
    with pytest.raises(auth.AuthError, match="Wrong user name or password") as unknown:
        auth.sign_in(db(), "my secret password", "x")
    assert unknown.value.status == 401
    (row,) = events("sign_in_refused")
    assert row["inputs"] == {"username": None} and "my secret" not in json.dumps(row)


def test_a_deactivated_account_cannot_sign_in_and_its_sessions_end():
    boss, _ = new_account("boss", "Head Admin", "admin", password="a fine long password")
    account, _ = new_account(password="another long password")
    session, _ = auth.sign_in(db(), "amina", "another long password")
    auth.set_active(db(), boss, account.id, False)
    assert auth.session_account(db(), session) is None
    with pytest.raises(auth.AuthError, match="deactivated") as off:
        auth.sign_in(db(), "amina", "another long password")
    assert off.value.status == 403
    auth.set_active(db(), boss, account.id, True)
    assert auth.sign_in(db(), "amina", "another long password")[1].active


def test_a_session_ends_after_15_idle_minutes_or_12_hours(monkeypatch):
    account, _ = new_account(password="a fine long password")
    session = auth.open_session(db(), account)
    monkeypatch.setattr(auth, "now", later(14))
    assert auth.session_account(db(), session, touch=False)  # polling: no use
    monkeypatch.setattr(auth, "now", later(16))
    assert auth.session_account(db(), session) is None

    session = auth.open_session(db(), account)
    for minutes in range(10, 12 * 60, 10):  # used every 10 minutes all shift
        monkeypatch.setattr(auth, "now", later(16 + minutes))
        assert auth.session_account(db(), session)
    monkeypatch.setattr(auth, "now", later(16 + 12 * 60 + 1))
    assert auth.session_account(db(), session) is None


def test_the_database_keeps_only_a_hash_of_the_session():
    account, _ = new_account(password="a fine long password")
    session = auth.open_session(db(), account)
    stored = [r["id_hash"] for r in db().execute("SELECT id_hash FROM session")]
    assert session not in stored and len(stored[0]) == 64


def test_admin_rules_protect_the_last_admin_and_the_chief_flag_is_for_doctors():
    boss, _ = new_account("boss", "Head Admin", "admin", password="a fine long password")
    doctor, _ = new_account("wanjiru", "Dr Wanjiru", "doctor")
    nurse, _ = new_account("amina", "Amina Otieno", "nurse")
    with pytest.raises(auth.AuthError, match="own role"):
        auth.set_role(db(), boss, boss.id, "nurse")
    with pytest.raises(auth.AuthError, match="own account"):
        auth.set_active(db(), boss, boss.id, False)
    with pytest.raises(auth.AuthError, match="Only a doctor"):
        auth.set_chief(db(), boss, nurse.id, True)
    chief = auth.set_chief(db(), boss, doctor.id, True)
    assert chief.chief and chief.chief_by == "Head Admin" and chief.chief_at
    assert not auth.set_role(db(), boss, doctor.id, "nurse").chief  # the flag goes with it
    other = auth.set_role(db(), boss, nurse.id, "admin")
    auth.set_active(db(), boss, other.id, False)
    # `other` acting at the same moment, with the account read before it was deactivated
    with pytest.raises(auth.AuthError, match="last active admin"):
        auth.set_role(db(), other, boss.id, "nurse")
    with pytest.raises(auth.AuthError, match="last active admin"):
        auth.set_active(db(), other, boss.id, False)
    tools = [e["tool"] for e in audit.entries(db())]
    assert {"account_created", "chief_flag_given", "role_changed"} <= set(tools)


def test_names_are_unique_so_records_name_one_person():
    new_account("amina", "Amina Otieno")
    with pytest.raises(auth.AuthError, match="taken"):
        new_account("Amina", "Someone Else")
    with pytest.raises(auth.AuthError, match="middle initial"):
        new_account("amina2", "amina otieno")
    with pytest.raises(auth.AuthError, match="lower-case"):
        new_account("a b", "Ab Cd")


def test_a_reset_lifts_the_lock_ends_sessions_and_gives_a_new_one_time_password():
    boss, _ = new_account("boss", "Head Admin", "admin", password="a fine long password")
    account, _ = new_account(password="another long password")
    session = auth.open_session(db(), account)
    for _ in range(auth.MAX_MISSES):
        with pytest.raises(auth.AuthError):
            auth.sign_in(db(), "amina", "guess")
    otp = auth.reset_password(db(), boss.full_name, account.id)
    assert auth.session_account(db(), session) is None
    assert auth.sign_in(db(), "amina", otp)[1].must_change


def test_no_password_or_session_ever_reaches_the_audit_log():
    account, otp = new_account()
    session, _ = auth.sign_in(db(), "amina", otp)
    auth.change_password(db(), auth.get(db(), account.id), otp, "a fine long password", session)
    text = json.dumps(audit.entries(db()))
    assert otp not in text and "a fine long password" not in text and session not in text
    assert audit.verify_chain(db()) is None


def test_the_first_start_prints_a_new_admin_password_until_one_is_chosen(monkeypatch):
    hashes = []
    real = auth.hash_password
    monkeypatch.setattr(auth, "hash_password", lambda p: hashes.append(p) or real(p))
    first = auth.first_start(db())
    assert hashes == [first]  # scrypt once per start, not once per statement
    second = auth.first_start(db())  # a restart before the first sign-in
    assert first and second and first != second
    with pytest.raises(auth.AuthError):
        auth.sign_in(db(), "admin", first)
    session, account = auth.sign_in(db(), "admin", second)
    assert account.role == "admin" and account.must_change
    auth.change_password(db(), account, second, "a password of my own", session)
    assert auth.first_start(db()) is None
    box = first_sign_in("abcd-efgh-jkmn")
    assert "admin" in box and "abcd-efgh-jkmn" in box and box.startswith("+")


def test_guessing_the_current_password_on_a_signed_in_tablet_locks_the_account():
    account, _ = new_account(password="a fine long password")
    session = auth.open_session(db(), account)
    for _ in range(auth.MAX_MISSES - 1):
        with pytest.raises(auth.AuthError, match="current password is not right"):
            auth.change_password(db(), account, "guess", "a new long password", session)
    with pytest.raises(auth.AuthError, match="locked for 15 minutes and signed out") as locked:
        auth.change_password(db(), account, "guess", "a new long password", session)
    assert locked.value.status == 429 and auth.session_account(db(), session) is None
    with pytest.raises(auth.AuthError, match="locked"):
        auth.sign_in(db(), "amina", "a fine long password")
    assert len(events("password_change_refused")) == auth.MAX_MISSES


def test_the_first_start_leaves_alone_what_was_set_up_by_hand(capsys):
    first = auth.first_start(db())
    assert admin.main(["reset-password", "admin"]) == 0  # IT gave it a password by hand
    by_hand = capsys.readouterr().out.split("One-time password: ")[1].split()[0]
    assert auth.first_start(db()) is None  # a restart keeps it
    assert auth.sign_in(db(), "admin", by_hand)[1].username == "admin"
    with pytest.raises(auth.AuthError):
        auth.sign_in(db(), "admin", first)


def test_the_first_start_never_brings_back_a_deactivated_or_bypassed_admin():
    auth.first_start(db())
    db().execute("UPDATE account SET active = 0 WHERE username = 'admin'")
    assert auth.first_start(db()) is None
    assert auth.find(db(), "admin").active is False
    db().execute("UPDATE account SET active = 1 WHERE username = 'admin'")
    assert auth.first_start(db())  # still only its own one-time password: renewed
    admin.main(["create-admin", "it.lead", "IT Lead"])  # another admin set up by hand
    assert auth.first_start(db()) is None


def test_an_admin_the_old_first_start_made_still_gets_its_password():
    auth.first_start(db())
    db().execute("UPDATE account SET password_by = NULL WHERE username = 'admin'")  # before 1.0.0
    assert auth.first_start(db())


def test_mizani_admin_creates_an_admin_and_resets_a_password(capsys):
    assert admin.main(["create-admin", "it.lead", "IT Lead"]) == 0
    otp = capsys.readouterr().out.split("One-time password: ")[1].split()[0]
    assert auth.sign_in(db(), "it.lead", otp)[1].role == "admin"
    assert admin.main(["reset-password", "it.lead"]) == 0
    assert "One-time password" in capsys.readouterr().out
    assert admin.main(["reset-password", "nobody"]) == 1
    assert events("account_created")[0]["approver"] == auth.CONSOLE


def test_each_install_makes_its_own_approval_secret_once(monkeypatch, tmp_path):
    monkeypatch.delenv("MIZANI_APPROVAL_SECRET")
    approval.ensure_secret()
    made = (tmp_path / "approval.secret").read_text()
    monkeypatch.delenv("MIZANI_APPROVAL_SECRET")
    approval.ensure_secret()  # the other server, or the next start
    assert made == (tmp_path / "approval.secret").read_text() and len(made) == 64
    monkeypatch.setenv("MIZANI_APPROVAL_SECRET", "from-env")
    approval.ensure_secret()
    assert approval._secret() == b"from-env"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("approval")] == [
        "approval.secret"
    ]


# The API: sign-in, the header guard, and the role check on every endpoint


def app():
    return create_app(FakeModel(messages=iter([])), mcp, InMemorySaver())


def page(api_app):
    return TestClient(api_app, base_url="http://127.0.0.1", headers={"X-Mizani": "1"})


def test_sign_in_sets_a_safe_cookie_and_a_one_time_password_blocks_the_rest():
    _, otp = new_account()
    with page(app()) as api:
        wrong = api.post("/api/auth/login", json={"username": "amina", "password": "x"})
        assert wrong.status_code == 401 and wrong.json()["detail"].startswith("Wrong")
        signed = api.post("/api/auth/login", json={"username": "amina", "password": otp})
        cookie = signed.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
        assert api.get("/api/auth/me").json()["must_change"] is True
        blocked = api.get("/api/queue")
        assert blocked.status_code == 403 and "own password" in blocked.json()["detail"]
        new = {"current": otp, "new": "a fine long password"}
        assert api.post("/api/auth/password", json=new).json()["must_change"] is False
        assert api.get("/api/queue").status_code == 200
        assert api.post("/api/auth/logout").status_code == 200
        assert api.get("/api/auth/me").status_code == 401
    assert [e["tool"] for e in audit.entries(db())][-3:] == [
        "sign_in", "password_changed", "sign_out"
    ]  # fmt: skip


def test_over_https_the_session_cookie_is_secure():
    new_account(password="a fine long password")
    secure = TestClient(app(), base_url="https://127.0.0.1", headers={"X-Mizani": "1"})
    with secure as api:
        signed = api.post(
            "/api/auth/login", json={"username": "amina", "password": "a fine long password"}
        )
    cookie = signed.headers["set-cookie"].lower()
    assert signed.status_code == 200 and "; secure" in cookie and "httponly" in cookie


def test_a_change_without_the_page_header_is_refused_and_answers_carry_safe_headers():
    with TestClient(app(), base_url="http://127.0.0.1") as api:
        forged = api.post("/api/auth/login", json={"username": "a", "password": "b"})
        assert forged.status_code == 403 and "Mizani page" in forged.json()["detail"]
        health = api.get("/api/health")
    assert health.status_code == 200  # open without a sign-in, for Docker's health check
    headers = health.headers
    assert headers["cache-control"] == "no-store" and headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"


def test_the_pages_own_polling_does_not_keep_an_idle_session_alive(monkeypatch):
    account, _ = new_account(password="a fine long password")
    session = auth.open_session(db(), account)
    with page(app()) as api:
        api.cookies.set(SESSION_COOKIE, session)
        monkeypatch.setattr(auth, "now", later(10))
        background = {"X-Mizani-Background": "1"}
        assert api.get("/api/queue", headers=background).status_code == 200
        monkeypatch.setattr(auth, "now", later(16))  # 16 minutes after the last real use
        assert api.get("/api/queue", headers=background).status_code == 401


ALL = {"nurse", "doctor", "admin", "chief"}
ENDPOINTS = [
    ("POST", "/api/intake", {"nurse"}),
    ("POST", "/api/queue/t1/retriage", {"nurse"}),
    ("GET", "/api/triage/t1/stream", {"nurse"}),
    ("POST", "/api/triage/t1/decision", {"nurse"}),
    ("POST", "/api/triage/t1/vitals", {"nurse"}),
    ("POST", "/api/triage/t1/assessment", {"nurse"}),
    ("GET", "/api/shadow", {"nurse"}),
    ("POST", "/api/queue/t1/handover", {"nurse"}),
    ("POST", "/api/queue/t1/handover/1/release", {"nurse"}),
    ("POST", "/api/queue/t1/called-in", {"nurse", "doctor", "chief"}),
    ("GET", "/api/queue", {"nurse", "doctor", "chief"}),
    ("GET", "/api/queue/t1", {"nurse", "doctor", "chief"}),
    ("GET", "/api/handovers", {"doctor", "chief"}),
    ("GET", "/api/admin/users", {"admin"}),
    ("POST", "/api/admin/users", {"admin"}),
    ("POST", "/api/admin/users/999/reset-password", {"admin"}),
    ("POST", "/api/admin/users/999/role", {"admin"}),
    ("POST", "/api/admin/users/999/chief", {"admin"}),
    ("POST", "/api/admin/users/999/active", {"admin"}),
    ("POST", "/api/admin/users/999/sign-out", {"admin"}),
    ("GET", "/api/charts", {"chief"}),
    ("POST", "/api/charts", {"chief"}),
    ("GET", "/api/charts/999", {"chief"}),
    ("POST", "/api/charts/999/evaluate", {"chief"}),
    ("POST", "/api/charts/999/activate", {"chief"}),
    ("POST", "/api/charts/rollback", {"chief"}),
    ("GET", "/api/admin/model", {"admin"}),
    ("POST", "/api/admin/model", {"admin"}),
    ("POST", "/api/admin/model/test", {"admin"}),
    ("GET", "/api/colours", ALL),
    ("GET", "/api/audit", ALL),
    ("GET", "/api/audit/verify", ALL),
]


def test_every_endpoint_checks_the_role_on_the_server():
    people = {
        "nurse": person("Nurse A", "nurse"),
        "doctor": person("Doctor D", "doctor"),
        "admin": person("Admin E", "admin"),
        "chief": person("Chief C", "doctor", chief=True),
    }
    sessions = {role: auth.open_session(db(), who) for role, who in people.items()}
    with page(app()) as api:
        for method, path, allowed in ENDPOINTS:
            nobody = api.request(method, path)
            assert nobody.status_code == 401, (method, path)
            for role, session in sessions.items():
                api.cookies.set(SESSION_COOKIE, session)
                response = api.request(method, path)
                if role in allowed:
                    assert response.status_code not in (401, 403), (method, path, role)
                else:
                    assert response.status_code == 403, (method, path, role, response.text)
                api.cookies.clear()


def test_the_admin_manages_accounts_through_the_api():
    boss, _ = new_account("boss", "Head Admin", "admin", password="a fine long password")
    with page(app()) as api:
        api.cookies.set(SESSION_COOKIE, auth.open_session(db(), boss))
        made = api.post(
            "/api/admin/users",
            json={"username": "wanjiru", "full_name": "Dr Wanjiru", "role": "doctor"},
        )
        assert made.status_code == 201 and made.json()["one_time_password"]
        doctor = made.json()["account"]
        assert "password_hash" not in doctor and doctor["must_change"] is True
        again = api.post(
            "/api/admin/users",
            json={"username": "wanjiru", "full_name": "Dr W", "role": "doctor"},
        )
        assert again.status_code == 409 and "taken" in again.json()["detail"]
        chief = api.post(f"/api/admin/users/{doctor['id']}/chief", json={"chief": True}).json()
        assert chief["chief"] and chief["chief_by"] == "Head Admin"
        assert api.post(f"/api/admin/users/{doctor['id']}/reset-password").status_code == 200
        off = api.post(f"/api/admin/users/{doctor['id']}/active", json={"active": False})
        assert off.json()["active"] is False
        listed = {a["username"]: a for a in api.get("/api/admin/users").json()}
        self_off = api.post(f"/api/admin/users/{boss.id}/active", json={"active": False})
    assert listed["wanjiru"]["active"] is False and listed["boss"]["role"] == "admin"
    assert self_off.status_code == 409
