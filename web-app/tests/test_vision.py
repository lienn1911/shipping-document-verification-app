"""Scanned PDFs are read with Gemini vision when it is ready, and go to review otherwise. No network."""

import hashlib
import logging
import os
from pathlib import Path
import sys
import types as pytypes
import unittest
from unittest import mock

logging.getLogger("pypdf").setLevel(logging.ERROR)
WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))
os.environ.setdefault("FIREBASE_ENABLED", "false")

import ai_service  # noqa: E402
from src.document_readers import _transcription_rows, read_document_ex  # noqa: E402
from src.extract import ALIAS_TO_FIELD, ReviewRequired, _normalize_label, extract_attachment_with_evidence  # noqa: E402
from src.service import process_dataset  # noqa: E402

BUNDLE = WEB_APP.parent / "local-data" / "participant-bundle"
GOOD = (
    "=== PAGE 1 ===\nSHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\nConsignee: AL GURG STATIONERY LLC\n"
    "Notify: AL GURG STATIONERY LLC\nPort of Loading: NHAVA SHEVA INDIA\nPort of Discharge: TUTICORIN, INDIA\n"
    "Containers: 6 x 40'HC\nGross Weight: 128,544 KG\nVessel: NAP914V.BS007\nBooking: 070500208599\n"
)
OVERLOAD = Exception("503 UNAVAILABLE. This model is currently experiencing high demand.")
DENIED = Exception("403 PERMISSION_DENIED. API_KEY_INVALID")


