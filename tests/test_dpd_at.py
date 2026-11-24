"""Paket Tracker AT: DPD Austria via the public search of mydpd.at.

The answers follow two live lookups of 2026-11-24 (lifecycle only; numbers invented).
"""

from datetime import datetime

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.parcel_tracker.carriers import build_carriers
from custom_components.parcel_tracker.carriers.base import CarrierUnavailable, NotFound, ParseError
from custom_components.parcel_tracker.carriers.dpd import (
    DPD_ENTRY_URL,
    DpdCarrier,
    _ForwardedToAustria,
    forwarded_to_austria,
)
from custom_components.parcel_tracker.carriers.dpd_at import (
    DPD_AT_BASE,
    DPD_AT_SEARCH_URL,
    VIENNA,
    parse_dpd_at,
)
from custom_components.parcel_tracker.models import ParcelStatus

NUMBER = "09999999999911"
DE_URL = f"{DPD_ENTRY_URL}?action=12&parcelno={NUMBER}"
FORWARD_URL = f"https://my.dpd.de/myparcels/forwarding.aspx?parcelno={NUMBER}&alpha3=AUT"


def _entry(stamp, value, name, text, depot=None, kind=None):
    return {
        "depot": "0621",
        "depotData": depot,
        "datetime": stamp,
        "type": kind,
        "state": {"value": value, "name": name, "text": text, "datetime": stamp},
        "extendedOnly": False,
    }


def _parcel(*entries, info="Paket unterwegs", predict=None):
    return {
        "parcelno": NUMBER,
        "sender_name1": "never read",
        "consignee_name1": "never read",
        "lifecycle": {
            "state_info": info,
            "predict": predict,
            "entries": list(entries),
        },
    }


DELIVERED = _parcel(
    _entry("20261030093500", 0, "ORDER_DATA", "Auftragsdaten übermittelt"),
    _entry("20261030182500", 2, "ON_THE_WAY", "Paket unterwegs", ["Leopoldsdorf", "AT"], "05"),
    _entry("20261102054600", 3, "IN_DEPOT", "Im Paketzustellzentrum", ["Leopoldsdorf", "AT"]),
    _entry("20261102080700", 4, "IN_DELIVERY", "In Zustellung"),
    _entry("20261102144700", 5, "DELIVERED", "Erfolgreich zugestellt"),
    info="Erfolgreich zugestellt",
)
ANNOUNCED = _parcel(
    _entry("20261123190200", 0, "ORDER_DATA", "Auftragsdaten übermittelt"),
    info="Auftragsdaten übermittelt",
)


def test_delivered():
    r = parse_dpd_at(DELIVERED)
    assert r.status is ParcelStatus.DELIVERED
    assert r.status_text == "Erfolgreich zugestellt"
    assert r.delivered_at == datetime(2026, 11, 2, 14, 47, tzinfo=VIENNA)
    assert [e.text for e in r.events][:2] == ["Erfolgreich zugestellt", "In Zustellung"]
    assert r.location == "Leopoldsdorf"
    assert "never read" not in repr(r)


def test_announced_and_in_delivery_with_forecast():
    assert parse_dpd_at(ANNOUNCED).status is ParcelStatus.PRE_TRANSIT
    r = parse_dpd_at(
        _parcel(
            _entry("20261124080700", 4, "IN_DELIVERY", "In Zustellung"),
            predict={"date": "2026-11-24", "from": "10:30", "to": "14:30"},
        )
    )
    assert r.status is ParcelStatus.OUT_FOR_DELIVERY
    assert r.eta_from == datetime(2026, 11, 24, 10, 30, tzinfo=VIENNA)
    assert r.eta_to == datetime(2026, 11, 24, 14, 30, tzinfo=VIENNA)


def test_shop_and_unknown_names():
    shop = parse_dpd_at(_parcel(_entry("20261124120000", 6, "IN_PICKUP_SHOP", "Im Paketshop")))
    assert shop.status is ParcelStatus.AWAITING_PICKUP
    odd = parse_dpd_at(_parcel(_entry("20261124120000", 3, "SOMETHING_NEW", "Neu")))
    assert odd.status is ParcelStatus.AT_DELIVERY_DEPOT  # by its value


def test_no_scans_and_bad_shapes():
    with pytest.raises(NotFound):
        parse_dpd_at(_parcel())
    with pytest.raises(ParseError):
        parse_dpd_at({"lifecycle": None})


def _mock_at(m, status=200, payload=None, **kwargs):
    m.get(f"{DPD_AT_BASE}/", status=200, body="<html></html>")
    m.post(DPD_AT_SEARCH_URL, status=status, payload=payload, **kwargs)


async def test_austria_first_with_country_at():
    with aioresponses() as m:
        _mock_at(m, payload={"state": "success", "data": [DELIVERED]})
        r = await DpdCarrier("at").fetch(NUMBER, "1230")
        [call] = m.requests[("POST", aiohttp.client.URL(DPD_AT_SEARCH_URL))]
    assert r.status is ParcelStatus.DELIVERED
    assert call.kwargs["json"] == [NUMBER]  # only the number


async def test_german_parcel_falls_back_to_the_german_page():
    with aioresponses() as m:
        _mock_at(m, payload={"state": "success", "data": []})
        m.get(DE_URL, status=200, body="<html>kein Paket</html>")
        with pytest.raises(NotFound):
            await DpdCarrier("at").fetch(NUMBER, None)


async def test_german_page_forwarding_to_austria_asks_mydpd_at(monkeypatch):
    assert forwarded_to_austria(FORWARD_URL)
    assert not forwarded_to_austria(DE_URL)

    async def forwarded(self, number):
        raise _ForwardedToAustria(number)

    monkeypatch.setattr(DpdCarrier, "_fetch_de", forwarded)
    with aioresponses() as m:
        _mock_at(m, payload={"state": "success", "data": [ANNOUNCED]})
        r = await DpdCarrier().fetch(NUMBER, None)
    assert r.status is ParcelStatus.PRE_TRANSIT


async def test_bot_protection_is_unavailable_not_unknown():
    for kwargs in ({"status": 403}, {"status": 200, "body": "<html>challenge</html>"}):
        with aioresponses() as m:
            _mock_at(m, **kwargs)
            with pytest.raises(CarrierUnavailable):
                await DpdCarrier("at").fetch(NUMBER, None)
    with aioresponses() as m:
        _mock_at(m, payload={"state": "failure"})
        with pytest.raises(CarrierUnavailable):
            await DpdCarrier("at").fetch(NUMBER, None)


def test_country_reaches_the_carrier():
    async def run():
        async with aiohttp.ClientSession() as session:
            return build_carriers(session, None, "at")["dpd"]._country

    import asyncio

    assert asyncio.run(run()) == "at"
