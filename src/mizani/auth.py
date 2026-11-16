"""Accounts, passwords and sign-in sessions.

There is no signup. An admin creates each account with a one-time password, and its owner must
choose their own at the first sign-in. Accounts are deactivated, never deleted, so audit rows
always name a real person. Passwords are hashed with scrypt from the standard library (a salt per
account) and compared in constant time. A session is a random value in an HttpOnly cookie; the
database keeps only its SHA-256, so a copy of the database cannot be used to sign in. Sign-ins,
refused sign-ins and every account change go into the hash-chained audit log; a password or a
session value never does.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from mizani import audit
from mizani.schemas import Account

ROLES = ("nurse", "doctor", "admin")
IDLE = timedelta(minutes=15)  # a shared tablet signs out after 15 minutes without use
LONGEST = timedelta(hours=12)  # and after one shift, used or not
MAX_MISSES, LOCKED_FOR = 5, timedelta(minutes=15)
MIN_PASSWORD, MAX_PASSWORD = 10, 200
# One of OWASP's equivalent scrypt settings: 16 MB of memory and about 0.2 s per check
SCRYPT = {"n": 2**14, "r": 8, "p": 5}
SCRYPT_MAXMEM = 64 * 2**20
OTP_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0, o, 1, l or i to misread on paper
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
FIRST_ADMIN = ("admin", "Administrator")
CONSOLE = "mizani-admin (server console)"  # the actor for changes made on the server itself
FIRST_START = "first start"  # password_by of the one-time password the first start prints


class AuthError(Exception):
    """A refused sign-in or account change. The message is written for the page to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, **SCRYPT, maxmem=SCRYPT_MAXMEM, dklen=32
    )
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    """Constant-time check. An unusable hash ("!", an account no one may sign in to) is False."""
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
            dklen=len(digest) // 2,
        )
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


_spare: list[str] = []


def _dummy_hash() -> str:
    """Checked against when the user name is unknown, so a wrong name takes as long as a wrong
    password and the time does not tell which names exist."""
    if not _spare:
        _spare.append(hash_password(secrets.token_hex(16)))
    return _spare[0]


def one_time_password() -> str:
    """12 letters and digits in groups of 4, easy to read out: about 59 bits."""
    return "-".join("".join(secrets.choice(OTP_LETTERS) for _ in range(4)) for _ in range(3))


def _session_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"],
        role=row["role"],
        chief=row["chief_at"] is not None,
        chief_by=row["chief_by"],
        chief_at=_when(row["chief_at"]),
        active=bool(row["active"]),
        must_change=bool(row["must_change"]),
        locked_until=_when(row["locked_until"]),
        created_at=_when(row["created_at"]),
        created_by=row["created_by"],
        last_login_at=_when(row["last_login_at"]),
    )


def get(con: sqlite3.Connection, account_id: int) -> Account | None:
    row = con.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()
    return _account(row) if row else None


def find(con: sqlite3.Connection, username: str) -> Account | None:
    row = con.execute(
        "SELECT * FROM account WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    return _account(row) if row else None


def accounts(con: sqlite3.Connection) -> list[Account]:
    """Every account, active ones first, then by name."""
    rows = con.execute("SELECT * FROM account ORDER BY active DESC, full_name COLLATE NOCASE")
    return [_account(row) for row in rows]


def has_role(account: Account, role: str) -> bool:
    """`chief` is a doctor with the chief flag; the other roles are the account's own."""
    if role == "chief":
        return account.role == "doctor" and account.chief
    return account.role == role


def _check_new_password(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Choose a password of at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"A password can have at most {MAX_PASSWORD} characters.")
    if username.lower() in password.lower():
        raise AuthError("The password must not contain your user name.")


def add_account(
    con: sqlite3.Connection,
    username: str,
    full_name: str,
    role: str,
    password_hash: str = "!",
    created_by: str | None = None,
) -> Account:
    """Insert one account; the default hash "!" can never be signed in to (the evaluation's own
    nurse, and tests). People get theirs from `create_account`."""
    username, full_name = username.strip().lower(), " ".join(full_name.split())
    if not USERNAME.fullmatch(username):
        raise AuthError(
            "A user name is 2 to 32 lower-case letters, digits, dots, dashes or underscores, "
            "starting with a letter or digit."
        )
    if not 2 <= len(full_name) <= 80:
        raise AuthError("Give the person's full name (2 to 80 characters).")
    if role not in ROLES:
        raise AuthError(f"The role must be one of: {', '.join(ROLES)}.")
    taken = con.execute(
        "SELECT username, full_name FROM account WHERE username = ? OR full_name = ?",
        (username, full_name),
    ).fetchone()
    if taken is not None:
        if taken["username"].lower() == username:
            raise AuthError(f"The user name {username} is taken.", 409)
        raise AuthError(
            f"An account for {full_name} exists ({taken['username']}). Records show the full "
            "name, so add something that tells the two apart, such as a middle initial.",
            409,
        )
    one_time = password_hash != "!"  # a real password from an admin is a one-time password
    cur = con.execute(
        "INSERT INTO account (username, full_name, role, password_hash, must_change, created_at,"
        " created_by, password_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            username,
            full_name,
            role,
            password_hash,
            int(one_time),
            now().isoformat(),
            created_by,
            created_by,
        ),
    )
    return get(con, cur.lastrowid)