class FakeClient:
    plan: dict = {}
    calls: list = []

    def __init__(self, api_key=None, http_options=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def models(self):
        return self

    def generate_content(self, model, contents, config=None):
        FakeClient.calls.append(model)
        outcome = FakeClient.plan.get(model, GOOD)
        if isinstance(outcome, Exception):
            raise outcome
        return pytypes.SimpleNamespace(text=outcome)


class FakeGeminiMixin:
    """A fake Gemini client, clean caches and a known configuration for every test."""

    ENV = {"GEMINI_AI_ENABLED": "true", "GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "main-model",
           "GEMINI_FALLBACK_MODELS": "backup-a", "GEMINI_VISION_ENABLED": "true"}

    def setUp(self):
        FakeClient.plan, FakeClient.calls = {}, []
        ai_service._vision_cache.clear()
        ai_service._vision_failures.clear()
        ai_service._vision_cooldown[:] = [0.0, ""]
        for p in (mock.patch.dict(os.environ, self.ENV), mock.patch("env_loader.load_environment"),
                  mock.patch("google.genai.Client", FakeClient)):
            p.start()
            self.addCleanup(p.stop)


class TranscribeTests(FakeGeminiMixin, unittest.TestCase):
    def test_success_and_cache(self):
        first = ai_service.transcribe_scanned_pdf(b"pdf-1")
        self.assertEqual((first["model"], "Shipper:" in first["text"]), ("main-model", True))
        again = ai_service.transcribe_scanned_pdf(b"pdf-1")
        self.assertTrue(again["cached"])
        self.assertEqual(FakeClient.calls, ["main-model"])  # one API call, however many readers ask

    def test_overload_uses_the_backup_model(self):
        FakeClient.plan = {"main-model": OVERLOAD}
        self.assertEqual(ai_service.transcribe_scanned_pdf(b"pdf-2")["model"], "backup-a")

    def test_bad_credentials_stop_at_once_and_failures_are_remembered(self):
        FakeClient.plan = {"main-model": DENIED}
        with self.assertRaisesRegex(RuntimeError, "credentials"):
            ai_service.transcribe_scanned_pdf(b"pdf-3")
        self.assertEqual(FakeClient.calls, ["main-model"])
        FakeClient.calls.clear()
        with self.assertRaises(RuntimeError):  # the second reader does not hit the API again
            ai_service.transcribe_scanned_pdf(b"pdf-3")
        self.assertEqual(FakeClient.calls, [])

    def test_all_models_overloaded_gives_a_friendly_error_without_a_key(self):
        FakeClient.plan = {"main-model": OVERLOAD, "backup-a": OVERLOAD}
        with self.assertRaises(RuntimeError) as ctx:
            ai_service.transcribe_scanned_pdf(b"pdf-4")
        self.assertIn("temporarily overloaded", str(ctx.exception))
        self.assertNotIn("test-key", str(ctx.exception))

    def test_unusable_answers_are_rejected_and_code_fences_removed(self):
        FakeClient.plan = {"main-model": "ok"}  # too short to be a transcription -> next model
        self.assertEqual(ai_service.transcribe_scanned_pdf(b"pdf-5")["model"], "backup-a")
        FakeClient.plan = {"main-model": "```\n" + GOOD + "```"}
        self.assertTrue(ai_service.transcribe_scanned_pdf(b"pdf-6")["text"].startswith("=== PAGE 1"))

    def test_oversized_pdf_is_refused_before_any_call(self):
        with self.assertRaisesRegex(RuntimeError, "15 MB"):
            ai_service.transcribe_scanned_pdf(b"x" * (ai_service.VISION_MAX_BYTES + 1))
        self.assertEqual(FakeClient.calls, [])

    def test_vision_switches(self):
        self.assertTrue(ai_service.vision_enabled())
        with mock.patch.dict(os.environ, {"GEMINI_VISION_ENABLED": "false"}):
            self.assertFalse(ai_service.vision_enabled())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertFalse(ai_service.vision_enabled())
        with mock.patch.dict(os.environ, {"GEMINI_AI_ENABLED": "false"}):
            self.assertFalse(ai_service.vision_enabled())


class TranscriptionParsingTests(unittest.TestCase):
    def test_pages_lines_and_illegible_markers(self):
        rows = _transcription_rows("=== PAGE 1 ===\nShipper: ACME\n\n=== PAGE 2 ===\nGross Weight: [illegible] KG\n```")
        self.assertEqual(rows, [("Shipper: ACME", 1, 1), ("Gross Weight: ??? KG", 2, 1)])

    def test_containers_label_is_recognised(self):
        self.assertEqual(ALIAS_TO_FIELD.get(_normalize_label("Containers")), "container_count")


class ScanStubMixin:
    """Stands in for Gemini vision: the email_512 pair gets its own text; every other scan fails like an outage."""

    SI = BUNDLE / "attachments" / "email_512_SI.pdf"
    BL = BUNDLE / "attachments" / "email_512_BL.pdf"

    def texts(self):
        """Each of the pair gets its own title; every other scan is unknown to the stub and fails like a real outage."""
        bl = GOOD.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)")
        return {hashlib.sha256(self.SI.read_bytes()).hexdigest(): GOOD, hashlib.sha256(self.BL.read_bytes()).hexdigest(): bl}

    def stub(self, text_for=None, error=None):
        known = {**self.texts(), **(text_for or {})}

        def transcribe(raw):
            digest = hashlib.sha256(raw).hexdigest()
            if error or digest not in known:
                raise RuntimeError(error or "Gemini is temporarily overloaded (high demand).")
            return {"text": known[digest], "model": "test-model"}

        return mock.patch.multiple(ai_service, vision_enabled=mock.Mock(return_value=True), transcribe_scanned_pdf=transcribe)


