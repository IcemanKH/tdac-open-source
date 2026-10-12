import os
import tempfile
import unittest

os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))

from tdac_fill import health_answer  # noqa: E402


class HealthGateTests(unittest.TestCase):
    def test_missing_key_returns_none(self):
        self.assertIsNone(health_answer({}, "yellowFeverVaccination"))
        self.assertIsNone(health_answer({}, "symptoms"))

    def test_empty_value_returns_none(self):
        self.assertIsNone(health_answer({"symptoms": ""}, "symptoms"))
        self.assertIsNone(health_answer({"yellowFeverVaccination": "   "}, "yellowFeverVaccination"))

    def test_present_values_are_normalized(self):
        self.assertEqual(health_answer({"symptoms": "no"}, "symptoms"), "NO")
        self.assertEqual(health_answer({"yellowFeverVaccination": "yes"}, "yellowFeverVaccination"), "YES")


if __name__ == "__main__":
    unittest.main()
