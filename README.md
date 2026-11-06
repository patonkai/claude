# Purgo – self-hosted personal data removal service

Purgo is a DeleteMe-style service. Customers sign up and tell it what to look for (names,
emails, phones, addresses), then sign an authorisation that lets the operator act as their
representative. Purgo then:

1. **Scans** a registry of data brokers, directories and people-search sites for listings.
2. **Sends erasure requests**: GDPR Art. 17 erasure, Art. 21 objection, Art. 19 recipient
   notification and a source enquiry for EU brokers (German or English); CCPA deletion and
   opt-out as an authorised agent for US brokers.
3. **Tracks statutory deadlines**: one month under GDPR Art. 12(3), 45 days under CCPA.
4. **Follows up** automatically when the deadline passes and **drafts a complaint** to the
   supervisory authority (Austrian DSB, German state DPAs, CPPA) if the reminder is ignored too.
5. **Re-scans** every 90 days and flags re-listings.
6. Gives operators a **queue** for anything that needs a human: web forms, postal letters,
   unverified contacts, suspected matches and manual checks.

Customers can download all their data (JSON) and delete their account, which revokes the
authorisation.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PURGO_SECRET_KEY=change-me .venv/bin/uvicorn purgo.main:app --reload
# open http://localhost:8000 - the first account registered becomes the operator
```

Run the periodic jobs daily from cron (deadlines, reminders, escalations, re-scans):

```bash
.venv/bin/python -m purgo.jobs           # add --sync to reload brokers.yaml first
```

Tests: `.venv/bin/python -m pytest`

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `PURGO_SECRET_KEY` | random per start | Session signing key. **Set it**, or everyone is logged out on restart |
| `PURGO_DATABASE_URL` | `sqlite:///./purgo.db` | Any SQLAlchemy URL (PostgreSQL recommended in production) |
| `PURGO_ADMIN_EMAIL` | – | Email that becomes operator on registration (otherwise the first user) |
| `PURGO_OPERATOR_NAME` / `_ADDRESS` / `_EMAIL` | placeholder | Who signs the letters; replies go to `_EMAIL` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` | – | Outgoing mail. Without `SMTP_HOST`, mail is written to `PURGO_OUTBOX_DIR` as `.eml` files |
| `PURGO_RESPONSE_DAYS` | 30 | GDPR response deadline |
| `PURGO_FOLLOWUP_GRACE_DAYS` | 14 | Time after the reminder before escalation |
| `PURGO_RESCAN_DAYS` | 90 | Re-scan interval |
| `PURGO_BROKERS_FILE` | `purgo/brokers.yaml` | Broker registry |
| `PURGO_HTTPS_ONLY` | 0 | Set to 1 behind HTTPS for secure cookies |

## Broker registry

`purgo/brokers.yaml` ships with 18 Austrian, German and US brokers. **Every entry is marked
`verified: false`.** Contact details and opt-out URLs change often. Check each entry against
the broker's current privacy notice, fill in `privacy_email` / `postal_address`, then set
`verified: true`. Only verified brokers with an email address are mailed automatically;
everything else goes to the operator queue for review.

`search_url` templates enable automated scans (placeholders `{first} {last} {first_lower}
{last_lower} {city} {full_name}`). Brokers that block bots show up as *manual check*. A hit
is only ever *suspected* until the customer or an operator confirms it.

## Architecture

```
purgo/
  main.py       FastAPI app: customer portal + operator console (Jinja templates)
  models.py     Users, profiles, brokers, findings, removal requests, request events (audit trail)
  scanner.py    Exposure scanning with pluggable fetcher
  letters.py    GDPR (de/en), CCPA, reminder and complaint templates
  workflow.py   Request lifecycle: create -> send -> follow up -> escalate -> close
  mailer.py     SMTP or .eml outbox
  jobs.py       Cron entry point
  brokers.py    YAML registry sync
```

## Before going live

These are deliberately left to you:

- **Legal review** of the letter templates, the authorisation text and the complaint drafts.
  Under Austrian law, check whether representing customers before the DSB requires a
  lawyer's involvement for your setup.
- **Broker verification**: see above. Extend the registry; commercial services cover hundreds of brokers.
- **Inbound mail**: replies arrive at `PURGO_OPERATOR_EMAIL`. Operators record them on the
  request page. Each mail carries the reference (`PG-XXXXXXXX`) in its subject and the
  `X-Purgo-Reference` header, so an IMAP poller can match replies automatically later.
- **California DROP**: for US customers who are California residents, the state's Delete
  Request and Opt-out Platform (Delete Act) can reach all registered brokers at once.
  Consider integrating it instead of per-broker requests.
- **Billing, email verification, password reset, 2FA, rate limiting**: not included.
- **Encryption at rest** for profile data, plus backups and a retention policy. Purgo stores
  exactly the sensitive data it removes elsewhere, so document it in your own privacy notice
  and records of processing (Art. 30 GDPR).
- **Scanning etiquette**: respect broker terms of service and rate limits. Some sites forbid
  automated access; keep those as manual checks.