@unittest.skipUnless((BUNDLE / "attachments" / "email_512_SI.pdf").is_file(), "participant bundle not present")
class ScannedPdfTests(ScanStubMixin, unittest.TestCase):
    def test_vision_off_keeps_todays_behaviour(self):
        with mock.patch.object(ai_service, "vision_enabled", return_value=False), self.assertRaisesRegex(ValueError, "require OCR/manual review"):
            read_document_ex(self.SI.read_bytes(), "x.pdf")

    def test_vision_reading_is_labelled_and_lower_confidence(self):
        with self.stub():
            rows, meta = read_document_ex(self.SI.read_bytes(), "x.pdf")
        self.assertEqual((meta["method"], meta["model"]), ("gemini_vision", "test-model"))
        self.assertIn(("Containers: 6 x 40'HC", 1, 7), rows)

    def test_a_failed_transcription_goes_to_review_with_the_reason(self):
        class Inbox:
            def read_bytes(self, path):
                return TestPath.SI.read_bytes()

        TestPath = type(self)
        with self.stub(error="Gemini is temporarily overloaded (high demand)."), self.assertRaises(ReviewRequired) as ctx:
            extract_attachment_with_evidence(Inbox(), "attachments/email_512_SI.pdf", "SI")
        self.assertEqual((ctx.exception.review_reason, ctx.exception.internal_reason), ("unreadable", "document_read_failed"))
        self.assertIn("Gemini vision", ctx.exception.detail)

    def test_end_to_end_a_scan_is_read_prefilled_and_escalated_for_confirmation(self):
        with self.stub():
            artifacts = process_dataset(BUNDLE)
        result, detail = artifacts.submission["email_512"], artifacts.internal_results["email_512"]
        # The reading is a model transcription, so a person confirms it: escalated as "unreadable" with the values prefilled.
        self.assertEqual((result["status"], result["review_reason"], result["defect_fields"]), ("NEEDS_REVIEW", "unreadable", []))
        self.assertEqual(detail["internal_reason"], "ai_read_needs_confirmation")
        self.assertEqual(detail["extracted"]["si"]["container_count"], "6 x 40'HC")
        self.assertIn("Preview of the comparison: No mismatch detected", detail["detail"])
        tags = {item.get("read_method") for role in detail["evidence"].values() for item in role.values()}
        self.assertEqual(tags, {"gemini_vision"})
        confidences = {item["confidence"] for role in detail["evidence"].values() for item in role.values()}
        self.assertEqual(confidences, {0.8})
        # the scans the stub cannot read still go to review with the reason, rather than being guessed
        for email_id in ("email_513", "email_514"):
            self.assertEqual(artifacts.submission[email_id]["status"], "NEEDS_REVIEW")
            self.assertIn("Gemini vision", artifacts.internal_results[email_id]["detail"])

    def test_a_garbled_transcription_is_sent_to_review_not_compared(self):
        garbled = "=== PAGE 1 ===\nSHIPPING INSTRUCTION\nShipper. APRIL FAR EAST\nPortof Discharge: TUTICORIN\nContainers 6 x 40}HC\n"
        digest = hashlib.sha256(self.SI.read_bytes()).hexdigest()
        with self.stub({digest: garbled}):
            artifacts = process_dataset(BUNDLE)
        result = artifacts.submission["email_512"]
        self.assertEqual((result["status"], result["review_reason"]), ("NEEDS_REVIEW", "missing_value"))


class PunctuationLeniencyTests(unittest.TestCase):
    SI = {"shipper": "ACME LTD", "consignee": "KPP-ANTALIS (SINGAPORE) PTE LTD", "notify_party": "X", "port_of_loading": "NANTONG, CHINA",
          "port_of_discharge": "GDANSK, POLAND", "container_count": "1 x 20'FCL", "gross_weight_kg": "22,825 KG"}

    def compare(self, changes, lenient):
        from src.compare import compare_documents

        return compare_documents(self.SI, {**self.SI, **changes}, ignore_punctuation=lenient)

    def test_a_dropped_full_stop_is_a_mismatch_for_text_documents_but_not_for_scans(self):
        change = {"consignee": "KPP-ANTALIS (SINGAPORE) PTE. LTD."}
        self.assertEqual(self.compare(change, lenient=False)["status"], "MISMATCH")
        result = self.compare(change, lenient=True)
        self.assertEqual((result["status"], result["ignored_punctuation"]), ("OK", ["consignee"]))

    def test_real_differences_are_still_flagged_when_lenient(self):
        result = self.compare({"consignee": "OTHER TRADING PTE. LTD.", "port_of_discharge": "ROTTERDAM, NETHERLANDS"}, lenient=True)
        self.assertEqual([m["field"] for m in result["mismatches"]], ["consignee", "port_of_discharge"])

    def test_numbers_are_never_treated_leniently(self):
        # "22.825" is a different quantity from "22,825": a misplaced separator must still be caught
        for change in ({"gross_weight_kg": "22.825 KG"}, {"container_count": "12 x 20'FCL"}):
            self.assertEqual(self.compare(change, lenient=True)["status"], "MISMATCH", change)


