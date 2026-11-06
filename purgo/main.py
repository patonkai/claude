"""FastAPI web application: customer portal + operator console."""

from __future__ import annotations

import json
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import db as dbmod
from .brokers import sync_brokers
from .config import BASE_DIR, settings
from .models import (
    Broker, Finding, FindingStatus, Profile, RemovalRequest, RequestEvent, RequestStatus, User, utcnow,
)
from .scanner import http_fetch, scan_profile
from .security import check_csrf, csrf_token, hash_password, verify_password
from .workflow import (
    OPEN_STATUSES, WorkflowError, create_request, dispatch, mark_submitted, record_outcome, request_all,
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

MANDATE_TEXT = (
    "I authorise {operator} to act as my representative towards data brokers, people-search "
    "sites and similar companies in order to exercise my rights to access, erasure and objection "
    "(Art. 15, 17 and 21 GDPR; for US companies as my authorised agent under the CCPA and other "
    "state privacy laws). This includes sending requests in my name, receiving replies and "
    "drafting complaints to supervisory authorities for my approval. I may revoke this "
    "authorisation at any time by deleting my account."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    dbmod.init_db()
    with dbmod.SessionLocal() as session:
        sync_brokers(session, settings.brokers_file)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Purgo", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
        https_only=settings.https_only_cookies, max_age=60 * 60 * 8,
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.state.fetcher = http_fetch
    register_routes(app)
    return app


# ------------------------------------------------------------------ helpers

class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def get_db():
    yield from dbmod.get_db()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None:
        raise Redirect("/login")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Operators only")
    return user


async def csrf(request: Request) -> None:
    if request.method == "POST":
        form = await request.form()
        if not check_csrf(request.session, form.get("csrf")):
            raise HTTPException(400, "Invalid or missing CSRF token - reload the page and try again.")


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("user", None)
    flashes = request.session.pop("flash", [])
    return templates.TemplateResponse(
        request, name,
        {"csrf": csrf_token(request.session), "flashes": flashes, "settings": settings,
         "FindingStatus": FindingStatus, "RequestStatus": RequestStatus, **ctx},
    )


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def go(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def parse_address(line: str) -> dict:
    """'Street 1, 1010 Vienna, AT' -> dict."""
    parts = [p.strip() for p in line.split(",")]
    addr = {"street": parts[0] if parts else "", "postal_code": "", "city": "", "country": ""}
    if len(parts) > 1:
        pc_city = parts[1].split(maxsplit=1)
        if len(pc_city) == 2 and any(ch.isdigit() for ch in pc_city[0]):
            addr["postal_code"], addr["city"] = pc_city
        else:
            addr["city"] = parts[1]
    if len(parts) > 2:
        addr["country"] = parts[2]
    return addr


def format_address(a: dict) -> str:
    return ", ".join(p for p in [a.get("street", ""), f"{a.get('postal_code', '')} {a.get('city', '')}".strip(), a.get("country", "")] if p)


templates.env.filters["address"] = format_address
templates.env.filters["dt"] = lambda d: d.strftime("%d.%m.%Y %H:%M") if d else "-"
templates.env.filters["d"] = lambda d: d.strftime("%d.%m.%Y") if d else "-"
templates.env.filters["label"] = lambda s: (s.value if hasattr(s, "value") else str(s)).replace("_", " ")


def _run_scan(user_id: int, fetcher) -> None:
    with dbmod.SessionLocal() as session:
        profile = session.scalar(select(Profile).where(Profile.user_id == user_id))
        if profile:
            scan_profile(session, profile, fetcher)


def _own_request(db: Session, user: User, rid: int) -> RemovalRequest:
    req = db.get(RemovalRequest, rid)
    if req is None or (req.user_id != user.id and not user.is_admin):
        raise HTTPException(404)
    return req


# ------------------------------------------------------------------ routes

def register_routes(app: FastAPI) -> None:

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return go(exc.url)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, db: Session = Depends(get_db)):
        uid = request.session.get("uid")
        user = db.get(User, uid) if uid else None
        broker_count = len(db.scalars(select(Broker.id).where(Broker.active.is_(True))).all())
        return render(request, "index.html", user=user, broker_count=broker_count)

    # ---- auth
    @app.get("/register", response_class=HTMLResponse)
    def register_form(request: Request):
        return render(request, "auth.html", mode="register")

    @app.post("/register", dependencies=[Depends(csrf)])
    def register(request: Request, email: str = Form(...), password: str = Form(...),
                 language: str = Form("de"), db: Session = Depends(get_db)):
        email = email.strip().lower()
        if len(password) < 10:
            flash(request, "Password must be at least 10 characters.", "error")
            return go("/register")
        if db.scalar(select(User).where(User.email == email)):
            flash(request, "An account with this email already exists.", "error")
            return go("/register")
        first_user = db.scalar(select(User.id).limit(1)) is None
        user = User(
            email=email, password_hash=hash_password(password), language=language if language in ("de", "en") else "de",
            is_admin=(email == settings.admin_email) or (first_user and not settings.admin_email),
        )
        user.profile = Profile(emails=[email])
        db.add(user)
        db.commit()
        request.session.clear()
        request.session["uid"] = user.id
        flash(request, "Welcome! Complete your profile so we know what to look for.")
        return go("/profile")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "auth.html", mode="login")

    @app.post("/login", dependencies=[Depends(csrf)])
    def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if not user or not verify_password(password, user.password_hash):
            flash(request, "Wrong email or password.", "error")
            return go("/login")
        request.session.clear()
        request.session["uid"] = user.id
        return go("/admin" if user.is_admin else "/dashboard")

    @app.post("/logout", dependencies=[Depends(csrf)])
    def logout(request: Request):
        request.session.clear()
        return go("/")

    # ---- customer portal
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        brokers = db.scalars(select(Broker).where(Broker.active.is_(True)).order_by(Broker.jurisdiction, Broker.name)).all()
        findings = {f.broker_id: f for f in user.findings}
        requests_by_broker = {}
        for r in sorted(user.requests, key=lambda r: r.created_at):
            requests_by_broker[r.broker_id] = r
        counts = Counter(f.status for f in user.findings)
        req_counts = Counter(r.status for r in user.requests)
        return render(request, "dashboard.html", user=user, profile=user.profile, brokers=brokers,
                      findings=findings, requests_by_broker=requests_by_broker, counts=counts, req_counts=req_counts)

    @app.get("/profile", response_class=HTMLResponse)
    def profile_form(request: Request, user: User = Depends(current_user)):
        return render(request, "profile.html", user=user, profile=user.profile)

    @app.post("/profile", dependencies=[Depends(csrf)])
    def profile_save(request: Request, first_name: str = Form(""), last_name: str = Form(""),
                     aliases: str = Form(""), emails: str = Form(""), phones: str = Form(""),
                     addresses: str = Form(""), birth_date: str = Form(""), language: str = Form("de"),
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
        p = user.profile or Profile(user_id=user.id)
        p.first_name, p.last_name = first_name.strip(), last_name.strip()
        p.aliases, p.emails, p.phones = _lines(aliases), _lines(emails), _lines(phones)
        p.addresses = [parse_address(line) for line in _lines(addresses)]
        try:
            p.birth_date = date.fromisoformat(birth_date) if birth_date else None
        except ValueError:
            flash(request, "Birth date must be YYYY-MM-DD.", "error")
            return go("/profile")
        user.language = language if language in ("de", "en") else "de"
        user.profile = p
        db.commit()
        if not p.is_complete:
            flash(request, "Saved. We need at least your name and an email or postal address.", "error")
            return go("/profile")
        flash(request, "Profile saved.")
        return go("/dashboard" if p.has_mandate else "/mandate")

    @app.get("/mandate", response_class=HTMLResponse)
    def mandate_form(request: Request, user: User = Depends(current_user)):
        return render(request, "mandate.html", user=user, profile=user.profile,
                      mandate=MANDATE_TEXT.format(operator=settings.operator_name))

    @app.post("/mandate", dependencies=[Depends(csrf)])
    def mandate_sign(request: Request, signature: str = Form(""), agree: str = Form(""),
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
        p = user.profile
        if not p or not p.is_complete:
            flash(request, "Complete your profile first.", "error")
            return go("/profile")
        if agree != "yes" or signature.strip().casefold() != p.full_name.casefold():
            flash(request, "Tick the box and type your full name exactly as in your profile to sign.", "error")
            return go("/mandate")
        p.mandate_signed_at = utcnow()
        p.mandate_signature = signature.strip()
        p.mandate_ip = request.client.host if request.client else None
        db.commit()
        flash(request, "Authorisation signed. Run your first scan.")
        return go("/dashboard")

    @app.post("/scan", dependencies=[Depends(csrf)])
    def scan(request: Request, background: BackgroundTasks, user: User = Depends(current_user)):
        if not user.profile or not user.profile.is_complete:
            flash(request, "Complete your profile first.", "error")
            return go("/profile")
        background.add_task(_run_scan, user.id, request.app.state.fetcher)
        flash(request, "Scan started. Refresh in a minute to see results.")
        return go("/dashboard")

    @app.post("/findings/{fid}/{action}", dependencies=[Depends(csrf)])
    def finding_action(fid: int, action: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        f = db.get(Finding, fid)
        if f is None or (f.user_id != user.id and not user.is_admin):
            raise HTTPException(404)
        mapping = {"confirm": FindingStatus.confirmed, "dismiss": FindingStatus.not_found}
        if action not in mapping:
            raise HTTPException(404)
        f.status = mapping[action]
        db.commit()
        return go(request.headers.get("referer") or "/dashboard")

    @app.post("/requests/all", dependencies=[Depends(csrf)])
    def requests_all(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        try:
            created = request_all(db, user)
        except WorkflowError as exc:
            flash(request, str(exc), "error")
            return go("/mandate")
        flash(request, f"{len(created)} removal requests created.")
        return go("/dashboard")

    @app.post("/brokers/{bid}/request", dependencies=[Depends(csrf)])
    def request_one(bid: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        broker = db.get(Broker, bid)
        if broker is None or not broker.active:
            raise HTTPException(404)
        try:
            req = create_request(db, user, broker)
        except WorkflowError as exc:
            flash(request, str(exc), "error")
            return go("/mandate")
        return go(f"/requests/{req.id}")

    @app.get("/requests/{rid}", response_class=HTMLResponse)
    def request_detail(rid: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        req = _own_request(db, user, rid)
        return render(request, "request.html", user=user, req=req)

    @app.get("/account/export")
    def export(user: User = Depends(current_user)):
        p = user.profile
        data = {
            "email": user.email,
            "created_at": user.created_at.isoformat(),
            "profile": None if not p else {
                "first_name": p.first_name, "last_name": p.last_name, "aliases": p.aliases, "emails": p.emails,
                "phones": p.phones, "addresses": p.addresses,
                "birth_date": p.birth_date.isoformat() if p.birth_date else None,
                "mandate_signed_at": p.mandate_signed_at.isoformat() if p.mandate_signed_at else None,
            },
            "findings": [{"broker": f.broker.name, "status": f.status.value, "evidence_url": f.evidence_url} for f in user.findings],
            "requests": [{"broker": r.broker.name, "reference": r.reference, "status": r.status.value,
                          "sent_at": r.sent_at.isoformat() if r.sent_at else None, "subject": r.subject, "body": r.body,
                          "events": [{"kind": e.kind, "detail": e.detail, "at": e.created_at.isoformat()} for e in r.events]}
                         for r in user.requests],
        }
        return Response(json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=purgo-export.json"})

    @app.post("/account/delete", dependencies=[Depends(csrf)])
    def delete_account(request: Request, confirm: str = Form(""), user: User = Depends(current_user), db: Session = Depends(get_db)):
        if confirm != "DELETE":
            flash(request, "Type DELETE to confirm.", "error")
            return go("/profile")
        db.delete(user)
        db.commit()
        request.session.clear()
        return go("/")

    # ---- operator console
    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)):
        def by_status(*statuses):
            return db.scalars(select(RemovalRequest).where(RemovalRequest.status.in_(statuses))
                              .order_by(RemovalRequest.created_at)).all()
        manual = db.scalars(select(Finding).where(Finding.status.in_((FindingStatus.manual_check, FindingStatus.suspected)))
                            .order_by(Finding.last_checked_at)).all()
        all_reqs = db.scalars(select(RemovalRequest)).all()
        stats = {
            "customers": len(db.scalars(select(User.id)).all()),
            "open": sum(r.status in OPEN_STATUSES for r in all_reqs),
            "completed": sum(r.status == RequestStatus.completed for r in all_reqs),
            "refused": sum(r.status == RequestStatus.refused for r in all_reqs),
        }
        return render(request, "admin.html", user=user, queued=by_status(RequestStatus.queued, RequestStatus.failed),
                      waiting=by_status(RequestStatus.sent, RequestStatus.followed_up),
                      escalated=by_status(RequestStatus.escalated), manual=manual, stats=stats, now=utcnow())

    @app.post("/admin/requests/{rid}/{action}", dependencies=[Depends(csrf)])
    def admin_request_action(rid: int, action: str, request: Request, note: str = Form(""),
                             user: User = Depends(admin_user), db: Session = Depends(get_db)):
        req = _own_request(db, user, rid)
        try:
            if action == "submitted":
                mark_submitted(db, req, user.email, note)
            elif action == "send":
                if not dispatch(db, req, actor=user.email, force=True):
                    flash(request, "Could not send - check the recipient address / event log.", "error")
                db.commit()
            elif action in ("completed", "refused"):
                record_outcome(db, req, action, user.email, note)
            elif action == "note":
                req.events.append(RequestEvent(kind="note", detail=note, actor=user.email))
                db.commit()
            elif action == "recipient":
                req.recipient = note.strip()
                db.commit()
            else:
                raise HTTPException(404)
        except WorkflowError as exc:
            flash(request, str(exc), "error")
        return go(f"/requests/{rid}")

    @app.get("/admin/brokers", response_class=HTMLResponse)
    def admin_brokers(request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)):
        brokers = db.scalars(select(Broker).order_by(Broker.jurisdiction, Broker.name)).all()
        return render(request, "brokers.html", user=user, brokers=brokers)

    @app.post("/admin/brokers/sync", dependencies=[Depends(csrf)])
    def admin_sync(request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)):
        n = sync_brokers(db, settings.brokers_file)
        flash(request, f"Synced {n} brokers from {settings.brokers_file.name}.")
        return go("/admin/brokers")

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"ok": True})


app = create_app()
