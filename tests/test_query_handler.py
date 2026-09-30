"""Handler tests for web/api/query.py with a fake model client: no API key, no network.

Live model output is not deterministic (see CLAUDE.md), so these tests cover the code around the model:
request checks, error mapping, and how a model reply becomes a validated filter.
"""
import http.client
import importlib.util
import json
import threading
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # anthropic 1.x builds its errors from httpx2 objects
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("query", ROOT / "web" / "api" / "query.py")
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), query.handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def limiter_allows(monkeypatch):
    """These tests are about the handler, not Upstash: let every request through by default."""
    monkeypatch.setattr(query, "rate_limit_check", lambda ip, now=None: None)


def call(port, method="POST", body=None, raw=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    conn.request(method, "/api/query", body=payload or None, headers={"Content-Type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.headers, json.loads(data)


def api_error(cls, status, message="upstream said sk-ant-SECRET"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=None)


def test_valid_question_returns_the_validated_filter(server, monkeypatch):
    seen = []
    monkeypatch.setattr(query, "translate", lambda q: seen.append(q) or {"filter": {"suburb_name": "ADELAIDE"}, "unsupported": None})
    status, headers, body = call(server, body={"question": "  trees in Adelaide  "})
    assert status == 200
    assert body == {"filter": {"suburb_name": "ADELAIDE"}, "unsupported": None}
    assert seen == ["trees in Adelaide"]
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [{"question": ""}, {"question": "   "}, {"question": "x" * 201}, {"question": 5}, {"question": ["a"]}, {"other": "x"}, [1, 2], {}],
)
def test_bad_questions_are_rejected_before_the_model(server, monkeypatch, body):
    monkeypatch.setattr(query, "translate", lambda q: pytest.fail("model must not be called"))
    status, _, payload = call(server, body=body)
    assert status == 400 and "error" in payload


def test_oversize_and_malformed_bodies_are_rejected(server, monkeypatch):
    monkeypatch.setattr(query, "translate", lambda q: pytest.fail("model must not be called"))
    padded = json.dumps({"question": "trees", "pad": "x" * 2000}).encode()  # valid JSON, only the size is wrong
    assert call(server, raw=padded)[0] == 400
    assert call(server, raw=b"not json")[0] == 400
    assert call(server, raw=b"")[0] == 400


def test_blocked_request_gets_429_and_never_reaches_the_model(server, monkeypatch):
    monkeypatch.setattr(query, "rate_limit_check", lambda ip, now=None: ("Demo limit reached.", 1234))
    monkeypatch.setattr(query, "translate", lambda q: pytest.fail("model must not be called"))
    status, headers, payload = call(server, body={"question": "trees"})
    assert status == 429
    assert payload == {"error": "Demo limit reached."}
    assert headers["Retry-After"] == "1234"


def test_limiter_outage_fails_closed(server, monkeypatch):
    def down(ip, now=None):
        raise query.RedisError("timeout while talking to https://secret.example")

    monkeypatch.setattr(query, "rate_limit_check", down)
    monkeypatch.setattr(query, "translate", lambda q: pytest.fail("model must not be called"))
    status, _, payload = call(server, body={"question": "trees"})
    assert status == 503
    assert "secret" not in json.dumps(payload) and "timeout" not in json.dumps(payload)


def test_limiter_receives_the_forwarded_client_ip(server, monkeypatch):
    seen = []
    monkeypatch.setattr(query, "rate_limit_check", lambda ip, now=None: seen.append(ip))
    monkeypatch.setattr(query, "translate", lambda q: {"filter": {}, "unsupported": None})
    assert call(server, body={"question": "trees"}, headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})[0] == 200
    assert call(server, body={"question": "trees"})[0] == 200  # no header: falls back to the socket address
    assert seen == ["203.0.113.7", "127.0.0.1"]


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "x" * 201}, {"other": "x"}])
def test_invalid_requests_do_not_count_against_the_limit(server, monkeypatch, body):
    monkeypatch.setattr(query, "rate_limit_check", lambda ip, now=None: pytest.fail("limiter must not be called"))
    assert call(server, body=body)[0] == 400


def test_get_is_not_allowed(server):
    status, headers, _ = call(server, method="GET")
    assert status == 405 and headers["Allow"] == "POST"


@pytest.mark.parametrize(
    "error, expected",
    [
        (api_error(anthropic.AuthenticationError, 401), 500),
        (api_error(anthropic.RateLimitError, 429), 429),
        (anthropic.APITimeoutError(httpx.Request("POST", "https://api.anthropic.com/v1/messages")), 504),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), 502),
        (api_error(anthropic.InternalServerError, 500), 502),
    ],
)
def test_upstream_errors_map_to_safe_responses(server, monkeypatch, error, expected):
    def boom(q):
        raise error

    monkeypatch.setattr(query, "translate", boom)
    status, _, payload = call(server, body={"question": "trees"})
    assert status == expected
    assert "sk-ant" not in json.dumps(payload)  # never echo upstream text


def fake_client(monkeypatch, text=None, stop_reason="end_turn", calls=None):
    def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)] if text is not None else [])

    monkeypatch.setattr(query.anthropic, "Anthropic", lambda **kw: SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_translate_validates_the_model_reply(monkeypatch):
    calls = []
    reply = json.dumps({"suburb_name": "NORTH ADELAIDE", "missing_species_flag": True, "drop_table": "x", "height_min": 999})
    fake_client(monkeypatch, text=reply, calls=calls)
    result = query.translate("trees in North Adelaide with missing species")
    assert result == {"filter": {"suburb_name": "NORTH ADELAIDE", "missing_species_flag": True}, "unsupported": None}
    (call_kwargs,) = calls
    assert call_kwargs["model"] == "claude-haiku-4-5"
    assert call_kwargs["output_config"]["format"] == {"type": "json_schema", "schema": query.FILTER_SCHEMA}
    assert call_kwargs["max_tokens"] <= 400
    assert call_kwargs["messages"] == [{"role": "user", "content": "trees in North Adelaide with missing species"}]


@pytest.mark.parametrize("text", ["not json at all", "[]", "null", "", '"suburb_name"'])
def test_translate_survives_unusable_model_text(monkeypatch, text):
    fake_client(monkeypatch, text=text)
    assert query.translate("anything") == {"filter": {}, "unsupported": query.FALLBACK_MESSAGE}


def test_translate_handles_a_refusal_and_empty_content(monkeypatch):
    fake_client(monkeypatch, text=None, stop_reason="refusal")
    refused = query.translate("anything")
    assert refused["filter"] == {} and "declined" in refused["unsupported"]
    fake_client(monkeypatch, text=None)
    assert query.translate("anything") == {"filter": {}, "unsupported": query.FALLBACK_MESSAGE}