@unittest.skipUnless((BUNDLE / "attachments" / "email_512_SI.pdf").is_file(), "participant bundle not present")
class ScanPunctuationEndToEndTests(ScanStubMixin, unittest.TestCase):

    def test_a_dropped_period_in_a_scan_is_not_previewed_as_a_defect(self):
        bl = GOOD.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("STATIONERY LLC", "STATIONERY L.L.C.")
        with self.stub({hashlib.sha256(self.BL.read_bytes()).hexdigest(): bl}):
            artifacts = process_dataset(BUNDLE)
        detail = artifacts.internal_results["email_512"]
        self.assertEqual(artifacts.submission["email_512"]["status"], "NEEDS_REVIEW")  # a person confirms every scan
        self.assertIn("No mismatch detected", detail["detail"])
        self.assertIn("Punctuation-only differences ignored: consignee, notify_party", detail["detail"])

    def test_a_real_difference_in_a_scan_is_still_a_defect(self):
        bl = GOOD.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("TUTICORIN, INDIA", "MUMBAI, INDIA")
        with self.stub({hashlib.sha256(self.BL.read_bytes()).hexdigest(): bl}):
            artifacts = process_dataset(BUNDLE)
        self.assertIn("Fields: port_of_discharge", artifacts.internal_results["email_512"]["detail"])

    def test_text_layer_documents_are_unaffected(self):
        artifacts = process_dataset(BUNDLE)  # vision off: nothing is lenient anywhere
        lenient = [k for k, d in artifacts.internal_results.items() if d.get("ignored_punctuation")]
        self.assertEqual(lenient, [])

    def test_a_person_can_confirm_a_scan_and_it_becomes_a_normal_result(self):
        from src.service import apply_human_review

        with self.stub():
            artifacts = process_dataset(BUNDLE)
        extracted = artifacts.internal_results["email_512"]["extracted"]
        updated = apply_human_review(artifacts, "email_512", extracted["si"], extracted["bl"], "checked against the scan")
        self.assertEqual(updated.submission["email_512"]["status"], "OK")
        self.assertEqual(updated.internal_results["email_512"]["human_review"]["note"], "checked against the scan")
        wrong = {**extracted["bl"], "gross_weight_kg": "999 KG"}
        corrected = apply_human_review(artifacts, "email_512", extracted["si"], wrong, "weight differs on the scan")
        self.assertEqual(corrected.submission["email_512"]["defect_fields"], ["gross_weight_kg"])


