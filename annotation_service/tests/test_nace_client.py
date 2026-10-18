import copy
import io
import json
import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from annotation_service.nace_client import NaceClient, NaceError, _RejectRedirects, validate_answers


QUESTIONS = {
    "primary": {"type": "choice", "instructions": "Choose the supported category.",
                "criteria": {"A": "Observed category", "NONE": "Not supported"}},
}
VALID = {"answers": {"primary": {"type": "choice", "choice": "A", "confidence": 0.8,
                                  "probabilities": {"A": 0.8, "NONE": 0.2}}},
         "usage": {"total_tokens": 123}, "request_id": "test-request"}


def response(value, status=200):
    result = io.BytesIO(json.dumps(value).encode("utf-8"))
    result.status = status
    return result


class NaceClientTests(unittest.TestCase):
    def setUp(self):
        self.key = "test-key-never-log"
        self.builder = patch("annotation_service.nace_client.urllib.request.build_opener")
        self.opener = self.builder.start().return_value
        self.addCleanup(self.builder.stop)
        self.client = NaceClient(self.key, timeout=7)

    def test_models_uses_fixed_https_and_bearer_auth(self):
        expected = {"models": [{"name": "drex-v2.0"}]}
        self.opener.open.return_value = response(expected)
        self.assertEqual(self.client.models(), expected)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://console.nace.ai/v1/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.key)
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 7})
        self.assertNotIn(self.key, repr(self.client))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_evaluate_posts_payload_and_preserves_metadata(self):
        self.opener.open.return_value = response(VALID)
        payload = {"model": "drex-v2.0", "state": {"text": "Аннотация"}, "questions": QUESTIONS}
        self.assertEqual(self.client.evaluate(payload), VALID)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://console.nace.ai/v1/systemone")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_http_error_does_not_leak_body_key_or_chained_exception(self):
        body = io.BytesIO(("private body " + self.key).encode())
        self.opener.open.side_effect = urllib.error.HTTPError(
            "https://console.nace.ai/v1/models", 401, self.key, {}, body)
        try:
            self.client.models()
        except NaceError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertEqual(str(error), "NACE HTTP status 401")
            self.assertNotIn(self.key, rendered)
            self.assertNotIn("private body", rendered)
            self.assertTrue(body.closed)
        else:
            self.fail("HTTP error was not reported")
        self.assertEqual(self.opener.open.call_count, 1)

    def test_network_error_is_sanitized_and_not_retried(self):
        for error in (urllib.error.URLError(self.key), TimeoutError(self.key), OSError(self.key)):
            with self.subTest(error_type=type(error).__name__):
                self.opener.open.side_effect = error
                with self.assertRaisesRegex(NaceError, "network or timeout") as caught:
                    self.client.models()
                self.assertNotIn(self.key, str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 3)

    def test_redirect_handler_rejects_every_redirect(self):
        handler = _RejectRedirects()
        request = urllib.request.Request("https://console.nace.ai/v1/models",
                                         headers={"Authorization": "Bearer " + self.key})
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(request, None, code, "", {},
                                                       "https://other.example/steal"))

    def test_redirect_status_is_rejected_even_for_custom_opener(self):
        self.opener.open.return_value = response({"secret": self.key}, status=302)
        with self.assertRaisesRegex(NaceError, "HTTP status 302"):
            self.client.models()

    def test_invalid_response_body_is_not_echoed(self):
        raw = io.BytesIO(("invalid json " + self.key).encode())
        raw.status = 200
        self.opener.open.return_value = raw
        with self.assertRaisesRegex(NaceError, "invalid JSON") as caught:
            self.client.models()
        self.assertNotIn(self.key, str(caught.exception))

    def test_models_shape_is_checked(self):
        for data in ([], {}, {"models": [{}]}, {"models": "drex-v2.0"}):
            with self.subTest(data=data):
                self.opener.open.return_value = response(data)
                with self.assertRaises(NaceError):
                    self.client.models()

    def test_evaluate_rejects_missing_answers(self):
        self.opener.open.return_value = response({"answers": {}})
        with self.assertRaisesRegex(NaceError, "Answer IDs"):
            self.client.evaluate({"model": "drex-v2.0", "state": [], "questions": QUESTIONS})

    def test_nonfinite_payload_and_invalid_questions_never_sent(self):
        with self.assertRaisesRegex(NaceError, "not valid JSON"):
            self.client.evaluate({"model": "drex-v2.0", "state": float("nan"), "questions": QUESTIONS})
        with self.assertRaises(ValueError):
            self.client.evaluate({"model": "drex-v2.0", "state": {}, "questions": {}})
        self.opener.open.assert_not_called()

    def test_timeout_and_key_validation(self):
        for timeout in (0, -1, True, float("nan"), float("inf"), "60"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                NaceClient(self.key, timeout=timeout)
        for key in (None, "", " ", "a\nb", "a b", "a\x00b", "ключ"):
            with self.subTest(key_type=type(key).__name__), self.assertRaises(NaceError):
                NaceClient(key)


class EnvTests(unittest.TestCase):
    def test_env_precedence_avoids_reading_file(self):
        with patch.dict(os.environ, {"NACE_API_KEY": "environment-key"}), \
                patch("annotation_service.nace_client._key_from_file") as reader:
            client = NaceClient.from_env(Path("missing.env"))
        self.assertEqual(client._key, "environment-key")
        reader.assert_not_called()

    def test_dotenv_quotes_export_comments_and_no_execution(self):
        values = [
            ("NACE_API_KEY=file-key # comment", "file-key"),
            (' export NACE_API_KEY = "file-key#part" # comment', "file-key#part"),
            ("NACE_API_KEY='file-key#part'", "file-key#part"),
            ("NACE_API_KEY=file-key#part", "file-key#part"),
            ("NACE_API_KEY='$(touch-do-not-execute)'", "$(touch-do-not-execute)"),
            ('NACE_API_KEY="${OTHER_KEY}"', "${OTHER_KEY}"),
        ]
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"NACE_API_KEY": ""}):
            path = Path(folder) / ".env"
            for assignment, expected in values:
                with self.subTest(assignment=assignment):
                    path.write_text("# config\nUNRELATED='bad quote\n" + assignment + "\n", encoding="utf-8")
                    self.assertEqual(NaceClient.from_env(path)._key, expected)

    def test_empty_or_missing_key_reports_configuration_error(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"NACE_API_KEY": ""}):
            path = Path(folder) / ".env"
            with self.assertRaisesRegex(NaceError, "not configured"):
                NaceClient.from_env(path)
            path.write_text("UNRELATED=some-value\nNACE_API_KEY=\n", encoding="utf-8")
            with self.assertRaisesRegex(NaceError, "not configured"):
                NaceClient.from_env(path)

    def test_malformed_assignment_never_echoes_value(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"NACE_API_KEY": ""}):
            path = Path(folder) / ".env"
            for value in ('"private-key', '"private-key" trailing'):
                path.write_text("NACE_API_KEY=" + value, encoding="utf-8")
                with self.assertRaises(NaceError) as caught:
                    NaceClient.from_env(path)
                self.assertNotIn("private-key", str(caught.exception))


