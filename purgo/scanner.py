"""Exposure scanning.

For each active broker with a ``search_url`` template, fetch the broker's public search
page and look for the customer's name (and city, if known). Many brokers block automated
access; those results fall back to ``manual_check`` so an operator can look by hand.
A hit is only ever ``suspected`` - a human confirms it before it counts as a listing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Broker, Finding, FindingStatus, Profile, utcnow


@dataclass
class FetchResult:
    status_code: int
    text: str


Fetcher = Callable[[str], FetchResult]


def http_fetch(url: str) -> FetchResult:
    with httpx.Client(
        timeout=settings.scan_timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.scan_user_agent, "Accept-Language": "en,de;q=0.8"},
    ) as client:
        resp = client.get(url)
        return FetchResult(resp.status_code, resp.text)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower())


def build_search_url(template: str, profile: Profile) -> str:
    city = ""
    if profile.addresses:
        city = (profile.addresses[0] or {}).get("city", "")
    values = {
        "first": profile.first_name,
        "last": profile.last_name,
        "first_lower": profile.first_name.lower(),
        "last_lower": profile.last_name.lower(),
        "city": city,
        "full_name": profile.full_name,
    }
    return template.format(**{k: quote(v, safe="") for k, v in values.items()})


def classify(page: str, profile: Profile) -> tuple[bool, str]:
    """Return (match, note) for a fetched page."""
    text = normalize(page)
    names = [(profile.first_name, profile.last_name)]
    for alias in profile.aliases or []:
        parts = alias.split()
        if len(parts) >= 2:
            names.append((parts[0], parts[-1]))
    for first, last in names:
        f, l = normalize(first).strip(), normalize(last).strip()
        if not f or not l:
            continue
        pattern = rf"\b{re.escape(f)}\b(?:\s+\w+\.?){{0,2}}\s+\b{re.escape(l)}\b"
        if re.search(pattern, text):
            note = f"Name '{first} {last}' appears on the results page."
            cities = [normalize(a.get("city", "")) for a in (profile.addresses or []) if a.get("city")]
            if any(c and c in text for c in cities):
                note += " City also matches."
            return True, note
    return False, "Name not found on the results page."


_STICKY = {FindingStatus.confirmed, FindingStatus.removal_requested, FindingStatus.refused}


def scan_broker(db: Session, profile: Profile, broker: Broker, fetch: Fetcher = http_fetch) -> Finding:
    finding = db.scalar(select(Finding).where(Finding.user_id == profile.user_id, Finding.broker_id == broker.id))
    if finding is None:
        finding = Finding(user_id=profile.user_id, broker_id=broker.id, status=FindingStatus.unchecked)
        db.add(finding)

    finding.last_checked_at = utcnow()

    if not broker.search_url:
        if finding.status in (FindingStatus.unchecked, FindingStatus.not_found):
            finding.status = FindingStatus.manual_check
            finding.evidence_note = "This broker cannot be searched automatically."
        return finding

    url = build_search_url(broker.search_url, profile)
    try:
        result = fetch(url)
    except httpx.HTTPError as exc:
        if finding.status not in _STICKY | {FindingStatus.removed}:
            finding.status = FindingStatus.manual_check
        finding.evidence_note = f"Automatic check failed ({exc.__class__.__name__}); please check manually."
        finding.evidence_url = url
        return finding

    if result.status_code == 404:
        found, note = False, "Search returned 404 (no listing)."
    elif result.status_code >= 400:
        if finding.status not in _STICKY | {FindingStatus.removed}:
            finding.status = FindingStatus.manual_check
        finding.evidence_note = f"Broker blocked the automated check (HTTP {result.status_code}); please check manually."
        finding.evidence_url = url
        return finding
    else:
        found, note = classify(result.text, profile)

    finding.evidence_url = url
    if found:
        if finding.status == FindingStatus.removed:
            finding.status = FindingStatus.suspected
            finding.evidence_note = "Re-listed after removal. " + note
        elif finding.status not in _STICKY:
            finding.status = FindingStatus.suspected
            finding.evidence_note = note
        else:
            finding.evidence_note = note
    else:
        if finding.status == FindingStatus.removal_requested:
            finding.status = FindingStatus.removed
            finding.removed_at = utcnow()
            finding.evidence_note = "Listing no longer found after removal request."
        elif finding.status not in _STICKY | {FindingStatus.removed}:
            finding.status = FindingStatus.not_found
            finding.evidence_note = note
    return finding


def scan_profile(db: Session, profile: Profile, fetch: Fetcher = http_fetch) -> list[Finding]:
    brokers = db.scalars(select(Broker).where(Broker.active.is_(True)).order_by(Broker.name)).all()
    findings = [scan_broker(db, profile, b, fetch) for b in brokers]
    profile.last_scan_at = utcnow()
    db.commit()
    return findings