class CooldownTests(FakeGeminiMixin, unittest.TestCase):
    def test_an_outage_pauses_every_document_instead_of_making_each_one_wait(self):
        FakeClient.plan = {"main-model": OVERLOAD, "backup-a": OVERLOAD}
        with self.assertRaises(RuntimeError):
            ai_service.transcribe_scanned_pdf(b"doc-a")
        self.assertEqual(FakeClient.calls, ["main-model", "backup-a"])
        FakeClient.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "temporarily overloaded"):
            ai_service.transcribe_scanned_pdf(b"a-different-doc")  # no API call: vision is paused for everyone
        self.assertEqual(FakeClient.calls, [])

    def test_a_cooldown_expires(self):
        FakeClient.plan = {"main-model": OVERLOAD, "backup-a": OVERLOAD}
        with self.assertRaises(RuntimeError):
            ai_service.transcribe_scanned_pdf(b"doc-b")
        ai_service._vision_cooldown[0] = 0.0  # time passes
        ai_service._vision_failures.clear()
        FakeClient.plan = {}
        self.assertEqual(ai_service.transcribe_scanned_pdf(b"doc-c")["model"], "main-model")

    def test_one_bad_document_does_not_pause_the_others(self):
        FakeClient.plan = {"main-model": "no", "backup-a": "no"}  # unusable answers for this document
        with self.assertRaises(RuntimeError):
            ai_service.transcribe_scanned_pdf(b"doc-d")
        FakeClient.plan = {}
        self.assertEqual(ai_service.transcribe_scanned_pdf(b"doc-e")["model"], "main-model")

    def test_at_most_two_models_are_tried(self):
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "backup-a,backup-b,backup-c"}):
            FakeClient.plan = {"main-model": OVERLOAD, "backup-a": OVERLOAD, "backup-b": OVERLOAD}
            with self.assertRaises(RuntimeError):
                ai_service.transcribe_scanned_pdf(b"doc-f")
        self.assertEqual(FakeClient.calls, ["main-model", "backup-a"])


@unittest.skipUnless((BUNDLE / "attachments" / "email_512_SI.pdf").is_file(), "participant bundle not present")
class PrewarmTests(ScanStubMixin, unittest.TestCase):
    def emails(self):
        import json

        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((BUNDLE / "inbox").glob("email_*.json"))]

    class Inbox:
        def read_bytes(self, path):
            return (BUNDLE / path).read_bytes()

    def test_every_scanned_pdf_is_read_once_and_concurrently(self):
        import threading
        import time

        from src.service import prewarm_vision

        seen, threads, lock = [], set(), threading.Lock()

        def slow(raw):
            with lock:
                seen.append(hashlib.sha256(raw).hexdigest())
                threads.add(threading.get_ident())
            time.sleep(0.05)
            return {"text": GOOD, "model": "test-model"}

        with mock.patch.multiple(ai_service, vision_enabled=mock.Mock(return_value=True), transcribe_scanned_pdf=slow):
            sent = prewarm_vision(self.Inbox(), self.emails())
        self.assertEqual(sent, 6)  # the three scanned pairs; text-layer and corrupt PDFs are not sent
        self.assertEqual(len(set(seen)), 6)
        self.assertGreater(len(threads), 1)

    def test_nothing_is_sent_when_vision_is_off(self):
        from src.service import prewarm_vision

        with mock.patch.object(ai_service, "vision_enabled", return_value=False), \
             mock.patch.object(ai_service, "transcribe_scanned_pdf") as call:
            self.assertEqual(prewarm_vision(self.Inbox(), self.emails()), 0)
        call.assert_not_called()

    def test_failures_while_pre_reading_do_not_break_the_run(self):
        from src.service import prewarm_vision

        def fail(raw):
            raise RuntimeError("Gemini is temporarily overloaded (high demand).")

        with mock.patch.multiple(ai_service, vision_enabled=mock.Mock(return_value=True), transcribe_scanned_pdf=fail):
            self.assertEqual(prewarm_vision(self.Inbox(), self.emails()), 6)  # attempted, all failed, no exception


class TransientPauseTests(FakeGeminiMixin, unittest.TestCase):
    def test_a_short_overload_pause_can_be_lifted_for_a_retry(self):
        FakeClient.plan = {"main-model": OVERLOAD, "backup-a": OVERLOAD}
        with self.assertRaises(RuntimeError):
            ai_service.transcribe_scanned_pdf(b"doc-1")
        self.assertTrue(ai_service.clear_transient_pause())
        FakeClient.plan, FakeClient.calls = {}, []
        self.assertEqual(ai_service.transcribe_scanned_pdf(b"doc-1")["model"], "main-model")
        self.assertEqual(FakeClient.calls, ["main-model"])

    def test_a_quota_or_credentials_pause_is_never_lifted(self):
        FakeClient.plan = {"main-model": DENIED}
        with self.assertRaises(RuntimeError):
            ai_service.transcribe_scanned_pdf(b"doc-2")
        self.assertFalse(ai_service.clear_transient_pause())
        FakeClient.calls.clear()
        with self.assertRaises(RuntimeError):
            ai_service.transcribe_scanned_pdf(b"doc-3")
        self.assertEqual(FakeClient.calls, [])

    def test_with_no_pause_active_a_retry_is_allowed(self):
        self.assertTrue(ai_service.clear_transient_pause())


