import unittest

from tdac_fill import current_step_invalid, is_form_url, is_preview_url


class StepGateTests(unittest.TestCase):
    def test_hash_routed_form_url_is_recognized(self):
        self.assertTrue(is_form_url("https://tdac.immigration.go.th/arrival-card/#/tac/arrival-card/add"))

    def test_preview_route_is_recognized(self):
        self.assertTrue(is_preview_url("https://tdac.immigration.go.th/arrival-card/#/tac/arrival-card/preview"))
    def test_current_step_invalid_ignores_future_step_controls(self):
        status = {"invalid": [{"role": "tdac-arrDate", "step": 2}]}
        self.assertEqual(current_step_invalid(status, selected_step=1), [])

    def test_current_step_invalid_keeps_current_visible_control(self):
        status = {"invalid": [{"role": "tdac-passportNo", "step": 1, "visible": True}]}
        self.assertEqual(current_step_invalid(status, selected_step=1), status["invalid"])
