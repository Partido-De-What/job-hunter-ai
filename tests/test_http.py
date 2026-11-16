"""Polite client: robots.txt, retries, delays, identification."""

import pytest
from fakeweb import FakeWeb

from vjh.sources.http import FetchError, RobotsDisallowed


def test_robots_disallow_blocks_without_requesting_page():
    web = FakeWeb({"https://ex.at/robots.txt": "User-agent: *\nDisallow: /jobs"})
    client, _ = web.client()
    with pytest.raises(RobotsDisallowed):
        client.get("https://ex.at/jobs/1")
    assert web.page_requests() == []


def test_robots_wildcard_rule_is_respected():
    web = FakeWeb({"https://ex.at/robots.txt": "User-agent: *\nDisallow: /*?search=",
                   "https://ex.at/jobs": "ok"})
    client, _ = web.client()
    assert client.allowed("https://ex.at/jobs")
    assert not client.allowed("https://ex.at/jobs?search=x")


def test_missing_robots_means_allowed():
    web = FakeWeb({"https://ex.at/jobs": "hello"})
    client, _ = web.client()
    assert client.get("https://ex.at/jobs").text == "hello"


def test_unreadable_robots_means_do_not_fetch():
    web = FakeWeb({"https://ex.at/robots.txt": (503, "down", {})})
    client, _ = web.client(max_retries=0)
    with pytest.raises(RobotsDisallowed):
        client.get("https://ex.at/jobs")
    assert web.page_requests() == []


def test_retry_after_429_then_success():
    answers = iter([(429, "slow down", {"Retry-After": "5"}), "finally"])
    web = FakeWeb({"https://ex.at/jobs": lambda: next(answers)})
    client, sleeps = web.client(max_retries=2)
    assert client.get("https://ex.at/jobs").text == "finally"
    assert 5 in sleeps


def test_gives_up_after_max_retries():
    web = FakeWeb({"https://ex.at/jobs": (500, "boom", {})})
    client, _ = web.client(max_retries=1)
    with pytest.raises(FetchError, match="500"):
        client.get("https://ex.at/jobs")
    assert web.page_requests().count("https://ex.at/jobs") == 2


def test_waits_between_requests_to_same_host():
    web = FakeWeb({"https://ex.at/a": "a", "https://ex.at/b": "b"})
    client, sleeps = web.client(min_delay_seconds=3)
    client.get("https://ex.at/a")
    client.get("https://ex.at/b")
    assert any(s > 0 for s in sleeps)


def test_user_agent_identifies_without_personal_data():
    from vjh.models.settings import CollectionSettings
    ua = CollectionSettings().user_agent
    assert "@" not in ua and "robots" in ua
