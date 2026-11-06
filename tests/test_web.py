from purgo.scanner import FetchResult

from .conftest import csrf_of


def _signup(client, email="eva@example.at"):
    token = csrf_of(client, "/register")
    r = client.post("/register", data={"csrf": token, "email": email, "password": "correct horse battery", "language": "de"})
    assert r.status_code == 200 and "Your profile" in r.text
    token = csrf_of(client, "/profile")
    r = client.post("/profile", data={
        "csrf": token, "first_name": "Eva", "last_name": "Huber", "emails": email,
        "addresses": "Gasse 2, 8010 Graz, AT", "phones": "", "aliases": "Eva Maier", "birth_date": "1980-02-03",
        "language": "de",
    })
    assert "Authorisation" in r.text
    token = csrf_of(client, "/mandate")
    r = client.post("/mandate", data={"csrf": token, "agree": "yes", "signature": "eva huber"})
    assert "Dashboard" in r.text and "Authorisation signed" in r.text


def test_csrf_required(client):
    r = client.post("/register", data={"email": "x@y.z", "password": "0123456789"})
    assert r.status_code == 400


def test_full_customer_and_operator_flow(client, web):
    web.pages["https://people.example/"] = FetchResult(200, "<p>Eva Huber, Graz</p>")
    _signup(client)  # first user becomes operator

    token = csrf_of(client, "/dashboard")
    r = client.post("/scan", data={"csrf": token})
    assert r.status_code == 200
    page = client.get("/dashboard").text
    assert "suspected" in page and "City also matches" in page

    r = client.post("/requests/all", data={"csrf": token})
    assert "3 removal requests created" in r.text  # 2 GDPR + 1 suspected US listing

    admin = client.get("/admin").text
    assert "To submit" in admin and "People Search Inc" in admin

    export = client.get("/account/export").json()
    assert export["profile"]["last_name"] == "Huber" and len(export["requests"]) == 3


def test_customers_cannot_see_each_other(client):
    _signup(client, "op@example.at")
    token = csrf_of(client, "/dashboard")
    client.post("/requests/all", data={"csrf": token})
    client.post("/logout", data={"csrf": token})

    _signup(client, "other@example.at")
    assert client.get("/requests/1").status_code == 404
    assert client.get("/admin").status_code == 403


def test_mandate_signature_must_match(client):
    token = csrf_of(client, "/register")
    client.post("/register", data={"csrf": token, "email": "a@b.at", "password": "0123456789ab"})
    token = csrf_of(client, "/profile")
    client.post("/profile", data={"csrf": token, "first_name": "A", "last_name": "B", "emails": "a@b.at"})
    token = csrf_of(client, "/mandate")
    r = client.post("/mandate", data={"csrf": token, "agree": "yes", "signature": "Someone Else"})
    assert "type your full name" in r.text.lower()
