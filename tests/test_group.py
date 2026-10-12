import unittest

from tdac_group import GroupConfig, Traveler, compare_preview, preview_report, should_use_previous_trip, validate_group
from tdac_sec import validate_group_secret_records


def traveler(identifier: str, **overrides) -> Traveler:
    data = dict(
        id=identifier, familyName="TEST", firstName="USER", middleName="",
        nationality="CHINESE", dateOfBirth="1990/01/01", gender="MALE",
        occupation="EMPLOYEE", countryOfResidence="CHINA", cityOfResidence="TESTCITY",
        phoneCode="86", visaNo="", passportNo="E12345678", phoneNo="13000000000",
        sharePreviousTravel=False,
    )
    data.update(overrides)
    return Traveler(**data)


class GroupValidationTests(unittest.TestCase):
    def test_validate_group_rejects_more_than_ten_travelers(self):
        group = GroupConfig([traveler(str(i)) for i in range(11)])
        self.assertEqual(validate_group(group), ["团体最多 10 名旅客，当前为 11 名"])

    def test_validate_group_reports_missing_phone_for_specific_traveler(self):
        group = GroupConfig([traveler("traveler-01", phoneNo="")])
        self.assertEqual(validate_group(group), ["traveler-01: 缺少敏感资料 phoneNo"])

    def test_duplicate_names_do_not_mix_secrets(self):
        group = GroupConfig([traveler("a", passportNo="E11111111"), traveler("b", passportNo="E22222222")])
        self.assertEqual(validate_group(group), [])
        self.assertNotEqual(group.travelers[0].passportNo, group.travelers[1].passportNo)

    def test_share_previous_is_only_allowed_after_first_traveler(self):
        self.assertFalse(should_use_previous_trip(0, True, True))
        self.assertFalse(should_use_previous_trip(1, True, False))
        self.assertTrue(should_use_previous_trip(1, True, True))


class PreviewTests(unittest.TestCase):
    def test_preview_comparison_reports_wrong_flight_without_sensitive_values(self):
        self.assertEqual(compare_preview({"arrivalFlightNo": "MU0000"}, {"arrivalFlightNo": "MU9999"}), ["入境航班号不一致"])

    def test_preview_report_excludes_sensitive_values(self):
        report = preview_report(traveler("t1", passportNo="E12345678", phoneNo="13000000000"), {"arrivalFlightNo": "MU9999"})
        self.assertIn("MU9999", report)
        self.assertNotIn("E12345678", report)
        self.assertNotIn("13000000000", report)


class SecretShapeTests(unittest.TestCase):
    def test_group_secret_rejects_fields_other_than_passport_and_phone(self):
        with self.assertRaises(ValueError):
            validate_group_secret_records({"traveler-01": {"passportNo": "E12345678", "email": "secret@example.com"}})
