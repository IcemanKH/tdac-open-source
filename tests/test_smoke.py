import datetime as dt
import unittest

from tdac_fill import date_keystrokes, smoke_trip_dates


class SmokeDateTests(unittest.TestCase):
    def test_smoke_dates_use_today_and_seven_days_later(self):
        self.assertEqual(smoke_trip_dates(dt.date(2026, 10, 10)), ("2026/10/10", "2026/10/17"))

    def test_date_mask_receives_digits_without_separator(self):
        self.assertEqual(date_keystrokes("2026/10/10"), "20261010")
