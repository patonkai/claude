from datetime import timedelta

import pytest
from sqlalchemy import select

from purgo import db as dbmod
from purgo.models import Broker, FindingStatus, Profile, RequestStatus, User, utcnow
from purgo.workflow import (
    WorkflowError, create_request, mark_submitted, process_deadlines, record_outcome, request_all,
)


def _user(s, mandate=True, lang="de"):
    u = User(email="kunde@example.at", password_hash="x", language=lang)
    u.profile = Profile(first_name="Eva", last_name="Huber", emails=["kunde@example.at"], phones=["+43 1 234"],
                        addresses=[{"street": "Gasse 2", "postal_code": "8010", "city": "Graz", "country": "AT"}],
                        mandate_signed_at=utcnow() if mandate else None, mandate_signature="Eva Huber")
    s.add(u)
    s.commit()
    return u


def _broker(s, slug):
    return s.scalar(select(Broker).where(Broker.slug == slug))


def test_requires_mandate(env):
    with dbmod.SessionLocal() as s:
        u = _user(s, mandate=False)
        with pytest.raises(WorkflowError):
            create_request(s, u, _broker(s, "mailbroker"))


def test_verified_email_broker_is_sent(env):
    with dbmod.SessionLocal() as s:
        u = _user(s)
        req = create_request(s, u, _broker(s, "mailbroker"))
        assert req.status == RequestStatus.sent
        assert req.due_at - req.sent_at == timedelta(days=30)
        assert "Art. 17" in req.body and "Eva Huber" in req.body and "Graz" in req.body
        assert req.reference in req.subject
        emls = list((env / "outbox").glob("*.eml"))
        assert len(emls) == 1 and b"privacy@mail.example" in emls[0].read_bytes()
        # idempotent while open
        assert create_request(s, u, _broker(s, "mailbroker")).id == req.id


def test_unverified_and_webform_brokers_are_queued(env):
    with dbmod.SessionLocal() as s:
        u = _user(s)
        r1 = create_request(s, u, _broker(s, "unverified"))
        r2 = create_request(s, u, _broker(s, "peoplesearch"))
        assert r1.status == r2.status == RequestStatus.queued
        assert "CCPA" in r2.subject and r2.language == "en"
        assert r2.due_at is None
        mark_submitted(s, r2, "op@example.at")
        assert r2.status == RequestStatus.sent and r2.due_at - r2.sent_at == timedelta(days=45)


def test_deadline_followup_then_escalation(env):
    with dbmod.SessionLocal() as s:
        u = _user(s)
        req = create_request(s, u, _broker(s, "mailbroker"))
        later = req.due_at + timedelta(days=1)
        assert process_deadlines(s, later) == {"followed_up": 1, "escalated": 0}
        assert req.status == RequestStatus.followed_up
        assert len(list((env / "outbox").glob("*.eml"))) == 2
        assert process_deadlines(s, later) == {"followed_up": 0, "escalated": 0}
        assert process_deadlines(s, req.due_at + timedelta(days=1)) == {"followed_up": 0, "escalated": 1}
        assert req.status == RequestStatus.escalated
        assert "Datenschutzbehörde" in req.complaint_draft and req.reference in req.complaint_draft


def test_outcome_updates_finding(env):
    with dbmod.SessionLocal() as s:
        u = _user(s)
        req = create_request(s, u, _broker(s, "mailbroker"))
        record_outcome(s, req, "completed", "op")
        f = next(f for f in u.findings if f.broker_id == req.broker_id)
        assert f.status == FindingStatus.removed and req.status == RequestStatus.completed


def test_request_all_targets_gdpr_and_found_listings(env):
    with dbmod.SessionLocal() as s:
        u = _user(s)
        created = request_all(s, u)
        slugs = sorted(r.broker.slug for r in created)
        # US brokers unscanned / not found are skipped, GDPR brokers always included
        assert slugs == ["mailbroker", "unverified"]
        # second run does not create duplicates
        assert request_all(s, u) == []


def test_english_gdpr_letter(env):
    with dbmod.SessionLocal() as s:
        u = _user(s, lang="en")
        req = create_request(s, u, _broker(s, "mailbroker"))
        assert req.subject.startswith("Erasure request") and "Art. 12(3)" in req.body
