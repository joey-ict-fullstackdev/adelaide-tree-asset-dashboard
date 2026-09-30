"""Rate limiter tests for web/api/query.py with a fake Redis: no network, no Upstash account."""
import importlib.util
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("query", ROOT / "web" / "api" / "query.py")
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)

NOW = datetime(2026, 10, 1, 14, 30, 15, tzinfo=timezone.utc)
IP_A, IP_B = "203.0.113.7", "198.51.100.4"


class FakeRedis:
    """Counts INCR calls and remembers the first EXPIRE per key, like Redis with EXPIRE ... NX."""

    def __init__(self):
        self.counts, self.ttl, self.calls = {}, {}, []

    def __call__(self, commands):
        self.calls.append(commands)
        results = []
        for command, key, *rest in commands:
            if command == "INCR":
                self.counts[key] = self.counts.get(key, 0) + 1
                results.append(self.counts[key])
            elif command == "EXPIRE":
                seconds, flag = rest
                assert flag == "NX", "EXPIRE must use NX so repeated hits cannot extend the window"
                results.append(0 if key in self.ttl else 1)
                self.ttl.setdefault(key, seconds)
            else:
                raise AssertionError(command)
        return results


@pytest.fixture()
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(query, "redis_multi_exec", fake)
    monkeypatch.setattr(query, "LIMIT_HOUR", 3)
    monkeypatch.setattr(query, "LIMIT_DAY", 5)
    monkeypatch.setattr(query, "LIMIT_GLOBAL_DAY", 6)
    return fake


def at(hour, minute=0, second=0):
    return NOW.replace(hour=hour, minute=minute, second=second)


def test_allows_up_to_the_hourly_limit_then_blocks(redis):
    for _ in range(3):
        assert query.rate_limit_check(IP_A, NOW) is None
    message, retry_after = query.rate_limit_check(IP_A, NOW)
    assert "3 searches per hour" in message and "5 per day" in message
    assert retry_after == 29 * 60 + 45  # seconds until 15:00:00


def test_daily_limit_blocks_across_hours(redis):
    for hour in range(5):  # one search in each of five different hours
        assert query.rate_limit_check(IP_A, at(hour)) is None
    message, retry_after = query.rate_limit_check(IP_A, at(10, 0, 0))
    assert "per day" in message
    assert retry_after == 14 * 3600  # until 00:00 UTC the next day


def test_global_limit_blocks_every_ip(redis):
    ips = [f"192.0.2.{n}" for n in range(1, 8)]
    assert [query.rate_limit_check(ip, NOW) for ip in ips[:6]] == [None] * 6
    message, retry_after = query.rate_limit_check(ips[6], NOW)
    assert "daily search limit" in message
    assert retry_after == int((NOW.replace(hour=0, minute=0, second=0) + timedelta(days=1) - NOW).total_seconds())


def test_a_blocked_ip_does_not_burn_the_global_counter(redis):
    for _ in range(3):
        assert query.rate_limit_check(IP_A, NOW) is None
    for _ in range(10):
        assert query.rate_limit_check(IP_A, NOW) is not None
    global_key = next(k for k in redis.counts if k.startswith("rl:g:"))
    assert redis.counts[global_key] == 3


def test_ips_are_counted_separately(redis):
    for _ in range(3):
        query.rate_limit_check(IP_A, NOW)
    assert query.rate_limit_check(IP_A, NOW) is not None
    assert query.rate_limit_check(IP_B, NOW) is None


def test_hourly_counter_resets_in_the_next_hour(redis):
    for _ in range(3):
        query.rate_limit_check(IP_A, NOW)
    assert query.rate_limit_check(IP_A, NOW) is not None
    assert query.rate_limit_check(IP_A, at(15, 0, 0)) is None


def test_keys_use_a_hash_never_the_raw_ip_and_expire(redis):
    query.rate_limit_check(IP_A, NOW)
    assert redis.counts, "no counters were written"
    for key in redis.counts:
        assert IP_A not in key and "203" not in key
    hour_key = next(k for k in redis.counts if k.startswith("rl:h:"))
    day_key = next(k for k in redis.counts if k.startswith("rl:d:"))
    global_key = next(k for k in redis.counts if k.startswith("rl:g:"))
    assert hour_key.endswith(":2026100114") and day_key.endswith(":20261001") and global_key == "rl:g:20261001"
    assert (redis.ttl[hour_key], redis.ttl[day_key], redis.ttl[global_key]) == (3660, 86460, 86460)


def test_a_redis_failure_propagates_so_the_handler_can_fail_closed(monkeypatch):
    def down(commands):
        raise query.RedisError("timeout")

    monkeypatch.setattr(query, "redis_multi_exec", down)
    with pytest.raises(query.RedisError):
        query.rate_limit_check(IP_A, NOW)


@pytest.mark.parametrize(
    "headers, peer, expected",
    [
        ({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}, "127.0.0.1", "203.0.113.7"),
        ({"x-forwarded-for": "2001:db8::1"}, "127.0.0.1", "2001:db8::1"),
        ({"x-forwarded-for": "garbage", "x-real-ip": "198.51.100.4"}, "127.0.0.1", "198.51.100.4"),
        ({"x-forwarded-for": "' OR 1=1 --"}, "192.0.2.9", "192.0.2.9"),
        ({"x-forwarded-for": ""}, "192.0.2.9", "192.0.2.9"),
        ({}, "192.0.2.9", "192.0.2.9"),
        ({}, "", "unknown"),
        ({}, None, "unknown"),
    ],
)
def test_client_ip_prefers_the_forwarded_address_and_ignores_garbage(headers, peer, expected):
    assert query.client_ip(headers, peer) == expected


class FakeResponse:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self.body


@pytest.fixture()
def upstash_env(monkeypatch):
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example-db.upstash.io/")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "tok-SECRET-123")


def test_multi_exec_sends_the_commands_and_returns_the_results(monkeypatch, upstash_env):
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, auth=request.get_header("Authorization"), body=json.loads(request.data), timeout=timeout)
        return FakeResponse([{"result": 1}, {"result": 0}])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    commands = [["INCR", "k"], ["EXPIRE", "k", 60, "NX"]]
    assert query.redis_multi_exec(commands) == [1, 0]
    assert seen == {"url": "https://example-db.upstash.io/multi-exec", "auth": "Bearer tok-SECRET-123", "body": commands, "timeout": 3}


@pytest.mark.parametrize(
    "outcome",
    [
        urllib.error.URLError("failed for tok-SECRET-123"),
        TimeoutError("timed out"),
        FakeResponse(b"not json"),
        FakeResponse({"error": "ERR bad"}),
        FakeResponse([{"error": "ERR bad"}, {"result": 1}]),
        FakeResponse([{"result": 1}]),
        FakeResponse("nope"),
    ],
)
def test_multi_exec_failures_raise_a_safe_error(monkeypatch, upstash_env, outcome):
    def fake_urlopen(request, timeout):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(query.RedisError) as info:
        query.redis_multi_exec([["INCR", "k"], ["EXPIRE", "k", 60, "NX"]])
    assert "tok-SECRET-123" not in str(info.value) + repr(info.value)
    assert info.value.__cause__ is None


@pytest.mark.parametrize("missing", ["UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"])
def test_multi_exec_without_configuration_raises(monkeypatch, upstash_env, missing):
    monkeypatch.delenv(missing)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not call the network"))
    with pytest.raises(query.RedisError):
        query.redis_multi_exec([["INCR", "k"]])
