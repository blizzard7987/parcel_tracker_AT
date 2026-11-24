"""Austrian shops (no Home Assistant imports). Paket Tracker AT.

Shipping mails of shops seen in Austrian mailboxes. They name the carrier's number (mostly
Österreichische Post, also DPD and DHL), the carrier's lookup then tells the status. Taken:
the number(s), the shop's name as the parcel's name and what the subject says about the
status. Addresses, names, items and prices are never read into an update.

A shop's mail about a return ("Retoure", "Rücksendung") is about a parcel going back: it is
ignored. Order confirmations name no number and simply give no update.
"""

from __future__ import annotations

import re
from email.message import EmailMessage

from ..models import ParcelStatus
from .base import MailUpdate, body_text, domain_of, find_numbers, sender, sent_at, subject

# sender domain (or its parent) -> name of the parcel
AT_SHOPS: dict[str, str] = {
    "dm.at": "dm",
    "shop-apotheke.at": "Shop Apotheke",
    "apotheke.at": "apotheke.at",
    "aboutyou.at": "ABOUT YOU",
    "asambeauty.com": "asambeauty",
    "pharmeo.at": "Pharmeo",
    "apol.at": "apol",
    "exporto.at": "apol",
}

_RETURN = re.compile(r"retour|rücksend|rueckgabe|rückgabe", re.IGNORECASE)
# 14 digits behind a parcel-number label: DPD in these shops' mails (ABOUT YOU, apotheke.at).
_DPD_LABELLED = re.compile(
    r"(?<!\w)(?:Sendungsnummer|Paketnummer|Tracking-?nummer)\s*:?\s*(\d{14})(?!\d)", re.I
)
# A DHL S10 number ("CR087434728DE") next to the word DHL (Pharmeo ships from Germany).
_DHL_S10 = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{9}DE)(?![A-Za-z0-9])")

_PICKUP = re.compile(r"abgeholt\s+werden|abholbereit|zur\s+abholung", re.IGNORECASE)
_DELIVERED = re.compile(r"\bwurde\s+zugestellt\b|\bzugestellt\b(?!.*\bnicht\b)", re.IGNORECASE)
_ON_ITS_WAY = re.compile(
    r"unterwegs|auf\s+dem\s+weg|versendet|versandbestätigung|verschickt|erreicht\s+sie",
    re.IGNORECASE,
)


def at_shop(address: str) -> str | None:
    """The shop's name for a sender at one of AT_SHOPS (sub-domains too), else None."""
    domain = domain_of(address)
    while domain:
        if domain in AT_SHOPS:
            return AT_SHOPS[domain]
        domain = domain.partition(".")[2]
    return None


_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _links(msg: EmailMessage) -> str:
    """The link targets of the HTML part (a tracking link may carry the only number)."""
    part = msg.get_body(preferencelist=("html",))
    if part is None:
        return ""
    try:
        markup = part.get_content()
    except (LookupError, ValueError):
        return ""
    return "\n".join(_HREF.findall(markup))


def _status(subj: str) -> ParcelStatus | None:
    if _PICKUP.search(subj):
        return ParcelStatus.AWAITING_PICKUP
    if _DELIVERED.search(subj):
        return ParcelStatus.DELIVERED
    if _ON_ITS_WAY.search(subj):
        return ParcelStatus.IN_TRANSIT
    return None


def parse_at_shop_mail(msg: EmailMessage) -> tuple[list[MailUpdate], bool]:
    """(updates, ignored) for a mail of one of AT_SHOPS."""
    subj = subject(msg)
    if _RETURN.search(subj):
        return [], True
    shop = at_shop(sender(msg)[0])
    text = f"{subj}\n{body_text(msg)}\n{_links(msg)}"
    found = find_numbers(text, dpd="labelled")
    known = {number for _, number in found}
    found += [
        ("dpd", m.group(1)) for m in _DPD_LABELLED.finditer(text) if m.group(1) not in known
    ]
    if "dhl" in text.lower():
        found += [("dhl", m.group(1)) for m in _DHL_S10.finditer(text)]
    sent = sent_at(msg)
    status = _status(subj)
    updates: list[MailUpdate] = []
    for carrier, number in found:
        if any(u.number == number for u in updates):
            continue
        update = MailUpdate(
            number=number, carrier=carrier, status=status, sent_at=sent, title=shop
        )
        if status is ParcelStatus.DELIVERED:
            update.delivered_at = sent
        updates.append(update)
    return updates, False