@unittest.skipUnless((BUNDLE / "attachments" / "email_512_SI.pdf").is_file(), "participant bundle not present")
class PrewarmRetryTests(ScanStubMixin, unittest.TestCase):
    Inbox = PrewarmTests.Inbox
    emails = PrewarmTests.emails

    def run_prewarm(self, transcribe, **kwargs):
        from src.service import prewarm_vision

        with mock.patch.multiple(ai_service, vision_enabled=mock.Mock(return_value=True), transcribe_scanned_pdf=transcribe):
            return prewarm_vision(self.Inbox(), self.emails(), retry_pause=0, **kwargs)

    def test_at_most_two_scans_are_read_at_a_time(self):
        import threading
        import time

        active, peak, lock = [0], [0], threading.Lock()

        def slow(raw):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return {"text": GOOD, "model": "m"}

        self.assertEqual(self.run_prewarm(slow), 6)
        self.assertEqual(peak[0], 2)

    def test_scans_that_were_throttled_get_one_calm_second_try(self):
        seen = {}

        def flaky(raw):
            key = hashlib.sha256(raw).hexdigest()
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 1:
                raise RuntimeError("Gemini is temporarily overloaded (high demand).")
            return {"text": GOOD, "model": "m"}

        self.assertEqual(self.run_prewarm(flaky), 6)
        self.assertEqual(sorted(seen.values()), [2] * 6)  # every scan: failed once, then read

    def test_a_scan_that_keeps_failing_is_not_hammered(self):
        calls = []

        def down(raw):
            calls.append(1)
            raise RuntimeError("Gemini is temporarily overloaded (high demand).")

        self.run_prewarm(down)
        self.assertEqual(len(calls), 6 + 1)  # the first pass, then one retry that stops at the first failure

    def test_no_retry_when_the_pause_is_a_quota_or_credentials_one(self):
        calls = []

        def down(raw):
            calls.append(1)
            raise RuntimeError("Gemini rejected the request (credentials or permission problem).")

        with mock.patch.object(ai_service, "clear_transient_pause", return_value=False):
            self.run_prewarm(down)
        self.assertEqual(len(calls), 6)


@unittest.skipUnless((BUNDLE / "attachments" / "email_512_SI.pdf").is_file(), "participant bundle not present")
class ReviewPageReasonTests(unittest.TestCase):
    def test_a_scan_that_could_not_be_read_says_why(self):
        import firebase_config
        from streamlit.testing.v1 import AppTest

        def overloaded(raw):
            raise RuntimeError("Gemini is temporarily overloaded (high demand). The local result is unaffected; retry in a minute.")

        original = firebase_config.db
        firebase_config.db = None
        self.addCleanup(setattr, firebase_config, "db", original)
        with mock.patch.multiple(ai_service, vision_enabled=mock.Mock(return_value=True), transcribe_scanned_pdf=overloaded), \
             mock.patch("src.service.prewarm_vision", lambda *a, **k: 0):
            at = AppTest.from_file(str(WEB_APP / "app.py"), default_timeout=300).run()
            [b for b in at.sidebar.button if b.label == "Inbox & work queue"][0].click()
            at.run()
            [b for b in at.button if "Analyze inbox" in b.label][0].click()
            at.run()
            [b for b in at.sidebar.button if b.label == "Review & resolution"][0].click()
            at.run()
            case = at.selectbox[0]
            case.select([o for o in case.options if "email_512" in str(o)][0]).run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("Why this needs review" in i.value and "overloaded" in i.value for i in at.info))


if __name__ == "__main__":
    unittest.main()