def create_account(
    con: sqlite3.Connection, actor: str, username: str, full_name: str, role: str
) -> tuple[Account, str]:
    """A new account and its one-time password, shown once to the admin who made it."""
    otp = one_time_password()
    account = add_account(con, username, full_name, role, hash_password(otp), created_by=actor)
    audit.event(
        con,
        "account_created",
        actor,
        {"username": account.username, "full_name": account.full_name, "role": role},
    )
    return account, otp


def open_session(con: sqlite3.Connection, account: Account) -> str:
    """A new session for this account. Returns the cookie's value; only its hash is stored."""
    value = secrets.token_urlsafe(32)
    stamp = now().isoformat()
    con.execute(
        "INSERT INTO session (id_hash, account_id, created_at, last_seen) VALUES (?, ?, ?, ?)",
        (_session_hash(value), account.id, stamp, stamp),
    )
    return value


def _count_miss(con: sqlite3.Connection, account_id: int) -> bool:
    """Count one wrong password in a single statement, so wrong passwords sent at the same moment
    all count. The fifth in a row locks the account for 15 minutes and starts the count again.
    True when this one locked it. (A lock set meanwhile by another request is kept.)"""
    (locked,) = con.execute(
        "UPDATE account SET failed = CASE WHEN failed + 1 >= ? THEN 0 ELSE failed + 1 END,"
        " locked_until = CASE WHEN failed + 1 >= ? THEN ? ELSE locked_until END"
        " WHERE id = ? RETURNING failed = 0",
        (MAX_MISSES, MAX_MISSES, (now() + LOCKED_FOR).isoformat(), account_id),
    ).fetchone()
    return bool(locked)


