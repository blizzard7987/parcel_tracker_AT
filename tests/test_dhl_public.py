"""Paket Tracker AT: DHL without an API key (public tracking of DHL Parcel NL and dhl.de)."""

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.parcel_tracker.carriers.base import MissingCredentials, NotFound
from custom_components.parcel_tracker.carriers.dhl import DhlCarrier
from custom_components.parcel_tracker.carriers.dhl_public import (
    DHL_NL_URL,
    parse_dhl_de,
    parse_dhl_nl,
)
from custom_components.parcel_tracker.models import ParcelStatus

JVGL = "JVGL06009999000499999999"
# shape of the live answer of 2026-11-24 (number changed)
NL_ANNOUNCED = [{
    "barcode": JVGL, "barcodes": [JVGL], "type": "SHIPMENT", "totalEvents": 2,
    "events": [
        {"category": "DATA_RECEIVED", "status": "PRENOTIFICATION_RECEIVED",
         "timestamp": "2026-11-24T07:45:29.764Z", "type": "LEG_EVENT"},
        {"category": "DATA_RECEIVED", "status": "DATA_RECEIVED_WITH_PREFIX_LABEL",
         "timestamp": "2026-11-24T07:45:30Z", "type": "PIECE_EVENT"},
    ],
}]


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client:
        yield client


def test_nl_announced():
    r = parse_dhl_nl(NL_ANNOUNCED, JVGL)
    assert r.status is ParcelStatus.PRE_TRANSIT
    assert r.status_text == "Sendungsdaten an DHL übermittelt"
    assert len(r.events) == 1  # leg + piece event of one step


def test_nl_delivered_and_out_for_delivery():
    data = [{"events": [
        {"category": "IN_DELIVERY", "status": "OUT_FOR_DELIVERY",
         "timestamp": "2026-11-27T07:00:00Z"},
        {"category": "DELIVERED", "status": "DELIVERED", "timestamp": "2026-11-27T11:00:00Z"},
    ]}]
    r = parse_dhl_nl(data, JVGL)
    assert r.status is ParcelStatus.DELIVERED and r.delivered_at is not None
    r = parse_dhl_nl([{"events": data[0]["events"][:1]}], JVGL)
    assert r.status is ParcelStatus.OUT_FOR_DELIVERY


def test_nl_empty_is_not_found():
    with pytest.raises(NotFound):
        parse_dhl_nl([], JVGL)


def test_de_progress_and_pickup():
    data = {"sendungen": [{"sendungsdetails": {"istZugestellt": False, "sendungsverlauf": {
        "fortschritt": 4, "kurzStatus": "Die Sendung wurde in das Zustellfahrzeug geladen.",
        "events": [
            {"datum": "2026-11-27T07:12:00+01:00", "status": "In Zustellung", "ort": "Wien"},
        ],
    }}}]}
    r = parse_dhl_de(data, "00340434161094042557")
    assert r.status is ParcelStatus.OUT_FOR_DELIVERY and r.location == "Wien"
    data["sendungen"][0]["sendungsdetails"]["sendungsverlauf"]["kurzStatus"] = (
        "Die Sendung liegt in der Filiale zur Abholung bereit."
    )
    assert parse_dhl_de(data, "x").status is ParcelStatus.AWAITING_PICKUP
    data["sendungen"][0]["sendungsdetails"]["istZugestellt"] = True
    assert parse_dhl_de(data, "x").status is ParcelStatus.DELIVERED


def test_de_known_but_empty_is_not_found():
    course = {"fortschritt": 0, "events": []}
    data = {"sendungen": [{"sendungsdetails": {"sendungsverlauf": course}}]}
    with pytest.raises(NotFound):
        parse_dhl_de(data, "x")


async def test_keyless_carrier_asks_dhl_nl_for_jvgl(session):
    with aioresponses() as m:
        m.get(f"{DHL_NL_URL}?key={JVGL}", payload=NL_ANNOUNCED)
        r = await DhlCarrier(session, None).fetch(JVGL, None)
    assert r.status is ParcelStatus.PRE_TRANSIT


async def test_keyless_failure_stays_missing_key(session):
    with aioresponses() as m:
        m.get(f"{DHL_NL_URL}?key={JVGL}", status=503)
        with pytest.raises(MissingCredentials):
            await DhlCarrier(session, None).fetch(JVGL, None)


def test_nl_same_step_twice_is_shown_once():
    assert len(parse_dhl_nl(NL_ANNOUNCED, JVGL).events) == 1


async def test_fedex_has_no_live_tracking_and_says_so_quietly(session):
    from custom_components.parcel_tracker.carriers import build_carriers

    fedex = build_carriers(session, None, "at")["fedex"]
    with pytest.raises(MissingCredentials):
        await fedex.fetch("384599999465", None)
