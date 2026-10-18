"""Small NACE client with fixed endpoints and no credential-bearing diagnostics."""

import json
import math
import os
import re
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path


_BASE_URL = "https://console.nace.ai/v1"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class NaceError(RuntimeError):
    """A sanitized configuration, transport, or API-response error."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A bearer token must never follow a server-selected destination.
        return None


def _env_value(raw):
    """Read one dotenv value literally; never expand variables or execute code."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] not in "\"'":
        return re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    quote = raw[0]
    chars = []
    index = 1
    while index < len(raw):
        char = raw[index]
        if char == quote:
            tail = raw[index + 1:].strip()
            if tail and not tail.startswith("#"):
                raise NaceError("Invalid NACE_API_KEY assignment in env file")
            return "".join(chars)
        if quote == '"' and char == "\\" and index + 1 < len(raw):
            following = raw[index + 1]
            if following in ('"', "\\"):
                chars.append(following)
                index += 2
                continue
        chars.append(char)
        index += 1
    raise NaceError("Invalid NACE_API_KEY assignment in env file")


def _key_from_file(path):
    try:
        with Path(path).open(encoding="utf-8-sig") as handle:
            value = None
            for line in handle:
                match = re.match(r"^\s*(?:export\s+)?NACE_API_KEY\s*=\s*(.*?)\s*$", line)
                if match:
                    value = _env_value(match.group(1))
            return value
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise NaceError("Cannot read NACE_API_KEY env file") from None


def _validate_questions(questions):
    if not isinstance(questions, dict) or not questions:
        raise ValueError("Questions must be a nonempty object")
    for question_id, question in questions.items():
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("Question IDs must be nonempty strings")
        if not isinstance(question, dict) or not isinstance(question.get("instructions"), str):
            raise ValueError("Each question must contain text instructions")
        if question.get("type") not in ("choice", "noul"):
            raise ValueError("Unsupported question type")
        if question["type"] == "choice":
            criteria = question.get("criteria")
            if not isinstance(criteria, dict) or not criteria:
                raise ValueError("Choice questions require criteria")
            if any(not isinstance(code, str) or not code or not isinstance(description, str)
                   for code, description in criteria.items()):
                raise ValueError("Choice criteria must map nonempty strings to descriptions")


def _is_probability(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 1 and math.isfinite(value))


def validate_answers(response, questions):
    """Return validated answers, or raise ValueError without echoing response data."""
    _validate_questions(questions)
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise ValueError("Response must contain an answers object")
    answers = response["answers"]
    if set(answers) != set(questions):
        raise ValueError("Answer IDs do not match question IDs")
    for question_id, question in questions.items():
        answer = answers[question_id]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise ValueError("Answer type does not match question type")
        if question["type"] == "noul":
            if not _is_probability(answer.get("noul")):
                raise ValueError("Invalid noul answer")
            continue
        criteria = question["criteria"]
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in criteria:
            raise ValueError("Answer choice is not a criterion")
        if not _is_probability(answer.get("confidence")):
            raise ValueError("Invalid answer confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(criteria):
            raise ValueError("Probability keys do not match criteria")
        if not all(_is_probability(value) for value in probabilities.values()):
            raise ValueError("Invalid answer probability")
        if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.02, rel_tol=0):
            raise ValueError("Answer probabilities are not normalized")
    return answers


class NaceClient:
    """Call only NACE's official HTTPS API; requests are never retried."""

    def __init__(self, key, timeout=60):
        if not isinstance(key, str) or not key.strip():
            raise NaceError("NACE_API_KEY is not configured")
        key = key.strip()
        if not key.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key):
            raise NaceError("NACE_API_KEY has invalid characters")
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("Timeout must be a finite positive number")
        self._key = key
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_RejectRedirects())

    @classmethod
    def from_env(cls, env_file=Path(".env"), timeout=60):
        """Prefer a nonempty process variable, otherwise read the named env file."""
        key = os.environ.get("NACE_API_KEY", "")
        if not key.strip():
            key = _key_from_file(env_file)
        return cls(key, timeout=timeout)

    def _request(self, path, payload=None):
        headers = {"Authorization": "Bearer " + self._key, "Accept": "application/json"}
        data = None
        if payload is not None:
            try:
                data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                raise NaceError("NACE request payload is not valid JSON") from None
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(_BASE_URL + path, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise NaceError("NACE HTTP status " + str(status))
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise NaceError("NACE HTTP status " + str(status)) from None
        except (urllib.error.URLError, OSError, HTTPException):
            raise NaceError("NACE request failed due to a network or timeout error") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise NaceError("NACE response exceeds the size limit")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise NaceError("NACE returned invalid JSON") from None
        if not isinstance(result, dict):
            raise NaceError("NACE response must be a JSON object")
        return result

    def models(self):
        """Return the model-list response after checking its documented shape."""
        result = self._request("/models")
        models = result.get("models")
        if not isinstance(models, list) or any(
                not isinstance(model, dict) or not isinstance(model.get("name"), str)
                or not model["name"] for model in models):
            raise NaceError("NACE returned an invalid model list")
        return result

    def evaluate(self, payload):
        """Evaluate a caller-supplied JSON state and validate every returned answer."""
        if (not isinstance(payload, dict) or not isinstance(payload.get("model"), str)
                or not payload["model"] or "state" not in payload):
            raise ValueError("Payload requires a model, state, and questions")
        _validate_questions(payload.get("questions"))
        result = self._request("/systemone", payload)
        try:
            validate_answers(result, payload["questions"])
        except ValueError as error:
            raise NaceError("NACE returned invalid answers: " + str(error)) from None
        return result
