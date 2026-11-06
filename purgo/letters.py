"""Letter templates for erasure requests, reminders and regulator complaints."""

from __future__ import annotations

from datetime import datetime

from .config import settings
from .models import Broker, Profile

RESPONSE_DAYS = {"GDPR": 30, "CCPA": 45}

AUTHORITIES = {
    "AT": "Österreichische Datenschutzbehörde, Barichgasse 40-42, 1030 Wien",
    "DE": "the competent German data protection supervisory authority (Landesdatenschutzbehörde) of the broker's seat",
    "US": "California Privacy Protection Agency (cppa.ca.gov) and the California Attorney General",
}


def response_days(broker: Broker) -> int:
    if broker.law == "GDPR":
        return settings.response_days
    return RESPONSE_DAYS.get(broker.law, settings.response_days)


def _fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def identifiers(profile: Profile, lang: str) -> str:
    L = {
        "de": ("Name", "Weitere Namen", "Geburtsdatum", "E-Mail", "Telefon", "Adresse"),
        "en": ("Name", "Other names", "Date of birth", "Email", "Phone", "Address"),
    }[lang]
    lines = [f"- {L[0]}: {profile.full_name}"]
    if profile.aliases:
        lines.append(f"- {L[1]}: {', '.join(profile.aliases)}")
    if profile.birth_date:
        lines.append(f"- {L[2]}: {_fmt_date(profile.birth_date)}")
    for e in profile.emails or []:
        lines.append(f"- {L[3]}: {e}")
    for p in profile.phones or []:
        lines.append(f"- {L[4]}: {p}")
    for a in profile.addresses or []:
        parts = [a.get("street", ""), f"{a.get('postal_code', '')} {a.get('city', '')}".strip(), a.get("country", "")]
        lines.append(f"- {L[5]}: {', '.join(p for p in parts if p)}")
    return "\n".join(lines)


def _mandate_line(profile: Profile, lang: str) -> str:
    when = _fmt_date(profile.mandate_signed_at)
    if lang == "de":
        return (f"Wir vertreten {profile.full_name} aufgrund einer am {when} erteilten Vollmacht "
                "und berufen uns auf diese. Eine Kopie übermitteln wir auf Anfrage.")
    return (f"We act on behalf of {profile.full_name} under a written authorisation dated {when}. "
            "A copy is available on request.")


def _signature() -> str:
    lines = [settings.operator_name]
    if settings.operator_address:
        lines.append(settings.operator_address)
    lines.append(settings.operator_email)
    return "\n".join(lines)


def erasure_request(profile: Profile, broker: Broker, reference: str, lang: str, now: datetime) -> tuple[str, str]:
    days = response_days(broker)
    if broker.law == "CCPA":
        return _ccpa_request(profile, broker, reference, days)
    if lang == "de":
        subject = f"Antrag auf Löschung und Widerspruch gem. Art. 17, 21 DSGVO - Ref. {reference}"
        body = f"""Sehr geehrte Damen und Herren,

{_mandate_line(profile, 'de')}

Namens und auftrags unserer Mandantschaft

1. verlangen wir die unverzügliche Löschung sämtlicher zu ihrer Person gespeicherter
   personenbezogener Daten gemäß Art. 17 DSGVO;
2. widersprechen wir jeder Verarbeitung dieser Daten, insbesondere zu Zwecken der
   Direktwerbung und des Adresshandels, gemäß Art. 21 Abs. 2 und 3 DSGVO;
3. ersuchen wir gemäß Art. 19 DSGVO, sämtliche Empfänger, denen Sie die Daten offengelegt
   haben, von der Löschung zu verständigen, und uns diese Empfänger zu nennen;
4. ersuchen wir um Mitteilung, aus welcher Quelle die Daten stammen (Art. 14 Abs. 2 lit. f
   bzw. Art. 15 Abs. 1 lit. g DSGVO).

Zur Identifikation dienen folgende Angaben (bitte ausschließlich zur Bearbeitung dieses
Antrags verwenden):

{identifiers(profile, 'de')}

Bitte bestätigen Sie die Löschung gemäß Art. 12 Abs. 3 DSGVO binnen eines Monats ab
Eingang dieses Schreibens unter Angabe der Referenz {reference}. Andernfalls behalten wir
uns eine Beschwerde bei der zuständigen Aufsichtsbehörde vor.

Mit freundlichen Grüßen

{_signature()}
"""
    else:
        subject = f"Erasure request and objection under Art. 17, 21 GDPR - Ref. {reference}"
        body = f"""Dear Sir or Madam,

{_mandate_line(profile, 'en')}

On behalf of our client we

1. request the erasure without undue delay of all personal data you hold about them
   (Art. 17 GDPR);
2. object to any processing of that data, in particular for direct marketing and the
   trading of address data (Art. 21(2) and (3) GDPR);
3. ask you to notify every recipient to whom the data was disclosed of the erasure and to
   tell us who those recipients are (Art. 19 GDPR);
4. ask you to tell us the source of the data (Art. 14(2)(f) and Art. 15(1)(g) GDPR).

To identify our client, please use the following details solely to handle this request:

{identifiers(profile, 'en')}

Please confirm the erasure within one month (Art. 12(3) GDPR), quoting reference
{reference}. Otherwise we reserve the right to lodge a complaint with the competent
supervisory authority.

Yours faithfully,

{_signature()}
"""
    return subject, body


