import base64
import csv
import os
import tempfile
import unittest

from tdac_fill import (
    extract_preview_fields,
    export_preview_pdf,
    required_fields_rows,
    write_required_fields_csv,
)


class _Body:
    def __init__(self, text):
        self.text = text

    def inner_text(self):
        return self.text


class _Page:
    def __init__(self, text):
        self.body = _Body(text)

    def locator(self, selector, **kwargs):
        self.last_selector = selector
        return self.body


class _CdpSession:
    def send(self, method, params):
        self.method = method
        self.params = params
        return {"data": base64.b64encode(b"%PDF-1.7 fake preview").decode("ascii")}


class _Context:
    def __init__(self):
        self.session = _CdpSession()

    def new_cdp_session(self, page):
        return self.session


class _PdfPage:
    def __init__(self):
        self.context = _Context()
        self.waited = False
        self.evaluations = []

    def wait_for_timeout(self, timeout_ms):
        self.waited = self.waited or timeout_ms >= 500

    def wait_for_function(self, expression, timeout=0):
        self.waited = self.waited and timeout >= 1000

    def evaluate(self, script):
        self.evaluations.append(script)


class PreviewMappingTests(unittest.TestCase):
    def test_maps_repeated_arrival_and_departure_fields(self):
        page = _Page("""
            Date of Arrival :
            2026/10/10
            Mode of Travel :
            AIR
            Mode of Transport :
            COMMERCIAL FLIGHT
            Flight No./Vehicle No. :
            MU0000
            Date of Departure :
            2026/10/17
            Mode of Travel :
            AIR
            Mode of Transport :
            COMMERCIAL FLIGHT
            Flight No./Vehicle No. :
            MU0001
            Post Code :
            00000
        """)
        fields = extract_preview_fields(page)
        self.assertEqual(fields["arrivalDate"], "2026/10/10")
        self.assertEqual(fields["departureDate"], "2026/10/17")
        self.assertEqual(fields["arrivalFlightNo"], "MU0000")
        self.assertEqual(fields["departureFlightNo"], "MU0001")
        self.assertEqual(fields["arrivalFlightType"], "COMMERCIAL FLIGHT")
        self.assertEqual(fields["departureFlightType"], "COMMERCIAL FLIGHT")

    def test_export_preview_pdf_writes_printed_page(self):
        with tempfile.TemporaryDirectory() as folder:
            page = _PdfPage()
            path = export_preview_pdf(page, folder, stamp="20261010-123456")
            self.assertTrue(path.endswith("TDAC_预览_20261010-123456.pdf"))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"%PDF-1.7 fake preview")
            self.assertTrue(page.waited)
            self.assertGreaterEqual(len(page.evaluations), 2)

    def test_required_fields_csv_contains_manual_and_health_sections(self):
        rows = required_fields_rows()
        keys = {row["字段键"] for row in rows}
        self.assertIn("passportNo", keys)
        self.assertIn("last14DaysCountries", keys)
        self.assertIn("termsAcceptedByUser", keys)
        self.assertTrue(any(row["填写方式"] == "本人手动" for row in rows))

        with tempfile.TemporaryDirectory() as folder:
            path = write_required_fields_csv(folder, stamp="20261010-123456")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                csv_rows = list(csv.DictReader(fh))
            self.assertEqual(len(csv_rows), len(rows))
            self.assertEqual(csv_rows[0]["分区"], "个人信息")


if __name__ == "__main__":
    unittest.main()
