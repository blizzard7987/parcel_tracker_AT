"""Paket Tracker AT: Amazon sends no "delivered" mail in Austria."""

from datetime import timedelta

from custom_components.parcel_tracker.carriers.base import BERLIN
from custom_components.parcel_tracker.models import ParcelStatus
from custom_components.parcel_tracker.schedule import order_overdue

from .test_order_closing import NOW, TODAY, _order


def _out_for_delivery(day, mailed=None):
    order = _order(first=day, status=ParcelStatus.OUT_FOR_DELIVERY, changed=NOW)
    order.last_mail_at = mailed
    return order


def test_out_for_delivery_amazon_closes_the_day_after_in_austria():
    today_box = _out_for_delivery(TODAY)
    assert not order_overdue(today_box, TODAY, BERLIN, no_delivery_mail=True)
    assert order_overdue(today_box, TODAY + timedelta(days=1), BERLIN, no_delivery_mail=True)
    # without the Austrian rule it waits the usual three days
    assert not order_overdue(today_box, TODAY + timedelta(days=1), BERLIN)


def test_stock_taking_counts_from_the_mail_not_from_reading_it():
    # read today (changed=NOW), but the mail and its day are ten days old
    old = _out_for_delivery(None, mailed=NOW - timedelta(days=10))
    assert order_overdue(old, TODAY, BERLIN, no_delivery_mail=True)


def test_only_amazon_boxes_out_for_delivery():
    in_transit = _order(first=TODAY - timedelta(days=1), status=ParcelStatus.IN_TRANSIT)
    assert not order_overdue(in_transit, TODAY, BERLIN, no_delivery_mail=True)
    ebay = _order(
        number="EBAY999999999901", carrier="ebay", first=TODAY - timedelta(days=1),
        status=ParcelStatus.OUT_FOR_DELIVERY,
    )
    assert not order_overdue(ebay, TODAY, BERLIN, no_delivery_mail=True)
