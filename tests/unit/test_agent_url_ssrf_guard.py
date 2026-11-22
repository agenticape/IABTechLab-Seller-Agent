"""Buyer-supplied ``agent_url`` fetches are guarded against SSRF.

Any request carrying an ``agent_url`` (``/discovery``, ``/proposals``,
``/api/v1/quotes``, ...) made the seller GET
``{agent_url}/.well-known/agent.json`` with no checks and a 15 s timeout,
so an anonymous caller could point it at loopback, the private network
or a cloud metadata endpoint. The fetch now requires ``https`` and a host
that resolves only to public addresses, does not follow redirects, and
uses a short configurable timeout.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_seller.clients.agent_registry_client import agent_url_fetch_violation, fetch_agent_card
from ad_seller.config import get_settings

PUBLIC_IP = ["93.184.215.14"]


def _resolver(addresses):
    return patch(
        "ad_seller.clients.agent_registry_client._resolve_host",
        new=AsyncMock(return_value=addresses),
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://buyer.example.com",
        "ftp://buyer.example.com",
        "file:///etc/passwd",
        "https://user:pw@buyer.example.com",
        "https://",
        "https://127.0.0.1",
        "https://[::1]",
        "https://10.0.0.5",
        "https://192.168.1.10:8443",
        "https://169.254.169.254",
        "https://[fd00::1]",
        "https://0.0.0.0",
    ],
)
async def test_unsafe_urls_are_refused(url):
    assert await agent_url_fetch_violation(url) is not None


@pytest.mark.parametrize("resolved", [["127.0.0.1"], ["93.184.215.14", "10.1.2.3"], ["fe80::1"]])
async def test_hostnames_resolving_to_internal_addresses_are_refused(resolved):
    with _resolver(resolved):
        assert await agent_url_fetch_violation("https://buyer.example.com") is not None


async def test_unresolvable_host_is_refused():
    with patch(
        "ad_seller.clients.agent_registry_client._resolve_host",
        new=AsyncMock(side_effect=OSError("no such host")),
    ):
        assert await agent_url_fetch_violation("https://nope.invalid") is not None


async def test_public_https_host_is_allowed():
    with _resolver(PUBLIC_IP):
        assert await agent_url_fetch_violation("https://buyer.example.com") is None


async def test_private_networks_can_be_allowed_for_local_dev():
    assert await agent_url_fetch_violation("http://localhost:9000", allow_private=True) is None
    assert await agent_url_fetch_violation("ftp://x", allow_private=True) is not None


async def test_refused_url_is_never_requested():
    with patch("ad_seller.clients.agent_registry_client.httpx.AsyncClient") as client_cls:
        assert await fetch_agent_card("https://169.254.169.254/latest/meta-data") is None
    client_cls.assert_not_called()


async def test_fetch_uses_short_timeout_and_no_redirects(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_card_fetch_timeout_seconds", 2.5)
    response = MagicMock(status_code=302)
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    with (
        _resolver(PUBLIC_IP),
        patch(
            "ad_seller.clients.agent_registry_client.httpx.AsyncClient", return_value=client
        ) as client_cls,
    ):
        assert await fetch_agent_card("https://buyer.example.com") is None

    client_cls.assert_called_once_with(timeout=2.5, follow_redirects=False)


async def test_discovery_with_metadata_agent_url_makes_no_request():
    from httpx import ASGITransport

    from ad_seller.interfaces.api.main import _get_optional_api_key_record, app

    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            with patch("ad_seller.clients.agent_registry_client.httpx.AsyncClient") as client_cls:
                resp = await c.post(
                    "/discovery",
                    json={"query": "ctv", "agent_url": "http://169.254.169.254"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["access_tier"] == "public"
    client_cls.assert_not_called()