def sign_in(con: sqlite3.Connection, username: str, password: str) -> tuple[str, Account]:
    """Check the password; returns the new session's cookie value and the account.

    Five wrong passwords in a row lock the account for 15 minutes. A wrong user name and a wrong
    password get the same answer, in the same time; a locked account says it is locked, so the
    person knows to wait or ask an admin (user names are not secret: screens and the log show
    them)."""
    row = con.execute(
        "SELECT * FROM account WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    wrong = AuthError("Wrong user name or password.", 401)

    def refuse(name: str | None, reason: str) -> None:
        audit.event(
            con, "sign_in_refused", None, {"username": name}, {"reason": reason}, error=True
        )

    if row is None:
        password_matches(password, _dummy_hash())
        refuse(None, "unknown user name")  # the typed name may be a password: not logged
        raise wrong
    account = _account(row)
    if account.locked_until and account.locked_until > now():
        password_matches(password, _dummy_hash())
        minutes = max(1, round((account.locked_until - now()).total_seconds() / 60))
        refuse(account.username, "locked")
        raise AuthError(
            f"Too many wrong passwords: this account is locked for {minutes} more minute"
            f"{'s' if minutes > 1 else ''}. If you have forgotten your password, ask an admin "
            "to reset it.",
            429,
        )
    if not password_matches(password, row["password_hash"]):
        locked = _count_miss(con, account.id)
        refuse(account.username, "wrong password" + (", locked for 15 minutes" if locked else ""))
        raise wrong
    if not account.active:
        refuse(account.username, "deactivated")
        raise AuthError("This account is deactivated. Ask an admin if you need it again.", 403)
    # The right password, unless the account was locked while it was being checked (guesses sent
    # at the same moment): then it is refused like any sign-in to a locked account.
    moment = now().isoformat()
    opened = con.execute(
        "UPDATE account SET failed = 0, locked_until = NULL, last_login_at = ?"
        " WHERE id = ? AND (locked_until IS NULL OR locked_until <= ?)",
        (moment, account.id, moment),
    ).rowcount
    if not opened:
        refuse(account.username, "locked")
        raise AuthError(
            "Too many wrong passwords: this account is locked for now. If you have forgotten "
            "your password, ask an admin to reset it.",
            429,
        )
    value = open_session(con, account)
    audit.event(
        con, "sign_in", account.full_name, {"username": account.username, "role": account.role}
    )
    return value, get(con, account.id)


def session_account(con: sqlite3.Connection, value: str, touch: bool = True) -> Account | None:
    """The account behind a session cookie, or None if the session ended, timed out (15 minutes
    without use, or 12 hours after sign-in) or belongs to a deactivated account. `touch` counts
    the request as use; the page's own polling does not."""
    key = _session_hash(value)
    row = con.execute(
        "SELECT s.created_at AS opened, s.last_seen, a.* FROM session s"
        " JOIN account a ON a.id = s.account_id WHERE s.id_hash = ? AND s.ended = 0",
        (key,),
    ).fetchone()
    if row is None:
        return None
    moment = now()
    if (
        moment - datetime.fromisoformat(row["last_seen"]) > IDLE
        or moment - datetime.fromisoformat(row["opened"]) > LONGEST
        or not row["active"]
    ):
        con.execute("UPDATE session SET ended = 1 WHERE id_hash = ?", (key,))
        return None
    if touch:
        con.execute("UPDATE session SET last_seen = ? WHERE id_hash = ?", (moment.isoformat(), key))
    return _account(row)


def sign_out(con: sqlite3.Connection, value: str, account: Account) -> None:
    con.execute("UPDATE session SET ended = 1 WHERE id_hash = ?", (_session_hash(value),))
    audit.event(con, "sign_out", account.full_name, {"username": account.username})


def _end_sessions(con: sqlite3.Connection, account_id: int, keep: str | None = None) -> None:
    con.execute(
        "UPDATE session SET ended = 1 WHERE account_id = ? AND id_hash != ?",
        (account_id, _session_hash(keep) if keep else ""),
    )


def change_password(
    con: sqlite3.Connection, account: Account, current: str, new: str, session: str
) -> Account:
    """The owner changes their password: needs the current one (or the one-time one). Other
    sessions of the account end; this one stays. A wrong current password counts like a wrong
    sign-in, so a tablet left signed in cannot be used to guess it: the fifth locks the account
    for 15 minutes and signs it out everywhere."""
    row = con.execute(
        "SELECT password_hash, locked_until FROM account WHERE id = ?", (account.id,)
    ).fetchone()
    if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now():
        raise AuthError("Too many wrong passwords: this account is locked for now.", 429)
    if not password_matches(current, row["password_hash"]):
        locked = _count_miss(con, account.id)
        if locked:
            _end_sessions(con, account.id)
        reason = "wrong current password" + (", locked for 15 minutes" if locked else "")
        audit.event(con, "password_change_refused", account.full_name,
                    {"username": account.username}, {"reason": reason}, error=True)  # fmt: skip
        if locked:
            raise AuthError(
                "Too many wrong passwords: the account is locked for 15 minutes and signed out.",
                429,
            )
        raise AuthError("Your current password is not right.", 403)
    if new == current:
        raise AuthError("Choose a password different from the current one.")
    _check_new_password(new, account.username)
    con.execute(
        "UPDATE account SET password_hash = ?, must_change = 0, failed = 0, password_by = NULL"
        " WHERE id = ?",
        (hash_password(new), account.id),
    )
    _end_sessions(con, account.id, keep=session)
    audit.event(con, "password_changed", account.full_name, {"username": account.username})
    return get(con, account.id)


def _target(con: sqlite3.Connection, account_id: int) -> Account:
    account = get(con, account_id)
    if account is None:
        raise AuthError(f"No account {account_id}.", 404)
    return account


def _last_admin(con: sqlite3.Connection, account: Account) -> bool:
    if account.role != "admin" or not account.active:
        return False
    others = con.execute(
        "SELECT COUNT(*) FROM account WHERE role = 'admin' AND active = 1 AND id != ?",
        (account.id,),
    ).fetchone()[0]
    return others == 0


def reset_password(con: sqlite3.Connection, actor: str, account_id: int) -> str:
    """A new one-time password for a person who forgot theirs or is locked out. Their sessions
    end and the lock is lifted."""
    account = _target(con, account_id)
    otp = one_time_password()
    con.execute(
        "UPDATE account SET password_hash = ?, must_change = 1, failed = 0, locked_until = NULL,"
        " password_by = ? WHERE id = ?",
        (hash_password(otp), actor, account.id),
    )
    _end_sessions(con, account.id)
    audit.event(con, "password_reset", actor, {"username": account.username})
    return otp


def set_role(con: sqlite3.Connection, actor: Account, account_id: int, role: str) -> Account:
    account = _target(con, account_id)
    if role not in ROLES:
        raise AuthError(f"The role must be one of: {', '.join(ROLES)}.")
    if role == account.role:
        return account
    if account.id == actor.id:
        raise AuthError("You cannot change your own role. Ask another admin.", 409)
    if _last_admin(con, account):
        raise AuthError("This is the last active admin. Make someone else an admin first.", 409)
    con.execute(
        "UPDATE account SET role = ?, chief_by = CASE WHEN ? = 'doctor' THEN chief_by END,"
        " chief_at = CASE WHEN ? = 'doctor' THEN chief_at END WHERE id = ?",
        (role, role, role, account.id),
    )
    audit.event(con, "role_changed", actor.full_name,
        {"username": account.username, "from": account.role, "to": role})  # fmt: skip
    return get(con, account.id)


def set_chief(con: sqlite3.Connection, actor: Account, account_id: int, chief: bool) -> Account:
    """Give or remove the chief flag. Only a doctor can hold it."""
    account = _target(con, account_id)
    if chief and account.role != "doctor":
        raise AuthError("Only a doctor can be the chief doctor.", 409)
    if chief == account.chief:
        return account
    stamp = now().isoformat() if chief else None
    con.execute(
        "UPDATE account SET chief_by = ?, chief_at = ? WHERE id = ?",
        (actor.full_name if chief else None, stamp, account.id),
    )
    audit.event(con, "chief_flag_given" if chief else "chief_flag_removed", actor.full_name,
        {"username": account.username})  # fmt: skip
    return get(con, account.id)


def set_active(con: sqlite3.Connection, actor: Account, account_id: int, active: bool) -> Account:
    """Deactivate (sessions end, records keep the name) or reactivate an account."""
    account = _target(con, account_id)
    if active == account.active:
        return account
    if not active and account.id == actor.id:
        raise AuthError("You cannot deactivate your own account.", 409)
    if not active and _last_admin(con, account):
        raise AuthError("This is the last active admin. Make someone else an admin first.", 409)
    con.execute("UPDATE account SET active = ? WHERE id = ?", (int(active), account.id))
    if not active:
        _end_sessions(con, account.id)
    audit.event(con, "account_reactivated" if active else "account_deactivated", actor.full_name,
        {"username": account.username})  # fmt: skip
    return get(con, account.id)


def sign_out_everywhere(con: sqlite3.Connection, actor: Account, account_id: int) -> None:
    account = _target(con, account_id)
    _end_sessions(con, account.id)
    audit.event(con, "signed_out_everywhere", actor.full_name, {"username": account.username})


def first_start(con: sqlite3.Connection) -> str | None:
    """On a fresh install, make sure the account `admin` exists with a one-time password, and
    return it for the server log. Each start until then makes a new one (the old stops working),
    so a lost log line costs a restart. It does nothing once any admin has chosen a password, once
    another admin account was set up (with `mizani-admin` or in the page), or when `admin` itself
    was changed by hand (reset, deactivated, or given another role)."""
    settled = con.execute(
        "SELECT 1 FROM account WHERE role = 'admin' AND active = 1 AND must_change = 0"
    ).fetchone()
    username, full_name = FIRST_ADMIN
    others = con.execute(
        "SELECT 1 FROM account WHERE role = 'admin' AND active = 1 AND username != ?", (username,)
    ).fetchone()
    if settled or others:
        return None
    row = con.execute(
        "SELECT active, role, password_by FROM account WHERE username = ?", (username,)
    ).fetchone()
    # (An empty password_by is an `admin` the first start made before the column existed.)
    if row is not None and (
        not row["active"] or row["role"] != "admin" or row["password_by"] not in (FIRST_START, None)
    ):
        return None  # changed by hand: an admin or the console decides about it now
    otp = one_time_password()
    stored = hash_password(otp)  # scrypt is slow on purpose: once is enough
    if row is None:
        add_account(con, username, full_name, "admin", stored, created_by=CONSOLE)
    con.execute(
        "UPDATE account SET password_hash = ?, must_change = 1, failed = 0, locked_until = NULL,"
        " password_by = ? WHERE username = ?",
        (stored, FIRST_START, username),
    )
    audit.event(con, "first_admin_password", CONSOLE, {"username": username})
    return otp
