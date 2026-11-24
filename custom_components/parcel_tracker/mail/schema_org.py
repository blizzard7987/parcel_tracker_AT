"""Paket Tracker AT: shipping mails that carry schema.org ``ParcelDelivery`` data.

Many shops put machine-readable delivery data into their HTML mails for Gmail's order
cards: microdata (``itemscope itemtype="http://schema.org/ParcelDelivery"`` with
``itemprop`` children) or a JSON-LD script. Google Store does (seen 2026-11-24:
"Deine Google Store-Bestellung … ist versandbereit", carrier "DHLN", number
"JVGL0600…"), so does a large part of the shops that have no parser of their own.

Read: tracking number, carrier name, the first shipped item as the parcel's name, the
expected arrival window and the order status. Never read: delivery address, recipient,
merchant address or prices. Only carriers the integration knows make a parcel; a number
of an unknown carrier is left to the generic parser.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

from ..models import ParcelStatus
from .base import MailUpdate, sent_at, subject

VIENNA = ZoneInfo("Europe/Vienna")
_VOID = frozenset({"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col"})

# schema.org carrier names (and a few shop spellings) -> carrier key
_CARRIERS = (
    (re.compile(r"\b(?:österreichische post|austrian post|post\.?at|post ag)\b", re.I), "post_at"),
    (re.compile(r"\bdhl", re.I), "dhl"),
    (re.compile(r"\bdpd\b", re.I), "dpd"),
    (re.compile(r"\bgls\b", re.I), "gls"),
    (re.compile(r"\bups\b|united parcel", re.I), "ups"),
    (re.compile(r"\bhermes\b", re.I), "hermes"),
)
_STATUS = {
    "orderprocessing": ParcelStatus.PRE_TRANSIT,
    "orderpaymentdue": ParcelStatus.PRE_TRANSIT,
    "orderintransit": ParcelStatus.IN_TRANSIT,
    "orderpickupavailable": ParcelStatus.AWAITING_PICKUP,
    "orderdelivered": ParcelStatus.DELIVERED,
    "orderproblem": ParcelStatus.EXCEPTION,
}
# "versandbereit"/"ready to ship": handed to the carrier soon, not on its way yet
# (Google marks such a mail OrderInTransit).
_NOT_YET = re.compile(r"versandbereit|ready to ship|wird (?:bald|demnächst) versendet", re.I)
_NUMBER = re.compile(r"^[A-Z0-9]{8,35}$")


class _Microdata(HTMLParser):
    """Collects microdata items as nested dicts: {"@type": ..., prop: [values]}."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self._stack: list[tuple[str, dict[str, Any] | None, str | None, list[str] | None]] = []

    def _item(self) -> dict[str, Any] | None:
        return next((it for _, it, _, _ in reversed(self._stack) if it is not None), None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        prop = a.get("itemprop") or None
        parent = self._item()
        item = None
        if "itemscope" in a:
            item = {"@type": a.get("itemtype", "").rsplit("/", 1)[-1]}
            if prop and parent is not None:
                parent.setdefault(prop, []).append(item)
            elif parent is None:
                self.items.append(item)
        value = None
        if prop and item is None and parent is not None:
            value = a.get("content") or a.get("href") or a.get("datetime") or a.get("src")
            if value:
                parent.setdefault(prop, []).append(value.strip())
        reads_text = bool(prop) and item is None and parent is not None and not value
        text: list[str] | None = [] if reads_text else None
        if tag in _VOID:
            return
        self._stack.append((tag, item, prop if text is not None else None, text))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for _tag, _, prop, text in reversed(self._stack[i:]):
                    if prop and text is not None and (joined := " ".join(" ".join(text).split())):
                        owner = next(
                            (it for _, it, _, _ in reversed(self._stack[:i]) if it is not None),
                            None,
                        )
                        if owner is not None:
                            owner.setdefault(prop, []).append(joined)
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        for _, _, prop, text in self._stack:
            if prop and text is not None:
                text.append(data)


def _first(item: dict[str, Any], key: str) -> Any:
    value = item.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = _first(value, "name")
    return " ".join(str(value).split()) or None if value else None


def _json_ld(markup: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(k).rsplit("/", 1)[-1] == "ParcelDelivery" for k in kinds):
                found.append(node)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for m in re.finditer(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", markup, re.S | re.I
    ):
        try:
            walk(json.loads(m.group(1)))
        except ValueError:
            continue
    return found


def parcel_deliveries(markup: str) -> list[dict[str, Any]]:
    """Every ParcelDelivery of an HTML mail, microdata and JSON-LD alike."""
    parser = _Microdata()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a broken mail is no parcel
        return _json_ld(markup)
    found: list[dict[str, Any]] = []

    def walk(item: dict[str, Any]) -> None:
        if item.get("@type") == "ParcelDelivery":
            found.append(item)
        for values in item.values():
            if isinstance(values, list):
                for v in values:
                    if isinstance(v, dict):
                        walk(v)

    for item in parser.items:
        walk(item)
    return found + _json_ld(markup)


def _carrier(name: str | None) -> str | None:
    if not name:
        return None
    return next((key for pattern, key in _CARRIERS if pattern.search(name)), None)


def _day(value: Any):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(VIENNA).date() if stamp.tzinfo else stamp.date()


def _status(delivery: dict[str, Any], subj: str) -> ParcelStatus | None:
    order = _first(delivery, "partOfOrder")
    raw = _first(order, "orderStatus") if isinstance(order, dict) else None
    raw = raw or _first(delivery, "deliveryStatus")
    if isinstance(raw, dict):
        raw = _first(raw, "name") or _first(raw, "@id")
    status = _STATUS.get(str(raw or "").rsplit("/", 1)[-1].lower())
    if status is ParcelStatus.IN_TRANSIT and _NOT_YET.search(subj):
        return ParcelStatus.PRE_TRANSIT
    return status


def _html(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("html",))
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, ValueError):
        return ""


def parse_schema_org(msg: EmailMessage) -> list[MailUpdate]:
    """MailUpdates of the ParcelDelivery data in a mail; [] without any."""
    markup = _html(msg)
    if "ParcelDelivery" not in markup:
        return []
    subj = subject(msg)
    sent = sent_at(msg)
    updates: list[MailUpdate] = []
    for delivery in parcel_deliveries(markup):
        number = str(_first(delivery, "trackingNumber") or "").replace(" ", "").upper()
        named = _name(_first(delivery, "carrier")) or _name(_first(delivery, "provider"))
        carrier = _carrier(named)
        if not carrier or not _NUMBER.match(number) or any(u.number == number for u in updates):
            continue
        status = _status(delivery, subj)
        start = _day(_first(delivery, "expectedArrivalFrom"))
        end = _day(_first(delivery, "expectedArrivalUntil"))
        update = MailUpdate(
            number=number,
            carrier=carrier,
            status=status,
            sent_at=sent,
            title=_name(_first(delivery, "itemShipped")),
            eta_date=start or end,
            eta_latest=end if start and end and end > start else None,
        )
        if status is ParcelStatus.DELIVERED:
            update.delivered_at = sent
        updates.append(update)
    return updates
