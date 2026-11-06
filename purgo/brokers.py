"""Broker registry: YAML file synced into the database."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Broker, Channel

_FIELDS = (
    "name", "website", "jurisdiction", "law", "category", "privacy_email", "optout_url",
    "postal_address", "search_url", "instructions", "verified", "active",
)


def load_registry(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    entries = data.get("brokers", [])
    for entry in entries:
        if "slug" not in entry or "name" not in entry:
            raise ValueError(f"Broker entry missing slug/name: {entry!r}")
        Channel(entry.get("channel", "email"))  # validate
    return entries


def sync_brokers(db: Session, path: Path) -> int:
    """Insert/update brokers from the registry. Brokers removed from the file are deactivated."""
    entries = load_registry(path)
    existing = {b.slug: b for b in db.scalars(select(Broker))}
    seen = set()
    for entry in entries:
        slug = entry["slug"]
        seen.add(slug)
        broker = existing.get(slug) or Broker(slug=slug)
        for f in _FIELDS:
            if f in entry:
                value = entry[f]
                setattr(broker, f, value.strip() if isinstance(value, str) else value)
        broker.channel = Channel(entry.get("channel", "email"))
        if broker.active is None:
            broker.active = True
        db.add(broker)
    for slug, broker in existing.items():
        if slug not in seen:
            broker.active = False
    db.commit()
    return len(entries)
