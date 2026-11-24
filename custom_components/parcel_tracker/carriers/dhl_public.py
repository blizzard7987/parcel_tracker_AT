"""Paket Tracker AT: DHL without an API key, from DHL's own public tracking pages.

Two sources, both asked with the number only:

- DHL Parcel NL / DHL eCommerce Europe (``JVGL…`` and the like, Google Store's "DHLN"):
  ``api-gw.dhlparcel.nl/track-trace?key=<number>`` answers JSON without any key (seen
  live 2026-11-24).
- DHL Germany: the tracking page ``dhl.de/int-verfolgen/data/search`` answers JSON once
  the session holds the cookies of the tracking page (seen live 2026-11-24 in a browser).
  It sits behind a bot protection: a refusal is "no key" (status from mails, no repair
  issue), never "not found".

Of the answers only status, time, place and text of the events are read; no address,
no name, no signature.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from ..const import MAX_EVENTS
from ..models import ParcelStatus, TrackingEvent, TrackingResult
from .base import CarrierUnavailable, MissingCredentials, NotFound, ParseError, plain_text

VIENNA = ZoneInfo("Europe/Vienna")
DHL_NL_URL = "https://api-gw.dhlparcel.nl/track-trace"
DHL_DE_PAGE = "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html"
DHL_DE_URL = "https://www.dhl.de/int-verfolgen/data/search"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(total=25)

# ----- DHL Parcel NL ----------------------------------------------------------------------
_NL_CATEGORY = {
    "DATA_RECEIVED": ParcelStatus.PRE_TRANSIT,
    "UNDERWAY": ParcelStatus.IN_TRANSIT,
    "IN_TRANSIT": ParcelStatus.IN_TRANSIT,
    "CUSTOMS": ParcelStatus.IN_TRANSIT,
    "LEG": ParcelStatus.IN_TRANSIT,
    "IN_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
    "PROBLEM": ParcelStatus.EXCEPTION,
    "EXCEPTION": ParcelStatus.EXCEPTION,
    "INTERVENTION": ParcelStatus.EXCEPTION,
}
_NL_TEXT = {
    ParcelStatus.PRE_TRANSIT: "Sendungsdaten an DHL übermittelt",
    ParcelStatus.IN_TRANSIT: "Unterwegs",
    ParcelStatus.OUT_FOR_DELIVERY: "In Zustellung",
    ParcelStatus.AWAITING_PICKUP: "Zur Abholung bereit",
    ParcelStatus.DELIVERED: "Zugestellt",
    ParcelStatus.EXCEPTION: "Problem bei der Zustellung",
}


def _ts(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=VIENNA)


def _nl_status(event: dict[str, Any]) -> ParcelStatus | None:
    status = str(event.get("status") or "").upper()
    if "PARCELSHOP" in status or "PICKUP" in status or "SERVICEPOINT" in status:
        return ParcelStatus.DELIVERED if "PICKED_UP" in status else ParcelStatus.AWAITING_PICKUP
    if "DELIVERED" in status:
        return ParcelStatus.DELIVERED
    if "OUT_FOR_DELIVERY" in status or "IN_DELIVERY" in status:
        return ParcelStatus.OUT_FOR_DELIVERY
    return _NL_CATEGORY.get(str(event.get("category") or "").upper())


def parse_dhl_nl(data: Any, number: str) -> TrackingResult:
    items = data if isinstance(data, list) else []
    shipment = next((s for s in items if isinstance(s, dict)), None)
    if shipment is None:
        raise NotFound(number)
    events_raw = [e for e in shipment.get("events") or [] if isinstance(e, dict)]
    scans = [(t, _nl_status(e), e) for e in events_raw if (t := _ts(e.get("timestamp")))]
    if not scans:
        raise NotFound(number)
    scans.sort(key=lambda s: s[0], reverse=True)
    status = next((s for _, s, _ in scans if s is not None), ParcelStatus.UNKNOWN)
    events: list[TrackingEvent] = []
    for t, s, e in scans:
        text = _NL_TEXT.get(s or ParcelStatus.UNKNOWN) or plain_text(str(e.get("status") or ""))
        # DHL sends one step as leg and piece event a second apart: shown once
        last = events[-1] if events else None
        if last and last.text == text and abs((last.timestamp - t).total_seconds()) < 600:
            continue
        events.append(TrackingEvent(timestamp=t, text=text, location=None))
    events = events[:MAX_EVENTS]
    frame = shipment.get("plannedDeliveryTimeframe") or {}
    start = _ts(frame.get("start")) if isinstance(frame, dict) else None
    end = _ts(frame.get("end")) if isinstance(frame, dict) else None
    open_parcel = status not in (ParcelStatus.DELIVERED, ParcelStatus.AWAITING_PICKUP)
    day: date | None = start.astimezone(VIENNA).date() if start and open_parcel else None
    return TrackingResult(
        status=status,
        status_text=_NL_TEXT.get(status),
        eta_date=day,
        eta_from=start if day and end else None,
        eta_to=end if day and start else None,
        location=None,
        pickup_point=None,
        pickup_until=None,
        delivered_at=next((t for t, s, _ in scans if s is ParcelStatus.DELIVERED), None)
        if status is ParcelStatus.DELIVERED else None,
        events=events,
    )


async def fetch_dhl_nl(session: aiohttp.ClientSession, number: str) -> TrackingResult:
    try:
        async with session.get(
            DHL_NL_URL, params={"key": number}, headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status == 404:
                raise NotFound(number)
            if resp.status != 200:
                raise CarrierUnavailable(f"DHL NL HTTP {resp.status}")
            try:
                data = await resp.json(content_type=None)
            except ValueError as err:
                raise ParseError("DHL NL answered no JSON") from err
    except (TimeoutError, aiohttp.ClientError) as err:
        raise CarrierUnavailable(str(err)) from err
    return parse_dhl_nl(data, number)


# ----- DHL Germany ------------------------------------------------------------------------
# "fortschritt" 0..5 of the tracking page's progress bar
_DE_PROGRESS = {
    0: ParcelStatus.PRE_TRANSIT,
    1: ParcelStatus.IN_TRANSIT,
    2: ParcelStatus.IN_TRANSIT,
    3: ParcelStatus.IN_TRANSIT,
    4: ParcelStatus.OUT_FOR_DELIVERY,
    5: ParcelStatus.DELIVERED,
}
_PICKUP_WORDS = ("filiale", "packstation", "paketshop", "zur abholung", "abholbereit")


def parse_dhl_de(data: Any, number: str) -> TrackingResult:
    shipments = data.get("sendungen") if isinstance(data, dict) else None
    shipment = next((s for s in shipments or [] if isinstance(s, dict)), None)
    if shipment is None:
        raise NotFound(number)
    details = shipment.get("sendungsdetails") or {}
    course = details.get("sendungsverlauf") or {}
    raw_events = [e for e in course.get("events") or [] if isinstance(e, dict)]
    events: list[TrackingEvent] = []
    for e in raw_events:
        if stamp := _ts(e.get("datum")):
            events.append(
                TrackingEvent(
                    timestamp=stamp,
                    text=plain_text(str(e.get("status") or "")).strip(),
                    location=(str(e["ort"]).strip() or None) if e.get("ort") else None,
                )
            )
    events.sort(key=lambda e: e.timestamp, reverse=True)
    progress = course.get("fortschritt")
    status = _DE_PROGRESS.get(progress, ParcelStatus.UNKNOWN) if isinstance(progress, int) else (
        ParcelStatus.UNKNOWN
    )
    if details.get("istZugestellt") is True:
        status = ParcelStatus.DELIVERED
    newest = (course.get("kurzStatus") or (events[0].text if events else "") or "").strip()
    if status is not ParcelStatus.DELIVERED and any(w in newest.lower() for w in _PICKUP_WORDS):
        status = ParcelStatus.AWAITING_PICKUP
    if status is ParcelStatus.PRE_TRANSIT and not events and not newest:
        raise NotFound(number)  # the page knows the number but nothing about it yet
    return TrackingResult(
        status=status,
        status_text=newest or None,
        eta_date=None,
        eta_from=None,
        eta_to=None,
        location=next((e.location for e in events if e.location), None),
        pickup_point=None,
        pickup_until=None,
        delivered_at=events[0].timestamp if status is ParcelStatus.DELIVERED and events else None,
        events=events[:MAX_EVENTS],
    )


async def fetch_dhl_de(number: str) -> TrackingResult:
    """One lookup in its own session (tracking page first for its cookies)."""
    try:
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(), headers={"User-Agent": _UA}, timeout=_TIMEOUT
        ) as session:
            async with session.get(DHL_DE_PAGE) as resp:
                if resp.status >= 400:
                    raise MissingCredentials(f"DHL page HTTP {resp.status} (bot protection?)")
                await resp.read()
            async with session.get(
                DHL_DE_URL,
                params={"piececode": number, "language": "de"},
                headers={"Accept": "application/json", "Referer": DHL_DE_PAGE},
            ) as resp:
                if resp.status != 200:
                    raise MissingCredentials(f"DHL tracking HTTP {resp.status} (bot protection?)")
                try:
                    data = await resp.json(content_type=None)
                except ValueError as err:
                    raise MissingCredentials("DHL tracking answered no JSON") from err
    except (TimeoutError, aiohttp.ClientError) as err:
        raise CarrierUnavailable(str(err)) from err
    return parse_dhl_de(data, number)
