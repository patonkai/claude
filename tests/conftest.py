from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from purgo import db as dbmod
from purgo.config import settings
from purgo.scanner import FetchResult

TEST_BROKERS = """
brokers:
  - slug: mailbroker
    name: Mail Broker GmbH
    website: https://mail.example
    jurisdiction: AT
    law: GDPR
    channel: email
    privacy_email: privacy@mail.example
    verified: true
  - slug: unverified
    name: Unverified Broker
    website: https://unverified.example
    jurisdiction: DE
    law: GDPR
    channel: email
    privacy_email: dsb@unverified.example
  - slug: peoplesearch
    name: People Search Inc
    website: https://people.example
    jurisdiction: US
    law: CCPA
    channel: webform
    optout_url: https://people.example/optout
    search_url: https://people.example/{first}-{last}
  - slug: blocked
    name: Blocked Search
    website: https://blocked.example
    jurisdiction: US
    law: CCPA
    channel: webform
    optout_url: https://blocked.example/optout
    search_url: https://blocked.example/?q={full_name}
"""


class FakeWeb:
    def __init__(self):
        self.pages: dict[str, FetchResult] = {}
        self.calls: list[str] = []

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        for prefix, result in self.pages.items():
            if url.startswith(prefix):
                return result
        return FetchResult(404, "")


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    brokers = tmp_path / "brokers.yaml"
    brokers.write_text(TEST_BROKERS)
    monkeypatch.setattr(settings, "brokers_file", brokers)
    monkeypatch.setattr(settings, "outbox_dir", tmp_path / "outbox")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "admin_email", "")
    dbmod.configure(f"sqlite:///{tmp_path / 'test.db'}")
    dbmod.init_db()
    from purgo.brokers import sync_brokers
    with dbmod.SessionLocal() as s:
        sync_brokers(s, brokers)
    return tmp_path


@pytest.fixture()
def web():
    return FakeWeb()


@pytest.fixture()
def client(env, web):
    from purgo.main import create_app
    app = create_app()
    app.state.fetcher = web
    with TestClient(app) as c:
        yield c


def csrf_of(client: TestClient, path: str) -> str:
    html = client.get(path).text
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, f"no csrf token on {path}"
    return m.group(1)
