import unittest

from src.ai_results import normalize_ai_result


class AIResultCompatibilityTests(unittest.TestCase):
    def test_previous_session_text_is_displayable(self):
        result = normalize_ai_result("Shipment fields match.")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["summary"], "Shipment fields match.")

    def test_json_text_and_string_observations(self):
        result = normalize_ai_result('{"summary":"Matches", "observations":"Check weight"}')
        self.assertEqual(result["result"]["observations"], ["Check weight"])

    def test_malformed_completed_payload_does_not_crash(self):
        for payload in (None, [], 12, "Legacy summary"):
            result = normalize_ai_result({"status": "completed", "result": payload})
            self.assertIsInstance(result["result"], dict)

    def test_absent_results_are_skipped(self):
        for value in (None, "", [], 12):
            self.assertEqual(normalize_ai_result(value)["status"], "skipped")
