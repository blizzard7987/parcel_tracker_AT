"""DPD Austria via the public parcel search of mydpd.at (tracking number only). Paket Tracker AT.

my.dpd.de only forwards an Austrian parcel to mydpd.at ("forwarding.aspx?…&alpha3=AUT"), so
the German page knows nothing about it. mydpd.at answers its own search without an account:
``POST jws.php/parcel/search`` with the number as the only positional argument returns the
parcel's ``lifecycle`` (status and scans). Seen live on 2026-11-24 in a browser.

The site sits behind a bot protection: the start page is loaded first in the same session
(it sets the cookies the portal needs, as ha-parcel-integrations/ha-dpd does for its login).
A refusal (403, 429, a page instead of JSON) is "unavailable", never "not found": the mails
of DPD Austria still tell the status.

Of the answer only the lifecycle is read. Sender and recipient fields are never touched.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from ..const import MAX_EVENTS
from ..models import ParcelStatus, TrackingEvent, TrackingResult
from .base import CarrierUnavailable, NotFound, ParseError, plain_text

VIENNA = ZoneInfo("Europe/Vienna")
DPD_AT_BASE = "https://www.mydpd.at"
DPD_AT_SEARCH_URL = f"{DPD_AT_BASE}/jws.php/parcel/search"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(total=25)
_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json",
    "Origin": DPD_AT_BASE,
    "Referer": f"{DPD_AT_BASE}/",
    "X-Requested-With": "XMLHttpRequest",
}

# lifecycle state names (value 0..5 seen live: ORDER_DATA, ON_THE_WAY, IN_DEPOT, IN_DELIVERY,
# DELIVERED). Names with SHOP/PICKUP mean the parcel waits to be picked up.
_STATUS = {
    "ORDER_DATA": ParcelStatus.PRE_TRANSIT,
    "PICKED_UP": ParcelStatus.IN_TRANSIT,
    "ON_THE_WAY": ParcelStatus.IN_TRANSIT,
    "IN_DEPOT": ParcelStatus.AT_DELIVERY_DEPOT,
    "IN_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
}
_BY_VALUE = {
    0: ParcelStatus.PRE_TRANSIT,
    1: ParcelStatus.IN_TRANSIT,
    2: ParcelStatus.IN_TRANSIT,
    3: ParcelStatus.AT_DELIVERY_DEPOT,
    4: ParcelStatus.OUT_FOR_DELIVERY,
    5: ParcelStatus.DELIVERED,
}


def _stamp(value: Any) -> datetime | None:
    """'20261102144700' (Austrian local time)."""
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(tzinfo=VIENNA)
    except (TypeError, ValueError):
        return None


# "Paket durch Empfänger abgeholt": collected from a Pickup shop or station - delivered,
# although the state name still carries PICKUP.
_COLLECTED = re.compile(r"(?:durch|vom|von)\s+(?:dem\s+)?Empf[aä]nger\s+abgeholt", re.I)


def _entry_status(state: dict[str, Any]) -> ParcelStatus | None:
    name = str(state.get("name") or "").upper()
    if name in _STATUS:
        return _STATUS[name]
    if _COLLECTED.search(str(state.get("text") or "")) or "COLLECTED" in name:
        return ParcelStatus.DELIVERED
    if "SHOP" in name or "PICKUP" in name:
        return ParcelStatus.AWAITING_PICKUP
    value = state.get("value")
    return _BY_VALUE.get(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _predict(predict: Any) -> tuple[date | None, datetime | None, datetime | None]:
    """A delivery forecast, if the answer carries one ({"date", "from", "to"} assumed)."""
    if not isinstance(predict, dict):
        return None, None, None
    try:
        day = date.fromisoformat(str(predict.get("date"))[:10])
    except (TypeError, ValueError):
        return None, None, None

    def clock(key: str) -> datetime | None:
        try:
            return datetime.combine(day, time.fromisoformat(str(predict.get(key))[:5]), VIENNA)
        except (TypeError, ValueError):
            return None

    start, end = clock("from"), clock("to")
    if start and end and start >= end:
        start = end = None
    return day, start, end


def parse_dpd_at(parcel: dict[str, Any]) -> TrackingResult:
    """Normalise one parcel of the search answer."""
    lifecycle = parcel.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise ParseError("DPD AT: no lifecycle")
    entries = lifecycle.get("entries") or []
    if not isinstance(entries, list):
        raise ParseError("DPD AT: entries is no list")
    scans: list[tuple[datetime, ParcelStatus | None, str, str | None]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("state"), dict):
            continue
        state = entry["state"]
        stamp = _stamp(state.get("datetime") or entry.get("datetime"))
        if stamp is None:
            continue
        depot = entry.get("depotData")
        place = str(depot[0]) if isinstance(depot, list) and depot and depot[0] else None
        text = plain_text(str(state.get("text") or state.get("name") or "")).strip()
        scans.append((stamp, _entry_status(state), text, place))
    if not scans:
        raise NotFound("DPD AT knows no scans of this parcel")
    scans.sort(key=lambda scan: scan[0], reverse=True)
    status = next((s for _, s, _, _ in scans if s is not None), ParcelStatus.UNKNOWN)
    day, start, end = _predict(lifecycle.get("predict"))
    open_parcel = status not in (ParcelStatus.DELIVERED, ParcelStatus.AWAITING_PICKUP)
    events = [TrackingEvent(timestamp=t, text=x, location=p) for t, _, x, p in scans][:MAX_EVENTS]
    info = lifecycle.get("state_info")
    return TrackingResult(
        status=status,
        status_text=plain_text(info).strip() or None if isinstance(info, str) else None,
        eta_date=day if open_parcel else None,
        eta_from=start if open_parcel else None,
        eta_to=end if open_parcel else None,
        location=next((p for _, _, _, p in scans if p), None),
        pickup_point=None,
        pickup_until=None,
        delivered_at=next(
            (t for t, s, _, _ in scans if s is ParcelStatus.DELIVERED), None
        ) if status is ParcelStatus.DELIVERED else None,
        events=events,
    )


# Paket Tracker AT, test (2026-11-26): does mydpd.at answer a plain, honest request - our
# own User-Agent, no start page, no browser headers? Tried first; only a refusal falls back
# to the browser-like way below. The counts go into the diagnostics ("dpd_at_probe"): if
# the honest way works, the fallback goes and the honest way is offered to the original.
_HONEST_HEADERS = {
    "User-Agent": "ParcelTracker (Home Assistant integration)",
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json",
}
PROBE: dict[str, Any] = {
    "honest_ok": 0,
    "honest_refused": 0,
    "fallback_ok": 0,
    "last_refusal": None,
    "last_honest_at": None,
}


def _answer(body: Any) -> bool:
    """A well-formed search answer (parcel found or not)."""
    return isinstance(body, dict) and body.get("state") in ("success", "failure")


async def _honest_search(number: str) -> Any:
    """The search as a plain client; None if mydpd.at refuses it."""
    PROBE["last_honest_at"] = datetime.now(VIENNA).isoformat(timespec="seconds")
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(
                DPD_AT_SEARCH_URL, json=[number], headers=_HONEST_HEADERS
            ) as resp:
                if resp.status != 200:
                    PROBE["last_refusal"] = f"HTTP {resp.status}"
                    return None
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    PROBE["last_refusal"] = "no JSON (page instead)"
                    return None
    except (TimeoutError, aiohttp.ClientError) as err:
        PROBE["last_refusal"] = type(err).__name__
        return None
    if not _answer(body):
        PROBE["last_refusal"] = "unexpected answer"
        return None
    return body


async def _browser_search(number: str) -> Any:
    """The search with the start page first for the portal's cookies (the old way)."""
    try:
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(), headers={"User-Agent": _UA}, timeout=_TIMEOUT
        ) as session:
            async with session.get(f"{DPD_AT_BASE}/") as resp:
                if resp.status >= 400:
                    raise CarrierUnavailable(f"DPD AT start page HTTP {resp.status}")
                await resp.read()
            async with session.post(DPD_AT_SEARCH_URL, json=[number], headers=_HEADERS) as resp:
                if resp.status != 200:
                    raise CarrierUnavailable(f"DPD AT HTTP {resp.status}")
                try:
                    return await resp.json(content_type=None)
                except ValueError as err:
                    raise CarrierUnavailable("DPD AT answered no JSON (bot protection?)") from err
    except (TimeoutError, aiohttp.ClientError) as err:
        raise CarrierUnavailable(str(err)) from err


async def fetch_dpd_at(number: str) -> TrackingResult:
    """One lookup: the honest request first, the browser-like way only if it is refused."""
    body = await _honest_search(number)
    if body is not None:
        PROBE["honest_ok"] += 1
    else:
        PROBE["honest_refused"] += 1
        body = await _browser_search(number)
        if _answer(body):
            PROBE["fallback_ok"] += 1
    if isinstance(body, dict) and body.get("state") == "failure" and not body.get("data"):
        # A well-formed answer without the parcel: DPD Austria does not know the number
        # (seen live 2026-11-24 for a parcel of Österreichische Post with 14 digits).
        raise NotFound(number)
    if not isinstance(body, dict) or body.get("state") != "success":
        raise CarrierUnavailable("DPD AT refused the search")
    data = body.get("data")
    parcel = next(
        (p for p in data or [] if isinstance(p, dict) and p.get("parcelno") == number), None
    ) if isinstance(data, list) else None
    if parcel is None:
        raise NotFound(number)
    return parse_dpd_at(parcel)