class AnswerValidationTests(unittest.TestCase):
    def test_valid_answers_are_returned(self):
        self.assertIs(validate_answers(VALID, QUESTIONS), VALID["answers"])

    def test_exact_question_ids_required(self):
        for answers in ({}, {**VALID["answers"], "extra": VALID["answers"]["primary"]}):
            with self.assertRaisesRegex(ValueError, "Answer IDs"):
                validate_answers({"answers": answers}, QUESTIONS)

    def test_invalid_choice_answers_rejected(self):
        edits = [
            {"type": "noul"}, {"choice": "UNKNOWN"}, {"choice": []},
            {"confidence": True}, {"confidence": -0.1}, {"confidence": float("nan")},
            {"confidence": float("inf")}, {"confidence": "0.9"},
            {"confidence": 10 ** 1000},
            {"probabilities": {"A": 1}},
            {"probabilities": {"A": 0.8, "NONE": 0.1, "EXTRA": 0.1}},
            {"probabilities": {"A": 0.8, "NONE": 0.5}},
            {"probabilities": {"A": 1.1, "NONE": -0.1}},
            {"probabilities": {"A": True, "NONE": 0}},
            {"probabilities": {"A": float("nan"), "NONE": 0.2}},
            {"probabilities": {"A": float("inf"), "NONE": 0.2}},
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                data = copy.deepcopy(VALID)
                data["answers"]["primary"].update(edit)
                with self.assertRaises(ValueError):
                    validate_answers(data, QUESTIONS)

    def test_rounding_tolerance(self):
        data = copy.deepcopy(VALID)
        data["answers"]["primary"]["probabilities"] = {"A": 0.79, "NONE": 0.20}
        validate_answers(data, QUESTIONS)

    def test_noul_only_when_requested(self):
        questions = {"check": {"type": "noul", "instructions": "Evaluate support"}}
        answer = {"answers": {"check": {"type": "noul", "noul": 0.9868}}}
        validate_answers(answer, questions)
        for value in (True, None, -0.1, 1.1, float("nan"), "0.9"):
            answer["answers"]["check"]["noul"] = value
            with self.assertRaises(ValueError):
                validate_answers(answer, questions)


if __name__ == "__main__":
    unittest.main()
