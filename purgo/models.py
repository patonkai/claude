"""ORM models."""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Channel(str, enum.Enum):
    email = "email"
    webform = "webform"
    postal = "postal"


class FindingStatus(str, enum.Enum):
    unchecked = "unchecked"          # never scanned
    not_found = "not_found"          # scan found no listing
    suspected = "suspected"          # scan found a likely match, needs confirmation
    manual_check = "manual_check"    # broker cannot be scanned automatically
    confirmed = "confirmed"          # customer/operator confirmed the listing
    removal_requested = "removal_requested"
    removed = "removed"
    refused = "refused"              # broker refused erasure (e.g. claims legal basis)


class RequestStatus(str, enum.Enum):
    queued = "queued"            # waiting for an operator (web form / postal / missing address)
    sent = "sent"                # sent, waiting for broker response
    followed_up = "followed_up"  # deadline passed, reminder sent
    escalated = "escalated"      # complaint to supervisory authority drafted
    completed = "completed"      # broker confirmed erasure
    refused = "refused"          # broker refused
    failed = "failed"            # delivery failed


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(2), default="de")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    profile: Mapped[Profile | None] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    findings: Mapped[list[Finding]] = relationship(back_populates="user", cascade="all, delete-orphan")
    requests: Mapped[list[RemovalRequest]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Profile(Base):
    """The identifiers we search for and ask brokers to delete."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    aliases: Mapped[list] = mapped_column(JSON, default=list)       # other names, maiden names
    emails: Mapped[list] = mapped_column(JSON, default=list)
    phones: Mapped[list] = mapped_column(JSON, default=list)
    addresses: Mapped[list] = mapped_column(JSON, default=list)     # [{street, postal_code, city, country}]
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Mandate: customer authorises the operator to act on their behalf.
    mandate_signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mandate_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mandate_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_complete(self) -> bool:
        return bool(self.first_name and self.last_name and (self.emails or self.addresses))

    @property
    def has_mandate(self) -> bool:
        return self.mandate_signed_at is not None


class Broker(Base):
    __tablename__ = "brokers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str] = mapped_column(String(500), default="")
    jurisdiction: Mapped[str] = mapped_column(String(10), default="EU")   # EU | US | ...
    law: Mapped[str] = mapped_column(String(20), default="GDPR")          # GDPR | CCPA
    category: Mapped[str] = mapped_column(String(80), default="people-search")
    channel: Mapped[Channel] = mapped_column(Enum(Channel), default=Channel.email)
    privacy_email: Mapped[str] = mapped_column(String(255), default="")
    optout_url: Mapped[str] = mapped_column(String(500), default="")
    postal_address: Mapped[str] = mapped_column(Text, default="")
    search_url: Mapped[str] = mapped_column(String(500), default="")      # template with {first} {last} {city}
    instructions: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Finding(Base):
    """Exposure status of one customer at one broker."""

    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("user_id", "broker_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    broker_id: Mapped[int] = mapped_column(ForeignKey("brokers.id"))
    status: Mapped[FindingStatus] = mapped_column(Enum(FindingStatus), default=FindingStatus.unchecked)
    evidence_url: Mapped[str] = mapped_column(String(1000), default="")
    evidence_note: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="findings")
    broker: Mapped[Broker] = relationship()


class RemovalRequest(Base):
    __tablename__ = "removal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    broker_id: Mapped[int] = mapped_column(ForeignKey("brokers.id"))
    channel: Mapped[Channel] = mapped_column(Enum(Channel))
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus), default=RequestStatus.queued, index=True)
    language: Mapped[str] = mapped_column(String(2), default="de")
    recipient: Mapped[str] = mapped_column(String(500), default="")
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    followed_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    complaint_draft: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="requests")
    broker: Mapped[Broker] = relationship()
    events: Mapped[list[RequestEvent]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestEvent.created_at"
    )


class RequestEvent(Base):
    """Audit trail per request (sent, reminder, broker reply, ...)."""

    __tablename__ = "request_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("removal_requests.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    request: Mapped[RemovalRequest] = relationship(back_populates="events")
