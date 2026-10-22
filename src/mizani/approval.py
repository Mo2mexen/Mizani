"""Approval tokens: proof that a named nurse decided on one triage.

The API calls `issue_approval` when the nurse presses Approve or Override. `commit_triage`
accepts only a token that is signed, unexpired, for that triage, and not used before. The
model never sees the secret, so it cannot make a token.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

from mizani.schemas import NurseDecision

TOKEN_TTL_SECONDS = 15 * 60


class ApprovalError(Exception):
    """The token is missing, forged, expired, for another triage, or already used."""


def _secret() -> bytes:
    secret = os.environ.get("MIZANI_APPROVAL_SECRET")
    if not secret:
        raise RuntimeError("MIZANI_APPROVAL_SECRET is not set; see .env.example")
    return secret.encode("utf-8")


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()


def issue_approval(
    triage_id: str, decision: NurseDecision, purpose: str = "commit", draft_id: int | None = None
) -> str:
    """Token for one nurse decision on one triage. Valid for 15 minutes, usable once.
    `purpose` is what it may unlock: "commit" (the triage) or "handover" (releasing the draft).
    A handover token names the exact draft the nurse read (`draft_id`)."""
    if purpose == "handover" and draft_id is None:
        raise ValueError("a handover approval must name the draft the nurse read")
    payload = {
        "triage_id": triage_id,
        "purpose": purpose,
        "draft_id": draft_id,
        "nurse_name": decision.nurse_name,
        "action": decision.action,
        "colour": decision.colour,
        "reason": decision.reason,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
        "nonce": uuid.uuid4().hex,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return f"{body}.{_sign(body)}"


def verify_approval(token: str, triage_id: str, purpose: str = "commit") -> dict:
    """The signed payload, or ApprovalError saying why the token is refused."""
    if not token or "." not in token:
        raise ApprovalError("no approval token: a named nurse must approve or override first")
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
    if not str(payload.get("nurse_name", "")).strip():
        raise ApprovalError("approval token has no approver name")
    return payload
