"""Approval tokens: proof that a signed-in person decided on one triage.

The API calls `issue_approval` for the account behind the request when a nurse presses Approve or
Override. `commit_triage` accepts only a token that is signed, unexpired, for that triage, not
used before, and from an account whose role may do that: an active account that still has the
role it had when the token was signed. The model never sees the secret, so it cannot make a
token. Releasing a handover and recording that a patient was called in work the same way, each
with a token for that purpose only.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid

from mizani import auth
from mizani.db import db_path
from mizani.schemas import Account, NurseDecision

# A token is used about a second after the click; two minutes covers a slow machine.
TOKEN_TTL_SECONDS = 2 * 60
# Who may do what. "chief" is a doctor with the chief flag.
ALLOWED = {
    "commit": ("nurse",),
    "handover": ("nurse",),
    "called_in": ("nurse", "doctor"),
    "chart": ("chief",),
}
WHAT = {
    "commit": "save a triage",
    "handover": "release a handover",
    "called_in": "record that a patient was called in",
    "chart": "activate a triage chart",
}
APPROVE = NurseDecision(action="approve")


class ApprovalError(Exception):
    """The token is missing, forged, expired, for another triage, already used, or from an
    account that may not do this."""


def ensure_secret() -> None:
    """Use `MIZANI_APPROVAL_SECRET`, or else this install's own secret: made once, on the first
    start, in the data folder the API and the MCP server share. Every install then has its own,
    and nobody has to remember to set one."""
    if os.environ.get("MIZANI_APPROVAL_SECRET"):
        return
    path = db_path().parent / "approval.secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        spare = path.with_name(f"approval.secret.{uuid.uuid4().hex}")
        fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(secrets.token_hex(32))
        try:
            os.link(spare, path)  # never replaces a secret the other server wrote meanwhile
        except FileExistsError:
            pass
        except OSError:  # a file system without hard links
            if not path.exists():
                os.replace(spare, path)
        finally:
            spare.unlink(missing_ok=True)
    os.environ["MIZANI_APPROVAL_SECRET"] = path.read_text(encoding="ascii").strip()


def _secret() -> bytes:
    secret = os.environ.get("MIZANI_APPROVAL_SECRET")
    if not secret:
        raise RuntimeError("MIZANI_APPROVAL_SECRET is not set; see .env.example")
    return secret.encode("utf-8")


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()


def issue_approval(
    triage_id: str,
    account: Account,
    decision: NurseDecision = APPROVE,
    purpose: str = "commit",
    draft_id: int | None = None,
) -> str:
    """Token for one decision by the signed-in `account` on one triage. Valid for 2 minutes,
    usable once. `purpose` is what it may unlock: "commit" (the triage), "handover" (releasing
    the draft), "called_in" (taking the patient off the waiting list) or "chart" (activating a
    triage chart; `triage_id` is then the chart's id). A handover token names the exact draft
    the nurse read (`draft_id`)."""
    if purpose == "handover" and draft_id is None:
        raise ValueError("a handover approval must name the draft the nurse read")
    payload = {
        "triage_id": triage_id,
        "purpose": purpose,
        "draft_id": draft_id,
        "account_id": account.id,
        "approver": account.full_name,
        "role": account.role,
        "chief": account.chief,
        "action": decision.action,
        "colour": decision.colour,
        "reason": decision.reason,
        "confirmed": decision.confirmed,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
        "nonce": uuid.uuid4().hex,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return f"{body}.{_sign(body)}"


def verify_approval(
    token: str, triage_id: str, con: sqlite3.Connection, purpose: str = "commit"
) -> dict:
    """The signed payload, or ApprovalError saying why the token is refused."""
    if not token or "." not in token:
        raise ApprovalError("no approval token: a signed-in nurse must approve or override first")
    body, signature = token.rsplit(".", 1)
    try:
        valid = hmac.compare_digest(signature, _sign(body))
    except (UnicodeEncodeError, TypeError):  # non-ASCII text can't be one of our tokens
        valid = False
    if not valid:
        raise ApprovalError("approval token signature is invalid")
    payload = json.loads(base64.urlsafe_b64decode(body))
    if payload["triage_id"] != triage_id:
        raise ApprovalError("approval token is for a different triage")
    if payload.get("purpose", "commit") != purpose:
        raise ApprovalError(f"approval token is for {payload.get('purpose')}, not {purpose}")
    if payload["exp"] < time.time():
        raise ApprovalError("approval token has expired; ask the nurse to decide again")
    # The account as it is now: deactivated, or its role changed, since the token was signed
    account = auth.get(con, payload.get("account_id") or -1)
    if account is None or not account.active:
        raise ApprovalError("approval token is not from an active account")
    if account.role != payload.get("role") or account.full_name != payload.get("approver"):
        raise ApprovalError("the account changed since this approval; decide again")
    allowed = ALLOWED[purpose]
    if not any(auth.has_role(account, role) for role in allowed):
        who = " or ".join("the chief doctor" if r == "chief" else f"a {r}" for r in allowed)
        raise ApprovalError(f"only {who} may {WHAT[purpose]}; this is from a {account.role}")
    return payload
