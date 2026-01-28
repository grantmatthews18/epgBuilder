import pytest
from app.utils.event_processor.event_processor import convert_to_utc


def test_convert_no_tz_returns_normalized_values():
    date, start, end = convert_to_utc("2023-01-01", "13:05", None, tz_name=None)
    assert date == "2023-01-01"
    assert start == "13:05"
    assert end is None


def test_convert_utc_tz_identity():
    date, start, end = convert_to_utc("2023-01-01", "13:00", None, tz_name="UTC")
    assert date == "2023-01-01"
    assert start == "13:00"
    assert end is None


def test_convert_pst_to_utc_date_rollover():
    date, start, end = convert_to_utc("2023-01-01", "23:00", None, tz_name="America/Los_Angeles")
    assert date == "2023-01-02"  # 23:00 PST is 07:00 UTC next day
    assert start == "07:00"


def test_convert_end_time_cross_midnight():
    date, start, end = convert_to_utc("2023-01-01", "23:30", "00:30", tz_name="America/Los_Angeles")
    assert date == "2023-01-02"
    assert start == "07:30"
    assert end == "08:30"


def test_convert_end_time_same_day():
    date, start, end = convert_to_utc("2023-01-01", "10:00", "12:00", tz_name="America/Los_Angeles")
    assert date == "2023-01-01"
    assert start == "18:00"
    assert end == "20:00"
