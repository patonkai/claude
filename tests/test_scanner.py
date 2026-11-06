from purgo import db as dbmod
from purgo.models import FindingStatus, Profile, User
from purgo.scanner import FetchResult, build_search_url, classify, scan_profile


def _profile(s):
    u = User(email="a@b.c", password_hash="x")
    u.profile = Profile(first_name="Jürgen", last_name="Müller", emails=["a@b.c"],
                        addresses=[{"street": "Ring 1", "postal_code": "1010", "city": "Wien", "country": "AT"}])
    s.add(u)
    s.commit()
    return u.profile


def test_build_search_url_quotes_values():
    p = Profile(first_name="Anna Maria", last_name="O'Neil", addresses=[{"city": "Graz"}])
    assert build_search_url("https://x/{first}-{last}?c={city}", p) == "https://x/Anna%20Maria-O%27Neil?c=Graz"


def test_classify_handles_accents_and_middle_names():
    p = Profile(first_name="Jürgen", last_name="Müller", aliases=[], addresses=[{"city": "Wien"}])
    hit, note = classify("<li>Jurgen K. Muller, age 52, Wien</li>", p)
    assert hit and "City" in note
    assert not classify("<li>Jürgen Schmidt</li>", p)[0]


def test_scan_statuses(env, web):
    web.pages["https://people.example/"] = FetchResult(200, "<h1>Jürgen Müller</h1>")
    web.pages["https://blocked.example/"] = FetchResult(403, "denied")
    with dbmod.SessionLocal() as s:
        profile = _profile(s)
        scan_profile(s, profile, web)
        by_slug = {f.broker.slug: f for f in profile.user.findings}
        assert by_slug["peoplesearch"].status == FindingStatus.suspected
        assert by_slug["blocked"].status == FindingStatus.manual_check
        assert by_slug["mailbroker"].status == FindingStatus.manual_check  # no search_url
        assert profile.last_scan_at is not None


def test_rescan_detects_removal_and_relisting(env, web):
    with dbmod.SessionLocal() as s:
        profile = _profile(s)
        scan_profile(s, profile, web)
        f = next(f for f in profile.user.findings if f.broker.slug == "peoplesearch")
        f.status = FindingStatus.removal_requested
        s.commit()
        scan_profile(s, profile, web)  # 404 now -> removed
        assert f.status == FindingStatus.removed
        web.pages["https://people.example/"] = FetchResult(200, "Jürgen Müller lives here")
        scan_profile(s, profile, web)
        assert f.status == FindingStatus.suspected
        assert "Re-listed" in f.evidence_note
