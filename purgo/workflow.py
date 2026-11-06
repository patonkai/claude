"""Removal request lifecycle: create -> send -> follow up -> escalate -> close."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import letters, mailer
from .config import settings
from .models import (
    Broker, Channel, Finding, FindingStatus, Profile, RemovalRequest, RequestEvent, RequestStatus, User, utcnow,
)
from .scanner import Fetcher, http_fetch, scan_profile

OPEN_STATUSES = (RequestStatus.queued, RequestStatus.sent, RequestStatus.followed_up, RequestStatus.escalated)


class WorkflowError(Exception):
    pass


def _reference() -> str:
    return "PG-" + secrets.token_hex(4).upper()


def _event(req: RemovalRequest, kind: str, detail: str = "", actor: str = "system") -> None:
    req.events.append(RequestEvent(kind=kind, detail=detail, actor=actor))


def _finding(db: Session, user_id: int, broker_id: int) -> Finding:
    f = db.scalar(select(Finding).where(Finding.user_id == user_id, Finding.broker_id == broker_id))
    if f is None:
        f = Finding(user_id=user_id, broker_id=broker_id, status=FindingStatus.unchecked)
        db.add(f)
    return f


def open_request(db: Session, user_id: int, broker_id: int) -> RemovalRequest | None:
    return db.scalar(
        select(RemovalRequest).where(
            RemovalRequest.user_id == user_id,
            RemovalRequest.broker_id == broker_id,
            RemovalRequest.status.in_(OPEN_STATUSES),
        )
    )


def _recipient(broker: Broker) -> str:
    if broker.channel == Channel.email:
        return broker.privacy_email
    if broker.channel == Channel.webform:
        return broker.optout_url
    return broker.postal_address


def create_request(db: Session, user: User, broker: Broker, now: datetime | None = None) -> RemovalRequest:
    now = now or utcnow()
    profile: Profile | None = user.profile
    if profile is None or not profile.is_complete:
        raise WorkflowError("Profile incomplete.")
    if not profile.has_mandate:
        raise WorkflowError("The authorisation (mandate) has not been signed.")
    existing = open_request(db, user.id, broker.id)
    if existing:
        return existing

    lang = user.language if broker.law == "GDPR" else "en"
    reference = _reference()
    subject, body = letters.erasure_request(profile, broker, reference, lang, now)
    req = RemovalRequest(
        user_id=user.id, broker_id=broker.id, channel=broker.channel, language=lang,
        recipient=_recipient(broker), subject=subject, body=body, reference=reference,
        status=RequestStatus.queued,
    )
    db.add(req)
    _event(req, "created", f"Channel: {broker.channel.value}")
    _finding(db, user.id, broker.id).status = FindingStatus.removal_requested
    dispatch(db, req, now)
    db.commit()
    return req


def can_autosend(req: RemovalRequest) -> bool:
    """Only verified brokers with an email address are mailed without an operator looking first."""
    return req.channel == Channel.email and bool(req.recipient) and req.broker.verified


def dispatch(db: Session, req: RemovalRequest, now: datetime | None = None, actor: str = "system", force: bool = False) -> bool:
    """Try to send an email request. Requests for other channels stay queued for an operator."""
    now = now or utcnow()
    db.flush()
    if not (can_autosend(req) or (force and req.channel == Channel.email and req.recipient)):
        reason = {
            Channel.webform: "Web form - an operator must submit it.",
            Channel.postal: "Postal letter - an operator must print and send it.",
        }.get(req.channel, "Broker contact not verified - operator review required.")
        if not req.recipient:
            reason = "No contact address on file for this broker - operator must research it."
        if not any(e.kind == "queued" for e in req.events):
            _event(req, "queued", reason)
        return False
    try:
        where = mailer.send(req.recipient, req.subject, req.body, req.reference)
    except Exception as exc:  # noqa: BLE001 - record any delivery failure
        req.status = RequestStatus.failed
        _event(req, "failed", f"{exc.__class__.__name__}: {exc}", actor)
        return False
    _mark_sent(req, now, f"Emailed to {req.recipient} ({where})", actor)
    return True


def _mark_sent(req: RemovalRequest, now: datetime, detail: str, actor: str) -> None:
    req.status = RequestStatus.sent
    req.sent_at = now
    req.due_at = now + timedelta(days=letters.response_days(req.broker))
    _event(req, "sent", detail, actor)


def mark_submitted(db: Session, req: RemovalRequest, actor: str, note: str = "", now: datetime | None = None) -> None:
    """Operator confirms a web form / letter was submitted."""
    if req.status not in (RequestStatus.queued, RequestStatus.failed):
        raise WorkflowError("Request is not waiting for submission.")
    _mark_sent(req, now or utcnow(), note or f"Submitted manually via {req.channel.value}", actor)
    db.commit()


def record_outcome(db: Session, req: RemovalRequest, outcome: str, actor: str, note: str = "", now: datetime | None = None) -> None:
    now = now or utcnow()
    finding = _finding(db, req.user_id, req.broker_id)
    if outcome == "completed":
        req.status = RequestStatus.completed
        finding.status = FindingStatus.removed
        finding.removed_at = now
    elif outcome == "refused":
        req.status = RequestStatus.refused
        finding.status = FindingStatus.refused
    else:
        raise WorkflowError(f"Unknown outcome {outcome!r}")
    req.closed_at = now
    _event(req, outcome, note, actor)
    db.commit()


def request_all(db: Session, user: User) -> list[RemovalRequest]:
    """Send requests to every broker where it makes sense.

    GDPR brokers get a request regardless of scan results (address brokers cannot be searched,
    and a request costs them nothing to answer). Other brokers only where a listing was found
    or a manual check is pending.
    """
    findings = {f.broker_id: f for f in db.scalars(select(Finding).where(Finding.user_id == user.id))}
    created = []
    for broker in db.scalars(select(Broker).where(Broker.active.is_(True))):
        f = findings.get(broker.id)
        status = f.status if f else FindingStatus.unchecked
        if status in (FindingStatus.removed, FindingStatus.refused, FindingStatus.removal_requested):
            continue
        wanted = broker.law == "GDPR" or status in (
            FindingStatus.suspected, FindingStatus.confirmed, FindingStatus.manual_check,
        )
        if wanted:
            created.append(create_request(db, user, broker))
    return created


def process_deadlines(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Send reminders for overdue requests and draft complaints when reminders are ignored."""
    now = now or utcnow()
    stats = {"followed_up": 0, "escalated": 0}
    overdue = db.scalars(
        select(RemovalRequest).where(
            RemovalRequest.status.in_((RequestStatus.sent, RequestStatus.followed_up)),
            RemovalRequest.due_at.is_not(None),
            RemovalRequest.due_at <= now,
        )
    ).all()
    for req in overdue:
        profile = req.user.profile
        if req.status == RequestStatus.sent:
            subject, body = letters.followup(profile, req.broker, req.reference, req.sent_at, req.language)
            req.status = RequestStatus.followed_up
            req.followed_up_at = now
            req.due_at = now + timedelta(days=settings.followup_grace_days)
            if req.channel == Channel.email and req.recipient:
                try:
                    where = mailer.send(req.recipient, subject, body, req.reference)
                    _event(req, "followed_up", f"Reminder emailed ({where})")
                except Exception as exc:  # noqa: BLE001
                    _event(req, "failed", f"Reminder failed: {exc}")
            else:
                _event(req, "followed_up", f"Deadline passed - operator should resubmit via {req.channel.value}.\n\n{subject}\n\n{body}")
            stats["followed_up"] += 1
        else:
            req.status = RequestStatus.escalated
            req.escalated_at = now
            req.complaint_draft = letters.complaint(profile, req.broker, req.reference, req.sent_at, req.followed_up_at)
            _event(req, "escalated", "Complaint to supervisory authority drafted - review and file.")
            stats["escalated"] += 1
    db.commit()
    return stats


def rescan_due(db: Session, now: datetime | None = None, fetch: Fetcher = http_fetch) -> int:
    """Re-scan profiles whose last scan is older than the rescan interval (catches re-listings)."""
    now = now or utcnow()
    cutoff = now - timedelta(days=settings.rescan_days)
    profiles = db.scalars(
        select(Profile).where(
            Profile.mandate_signed_at.is_not(None),
            (Profile.last_scan_at.is_(None)) | (Profile.last_scan_at <= cutoff),
        )
    ).all()
    for profile in profiles:
        scan_profile(db, profile, fetch)
    return len(profiles)
