"""The mailbox is checked at most hourly from 22 to 6, right again at 6."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.parcel_tracker.schedule import mail_delay

VIENNA = ZoneInfo("Europe/Vienna")
TEN = timedelta(minutes=10)


def _at(hour: int, minute: int = 0, day: int = 24) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=VIENNA)


@pytest.mark.parametrize("hour", [6, 12, 21])
def test_day_uses_the_set_interval(hour):
    assert mail_delay(TEN, _at(hour)) == TEN


@pytest.mark.parametrize(("hour", "minute"), [(22, 0), (23, 30), (0, 0), (3, 15)])
def test_night_is_hourly(hour, minute):
    assert mail_delay(TEN, _at(hour, minute)) == timedelta(hours=1)


def test_first_check_of_the_day_comes_at_six():
    assert mail_delay(TEN, _at(5, 30)) == timedelta(minutes=30)
    assert mail_delay(TEN, _at(5, 55)) == TEN  # never shorter than the set interval


def test_longer_interval_wins():
    assert mail_delay(timedelta(hours=3), _at(23)) == timedelta(hours=3)


def test_utc_input():
    now = _at(23).astimezone(ZoneInfo("UTC"))
    assert mail_delay(TEN, now) == timedelta(hours=1)
