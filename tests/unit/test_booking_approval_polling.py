"""Buyer follow-up on a booking the approval gate holds.

A held booking answers 202 ``pending_approval`` with an ``approval_id``. The
202 now names a buyer-scoped status resource in ``Location`` (with
``Retry-After``), and ``GET /api/v1/deals/approvals/{approval_id}`` reports
the decision and, once the re-submitted booking has minted it, the
``deal_id``. Only the API key the booking was held for can read it; every
other caller gets 404.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.events.approval import ApprovalGate
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.api_key import ApiKeyRole
from ad_seller.models.buyer_identity import BuyerIdentity
from tests.unit.test_quote_booking_approval_gate import (
    QUOTE_ID,
    TEMPLATE_BODY,
    _catalog,
    _quote,
    _settings,
)

GATED = {"approval_required_above_value": 250_000}


@pytest.fixture
def storage():
    store = {f"quote:{QUOTE_ID}": _quote()}
    s = AsyncMock()
    s.get = AsyncMock(side_effect=lambda k: store.get(k))
    s.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    s.get_quote = AsyncMock(side_effect=lambda q: store.get(f"quote:{q}"))
    s.set_quote = AsyncMock(side_effect=lambda q, d, ttl=86400: store.__setitem__(f"quote:{q}", d))
    s.get_deal = AsyncMock(side_effect=lambda d: store.get(f"deal:{d}"))
    s.set_deal = AsyncMock(side_effect=lambda d, data: store.__setitem__(f"deal:{d}", data))
    s.get_negotiation = AsyncMock(side_effect=lambda k: store.get(f"negotiation:{k}"))
    s._store = store
    return s


def _as_key(key_id):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: SimpleNamespace(
        key_id=key_id,
        role=ApiKeyRole.BUYER,
        identity=BuyerIdentity(agency_id="agency-1", agency_name="Agency One"),
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _call(storage, method, path, *, key_id="key-buyer-a", settings=None, **kwargs):
    if key_id is None:
        app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    else:
        _as_key(key_id)
    with (
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.config.get_settings", return_value=settings or _settings(**GATED)),
        patch("ad_seller.events.helpers.emit_event", new_callable=AsyncMock),
        patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_catalog(),
        ),
    ):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, **kwargs)


async def _book(storage, key_id="key-buyer-a", idem="idem-poll-1"):
    return await _call(
        storage,
        "POST",
        "/api/v1/deals",
        key_id=key_id,
        json={"idempotency_key": idem, "quote_id": QUOTE_ID},
    )


async def _poll(storage, approval_id, key_id="key-buyer-a"):
    return await _call(storage, "GET", f"/api/v1/deals/approvals/{approval_id}", key_id=key_id)


class TestHeldBookingResponse:
    async def test_202_names_the_status_resource(self, storage):
        resp = await _book(storage)

        assert resp.status_code == 202, resp.text
        approval_id = resp.json()["approval_id"]
        assert resp.headers["location"] == f"/api/v1/deals/approvals/{approval_id}"
        assert resp.headers["retry-after"] == "60"
        assert resp.json()["status_url"] == resp.headers["location"]

    async def test_template_202_names_the_status_resource(self, storage):
        resp = await _call(
            storage,
            "POST",
            "/api/v1/deals/from-template",
            settings=_settings(approval_required_above_value=1000),
            json=TEMPLATE_BODY,
        )

        assert resp.status_code == 202, resp.text
        approval_id = resp.json()["approval_id"]
        assert resp.headers["location"] == f"/api/v1/deals/approvals/{approval_id}"
        assert resp.headers["retry-after"] == "60"


class TestPollingAccess:
    async def test_requesting_key_sees_pending(self, storage):
        approval_id = (await _book(storage)).json()["approval_id"]

        resp = await _poll(storage, approval_id)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["next_action"] == "poll"
        assert body["deal_id"] is None
        assert body["quote_id"] == QUOTE_ID
        assert resp.headers["retry-after"] == "60"

    async def test_other_key_gets_404(self, storage):
        approval_id = (await _book(storage)).json()["approval_id"]

        resp = await _poll(storage, approval_id, key_id="key-buyer-b")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "approval_not_found"

    async def test_unknown_approval_is_the_same_404(self, storage):
        await _book(storage)

        resp = await _poll(storage, "no-such-approval")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "approval_not_found"

    async def test_anonymous_gets_401(self, storage):
        approval_id = (await _book(storage)).json()["approval_id"]

        resp = await _poll(storage, approval_id, key_id=None)

        assert resp.status_code == 401

    async def test_non_booking_approval_is_404_even_if_registered(self, storage):
        from ad_seller.services import approval_service

        req = await ApprovalGate(storage).request_approval(
            flow_id="proposal-1",
            flow_type="proposal_handling",
            gate_name="proposal_decision",
            context={"proposal_id": "prop-1"},
            flow_state_snapshot={},
        )
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            await approval_service.record_booking_requester(req.approval_id, "key-buyer-a")

        resp = await _poll(storage, req.approval_id)

        assert resp.status_code == 404

    async def test_second_key_that_resubmits_can_read_it(self, storage):
        first = (await _book(storage)).json()
        second = (await _book(storage, key_id="key-buyer-a2", idem="idem-poll-2")).json()
        assert second["approval_id"] == first["approval_id"]

        resp = await _poll(storage, first["approval_id"], key_id="key-buyer-a2")

        assert resp.status_code == 200
        assert storage._store[f"approval_requesters:{first['approval_id']}"] == [
            "key-buyer-a",
            "key-buyer-a2",
        ]


class TestDecisions:
    async def test_approved_then_booked_reports_the_deal(self, storage):
        approval_id = (await _book(storage)).json()["approval_id"]
        await ApprovalGate(storage).submit_decision(approval_id, "approve", "operator")

        approved = (await _poll(storage, approval_id)).json()
        assert approved["status"] == "approved"
        assert approved["next_action"] == "resubmit_booking"
        assert approved["deal_id"] is None
        assert approved["decided_at"]
        approved_resp = await _poll(storage, approval_id)
        assert "retry-after" not in approved_resp.headers

        booked = await _book(storage)
        assert booked.status_code == 200, booked.text
        deal_id = booked.json()["deal"]["deal_id"]

        after = (await _poll(storage, approval_id)).json()
        assert after["status"] == "approved"
        assert after["deal_id"] == deal_id
        assert after["next_action"] == "none"

    async def test_rejected_hides_the_operator_reason(self, storage):
        approval_id = (await _book(storage)).json()["approval_id"]
        await ApprovalGate(storage).submit_decision(
            approval_id, "reject", "operator", reason="below the internal floor of 39.50"
        )

        resp = await _poll(storage, approval_id)

        body = resp.json()
        assert body["status"] == "rejected"
        assert body["next_action"] == "none"
        assert body["deal_id"] is None
        assert "39.50" not in resp.text
        assert "gross_value" not in resp.text

    async def test_timed_out_reads_as_expired(self, storage):
        from ad_seller.events.models import ApprovalStatus

        approval_id = (await _book(storage)).json()["approval_id"]
        record = storage._store[f"approval:{approval_id}"]
        record["status"] = ApprovalStatus.TIMED_OUT.value
        record["expires_at"] = (datetime.utcnow() - timedelta(hours=1)).isoformat()

        body = (await _poll(storage, approval_id)).json()

        assert body["status"] == "expired"
        assert body["next_action"] == "none"

    async def test_template_booking_reports_the_consumed_deal(self, storage):
        settings = _settings(approval_required_above_value=1000)

        async def template():
            return await _call(
                storage,
                "POST",
                "/api/v1/deals/from-template",
                settings=settings,
                json=TEMPLATE_BODY,
            )

        approval_id = (await template()).json()["approval_id"]
        await ApprovalGate(storage).submit_decision(approval_id, "approve", "operator")
        booked = await template()
        assert booked.status_code == 201, booked.text

        body = (await _poll(storage, approval_id)).json()

        assert body["status"] == "approved"
        assert body["deal_id"] == booked.json()["deal_id"]
        assert body["next_action"] == "none"
