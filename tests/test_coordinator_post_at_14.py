"""Paket Tracker AT: a 14-digit number DPD does not know may be one of Österreichische Post."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parcel_tracker.carriers.base import CarrierUnavailable, NotFound
from custom_components.parcel_tracker.const import DOMAIN
from custom_components.parcel_tracker.coordinator import ParcelCoordinator
from custom_components.parcel_tracker.models import ParcelStatus
from custom_components.parcel_tracker.store import ParcelStore

from .test_coordinator import FakeCarrier, result

NUMBER = "62316185337214"


class FakeDpd(FakeCarrier):
    key = "dpd"
    name = "DPD"


class FakePost(FakeCarrier):
    key = "post_at"
    name = "Österreichische Post"


async def _coordinator(hass, country):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"postcode": "1230", "country": country}, options={}
    )
    entry.add_to_hass(hass)
    store = ParcelStore(hass)
    await store.async_load()
    dpd, post = FakeDpd(), FakePost()
    return ParcelCoordinator(hass, entry, store, {"dpd": dpd, "post_at": post}), dpd, post


@pytest.mark.parametrize("dpd_error", [NotFound("x"), CarrierUnavailable("x")])
async def test_dpd_parcel_unknown_to_dpd_becomes_a_post_parcel(hass, dpd_error):
    coord, dpd, post = await _coordinator(hass, "at")
    dpd.answers = [dpd_error]
    post.answers = [result(ParcelStatus.DELIVERED)]
    parcel = await coord.async_add(NUMBER, "dpd", "apotheke.at")
    assert parcel.carrier == "post_at"
    assert parcel.status is ParcelStatus.DELIVERED
    assert parcel.last_error is None


async def test_post_unknown_too_keeps_dpd(hass):
    coord, dpd, post = await _coordinator(hass, "at")
    dpd.answers = [NotFound("x")]
    post.answers = [NotFound("x")]
    parcel = await coord.async_add(NUMBER, "dpd", None)
    assert parcel.carrier == "dpd"
    assert parcel.last_error == "not_found"


async def test_dpd_answer_wins_and_post_is_not_asked(hass):
    coord, dpd, post = await _coordinator(hass, "at")
    dpd.answers = [result(ParcelStatus.IN_TRANSIT)]
    post.answers = [result(ParcelStatus.DELIVERED)]
    parcel = await coord.async_add(NUMBER, "dpd", None)
    assert parcel.carrier == "dpd"
    assert post.calls == 0


async def test_only_in_austria(hass):
    coord, dpd, post = await _coordinator(hass, "de")
    dpd.answers = [NotFound("x")]
    post.answers = [result(ParcelStatus.DELIVERED)]
    parcel = await coord.async_add(NUMBER, "dpd", None)
    assert parcel.carrier == "dpd"
    assert post.calls == 0
