"""Paket Tracker AT: shipping mails of Austrian shops (fixtures after real mails, invented
numbers) and Post numbers in any mail."""

import pytest

from custom_components.parcel_tracker.mail import known_sender_domain, parse_mail
from custom_components.parcel_tracker.mail.at_shops import at_shop
from custom_components.parcel_tracker.mail.base import find_numbers
from custom_components.parcel_tracker.models import ParcelStatus

from .conftest import load_mail


def _updates(name):
    return [(u.number, u.carrier, u.status, u.title) for u in parse_mail(load_mail(name)).updates]


def test_shop_domains():
    assert at_shop("info@shop.dm.at") == "dm"
    assert at_shop("noreply@delivery.shop-apotheke.at") == "Shop Apotheke"
    assert at_shop("kontakt@apotheke.at") == "apotheke.at"
    assert at_shop("someone@example.at") is None
    assert known_sender_domain("info@shop.dm.at") == "dm.at"


def test_dm_names_every_post_parcel():
    assert _updates("310_dm_bestellung_unterwegs.eml") == [
        ("1099999999999990212301", "post_at", ParcelStatus.IN_TRANSIT, "dm"),
        ("1099999999999991212308", "post_at", ParcelStatus.IN_TRANSIT, "dm"),
    ]


def test_post_number_from_a_tracking_link():
    assert _updates("311_shop_apotheke_abholbereit.eml") == [
        ("1099999999999992212305", "post_at", ParcelStatus.AWAITING_PICKUP, "Shop Apotheke"),
    ]
    assert _updates("314_asambeauty_unterwegs.eml") == [
        ("1099999999999993112309", "post_at", ParcelStatus.IN_TRANSIT, "asambeauty"),
    ]


def test_dpd_numbers_behind_a_label():
    assert _updates("312_aboutyou_sendungsverfolgung.eml") == [
        ("09999999999921", "dpd", None, "ABOUT YOU"),
    ]
    assert _updates("313_apotheke_at_auf_dem_weg.eml") == [
        ("09999999999922", "dpd", ParcelStatus.IN_TRANSIT, "apotheke.at"),
    ]


def test_dhl_number_of_a_shop_shipping_from_germany():
    assert _updates("315_pharmeo_versandbestaetigung_dhl.eml") == [
        ("CR999999999DE", "dhl", ParcelStatus.IN_TRANSIT, "Pharmeo"),
    ]


def test_returns_are_ignored():
    result = parse_mail(load_mail("316_aboutyou_retoure.eml"))
    assert result.ignored and not result.updates


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (
            "https://www.post.at/s/sendungsdetails?snr=1099999999999994212301",
            "1099999999999994212301",
        ),
        ("Paketnummer: 158999999999999999901230", "158999999999999999901230"),
        ("Ihre Sendung RR123456789AT ist unterwegs", "RR123456789AT"),
    ],
)
def test_post_numbers_anywhere(text, found):
    assert find_numbers(text) == [("post_at", found)]


def test_unlabelled_long_numbers_and_returns_say_nothing():
    assert find_numbers("Bestellnummer 1099999999999994212301") == []
    assert find_numbers("Retouren-Trackingnummer: 1099999999999994212301") == []