def _ccpa_request(profile: Profile, broker: Broker, reference: str, days: int) -> tuple[str, str]:
    subject = f"Request to delete and opt out of sale/sharing (CCPA) - Ref. {reference}"
    body = f"""To the privacy team of {broker.name},

I am an authorized agent acting on behalf of the consumer named below, who has given me
signed permission to submit this request. Under the California Consumer Privacy Act
(Cal. Civ. Code §§ 1798.105, 1798.120) and applicable state privacy laws, the consumer

1. requests deletion of all personal information you have collected about them;
2. opts out of the sale and sharing of their personal information;
3. asks you to direct your service providers and contractors to delete it as well.

Consumer details (to be used only to process this request):

{identifiers(profile, 'en')}

Please confirm completion within {days} days, quoting reference {reference}.

Sincerely,

{_signature()}
"""
    return subject, body


def followup(profile: Profile, broker: Broker, reference: str, sent_at: datetime, lang: str) -> tuple[str, str]:
    if lang == "de" and broker.law == "GDPR":
        subject = f"Erinnerung: Löschungsantrag vom {_fmt_date(sent_at)} - Ref. {reference}"
        body = f"""Sehr geehrte Damen und Herren,

am {_fmt_date(sent_at)} haben wir namens {profile.full_name} die Löschung ihrer
personenbezogenen Daten verlangt (Ref. {reference}). Die Frist des Art. 12 Abs. 3 DSGVO ist
abgelaufen, ohne dass wir eine Antwort erhalten haben.

Wir fordern Sie auf, den Antrag binnen {settings.followup_grace_days} Tagen zu erledigen und uns dies
zu bestätigen. Nach fruchtlosem Ablauf werden wir Beschwerde bei der Aufsichtsbehörde erheben.

Mit freundlichen Grüßen

{_signature()}
"""
    else:
        subject = f"Reminder: request of {_fmt_date(sent_at)} - Ref. {reference}"
        body = f"""Dear Sir or Madam,

On {_fmt_date(sent_at)} we asked you on behalf of {profile.full_name} to delete their
personal data (Ref. {reference}). The statutory deadline has passed without a response.

Please complete the request within {settings.followup_grace_days} days and confirm this to us.
Otherwise we will file a complaint with the competent authority.

Yours faithfully,

{_signature()}
"""
    return subject, body


def complaint(profile: Profile, broker: Broker, reference: str, sent_at: datetime, followed_up_at: datetime | None) -> str:
    authority = AUTHORITIES.get(broker.jurisdiction, "the competent supervisory authority")
    if broker.law == "GDPR" and broker.jurisdiction == "AT":
        return f"""An die {authority}

Beschwerde gemäß Art. 77 DSGVO iVm § 24 DSG

Beschwerdeführer/in: {profile.full_name}
vertreten durch: {settings.operator_name}
Beschwerdegegner: {broker.name} ({broker.website})

Sachverhalt:
Am {_fmt_date(sent_at)} wurde namens der Beschwerdeführerin/des Beschwerdeführers die Löschung
(Art. 17 DSGVO) sowie Widerspruch (Art. 21 DSGVO) geltend gemacht (Ref. {reference}).
{f'Am {_fmt_date(followed_up_at)} wurde erinnert. ' if followed_up_at else ''}Die Beschwerdegegnerin hat innerhalb der Frist
des Art. 12 Abs. 3 DSGVO weder reagiert noch die Löschung bestätigt.

Beantragt wird, die Verletzung im Recht auf Löschung und Auskunft festzustellen und der
Beschwerdegegnerin die Löschung aufzutragen.

Beilagen: Löschungsantrag, Erinnerung, Vollmacht

[ENTWURF - vor Einbringung prüfen]
"""
    return f"""To: {authority}

Complaint regarding {broker.name} ({broker.website})

Data subject: {profile.full_name}, represented by {settings.operator_name}

On {_fmt_date(sent_at)} a request to delete personal data was sent on behalf of the data
subject (Ref. {reference}).{f' A reminder was sent on {_fmt_date(followed_up_at)}.' if followed_up_at else ''}
The company has not responded or confirmed the deletion within the statutory deadline.

We ask the authority to investigate and order the company to comply.

Enclosures: original request, reminder, authorisation

[DRAFT - review before filing]
"""
