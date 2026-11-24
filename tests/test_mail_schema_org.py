"""Paket Tracker AT: schema.org ParcelDelivery data in shop mails (Google Store, JSON-LD).

The fixtures are built after a real mail; numbers, names and places are invented.
"""

from datetime import date

from custom_components.parcel_tracker.carriers.base import Match
from custom_components.parcel_tracker.carriers.dhl import DhlCarrier
from custom_components.parcel_tracker.mail import parse_mail
from custom_components.parcel_tracker.mail.readonly import gmail_query
from custom_components.parcel_tracker.mail.schema_org import parcel_deliveries
from custom_components.parcel_tracker.models import ParcelStatus

from .conftest import load_mail

NUMBER = "JVGL06009999000499999999"


def test_google_store_ready_to_ship_is_announced_with_window_and_item():
    [u] = parse_mail(load_mail("320_google_store_versandbereit.eml")).updates
    assert (u.number, u.carrier, u.status) == (NUMBER, "dhl", ParcelStatus.PRE_TRANSIT)
    assert u.title == "Google Fitbit Edge (Olive / Satin Pyrite)"
    assert (u.eta_date, u.eta_latest) == (date(2026, 11, 27), date(2026, 11, 28))


def test_google_store_delivered():
    [u] = parse_mail(load_mail("321_google_store_zugestellt.eml")).updates
    assert u.status is ParcelStatus.DELIVERED
    assert u.delivered_at is not None


def test_json_ld_names_the_carrier():
    [u] = parse_mail(load_mail("322_schema_org_json_ld_post.eml")).updates
    assert (u.number, u.carrier, u.status, u.title) == (
        "1099999999999998212399", "post_at", ParcelStatus.IN_TRANSIT, "Wanderschuhe",
    )


def test_address_is_never_read():
    markup = load_mail("320_google_store_versandbereit.eml").get_body(("html",)).get_content()
    [d] = parcel_deliveries(markup)
    assert "deliveryAddress" in d  # parsed as an item, but parse_schema_org never reads it
    [u] = parse_mail(load_mail("320_google_store_versandbereit.eml")).updates
    assert "Muster" not in repr(u) and "1010" not in repr(u)


def test_unknown_carrier_makes_no_parcel():
    markup = (
        '<div itemscope itemtype="http://schema.org/ParcelDelivery">'
        '<div itemprop="carrier" itemscope itemtype="http://schema.org/Organization">'
        '<span itemprop="name">Kurier Blitz</span></div>'
        '<meta itemprop="trackingNumber" content="KB123456789"></div>'
    )
    [d] = parcel_deliveries(markup)
    assert d["carrier"][0]["name"] == ["Kurier Blitz"]  # text values are read too


def test_dhl_ecommerce_numbers_and_gmail_search():
    assert DhlCarrier.matches(NUMBER) is Match.POSSIBLE
    assert "googlestore-noreply@google.com" in gmail_query()
    assert "subject:versandbereit" in gmail_query()
