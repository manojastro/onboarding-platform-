"""The mock must misbehave exactly as documented, or the platform's reliability
tests prove nothing. These tests pin that behaviour down."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from mock_destination.app import create_app
from mock_destination.testing import serve

KEY = "test_key"
AUTH = {"Authorization": f"Bearer {KEY}"}


def customer(external_id: str = "C-1", **overrides: object) -> dict[str, object]:
    return {"external_id": external_id, "email": f"{external_id.lower()}@example.com", **overrides}


def order(customer_id: str, external_id: str = "O-1", **overrides: object) -> dict[str, object]:
    return {
        "external_id": external_id,
        "customer_id": customer_id,
        "order_date": "2026-04-03",
        "total_amount": "19.99",
        "currency": "USD",
        **overrides,
    }


@pytest.fixture
def app():
    return create_app(api_key=KEY, rate_limit_rps=1000, rate_limit_burst=1000)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def stats(client: TestClient) -> dict:
    return client.get("/_admin/stats").json()


def add_fault(client: TestClient, **rule: object) -> None:
    assert client.post("/_admin/faults", json={"rules": [rule]}).status_code == 201


# --- Basic contract ---------------------------------------------------------------


def test_rejects_missing_api_key(client):
    response = client.post("/v1/customers", json=customer())
    assert response.status_code == 401
    assert stats(client)["records"]["customers"] == 0


def test_create_then_find_by_external_id(client):
    created = client.post("/v1/customers", json=customer("C-7"), headers=AUTH)
    assert created.status_code == 201
    assert created.json()["id"].startswith("cus_")

    found = client.get("/v1/customers", params={"external_id": "C-7"}, headers=AUTH).json()["data"]
    assert [r["id"] for r in found] == [created.json()["id"]]


def test_unknown_fields_are_rejected(client):
    response = client.post("/v1/customers", json=customer(favourite_colour="blue"), headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_invalid_email_is_rejected(client):
    response = client.post("/v1/customers", json=customer(email="not-an-email"), headers=AUTH)
    assert response.status_code == 400
    assert "email" in response.json()["error"]["message"]


def test_update_changes_record_and_bumps_version(client):
    created = client.post("/v1/customers", json=customer(), headers=AUTH).json()
    updated = client.patch(f"/v1/customers/{created['id']}", json={"first_name": "Ann"}, headers=AUTH)
    assert updated.status_code == 200
    assert updated.json()["first_name"] == "Ann"
    assert updated.json()["version"] == 2


# --- Money discipline at the boundary -----------------------------------------------


def test_money_sent_as_json_number_is_rejected(client):
    cust = client.post("/v1/customers", json=customer(), headers=AUTH).json()
    response = client.post("/v1/orders", json=order(cust["id"], total_amount=19.99), headers=AUTH)
    assert response.status_code == 400
    assert "decimal string" in response.json()["error"]["message"]


@pytest.mark.parametrize(("amount", "currency"), [("10.005", "USD"), ("1000.5", "JPY")])
def test_more_decimals_than_the_currency_allows_is_rejected(client, amount, currency):
    cust = client.post("/v1/customers", json=customer(), headers=AUTH).json()
    response = client.post("/v1/orders", json=order(cust["id"], total_amount=amount, currency=currency),
                           headers=AUTH)
    assert response.status_code == 400
    assert "decimal places" in response.json()["error"]["message"]


def test_order_for_unknown_customer_is_rejected(client):
    response = client.post("/v1/orders", json=order("cus_does_not_exist"), headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "resource_missing"
    assert stats(client)["records"]["orders"] == 0


# --- Idempotency --------------------------------------------------------------------


def test_same_key_same_body_replays_original_and_writes_once(client):
    headers = {**AUTH, "Idempotency-Key": "key-1"}
    first = client.post("/v1/customers", json=customer(), headers=headers)
    second = client.post("/v1/customers", json=customer(), headers=headers)

    assert first.status_code == second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert stats(client)["records"]["customers"] == 1
    assert stats(client)["idempotent_replays"] == 1


def test_same_key_different_body_is_refused(client):
    headers = {**AUTH, "Idempotency-Key": "key-1"}
    client.post("/v1/customers", json=customer(), headers=headers)
    reused = client.post("/v1/customers", json=customer(first_name="Changed"), headers=headers)

    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    assert stats(client)["records"]["customers"] == 1


def test_blind_retry_without_a_key_creates_a_duplicate(client):
    """The destination does not protect careless clients. Zero duplicates has to be earned."""
    client.post("/v1/customers", json=customer("C-1"), headers=AUTH)
    client.post("/v1/customers", json=customer("C-1"), headers=AUTH)

    snapshot = stats(client)
    assert snapshot["records"]["customers"] == 2
    assert snapshot["duplicate_external_ids"]["customers"] == {"C-1": 2}
    assert snapshot["duplicate_count"] == 1


# --- Rate limiting -----------------------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(client):
    client.put("/_admin/rate-limit", json={"requests_per_second": 0.5, "burst": 2})
    statuses = [client.get("/v1/customers", headers=AUTH).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    limited = client.get("/v1/customers", headers=AUTH)
    assert int(limited.headers["Retry-After"]) >= 1


# --- Fault injection -------------------------------------------------------------------------


def test_injected_500_writes_nothing_and_does_not_poison_the_key(client):
    add_fault(client, action="error_500", method="POST", path_prefix="/v1/customers")
    headers = {**AUTH, "Idempotency-Key": "key-1"}

    failed = client.post("/v1/customers", json=customer(), headers=headers)
    assert failed.status_code == 500
    assert stats(client)["records"]["customers"] == 0

    retried = client.post("/v1/customers", json=customer(), headers=headers)
    assert retried.status_code == 201
    assert "Idempotent-Replayed" not in retried.headers
    assert stats(client)["records"]["customers"] == 1


def test_fault_can_skip_the_first_n_matching_requests(client):
    add_fault(client, action="error_503", method="POST", after_requests=2, times=1)
    statuses = [
        client.post("/v1/customers", json=customer(f"C-{i}"), headers=AUTH).status_code for i in range(4)
    ]
    assert statuses == [201, 201, 503, 201]


def test_injected_429_carries_retry_after(client):
    add_fault(client, action="rate_limit_429", times=1)
    response = client.post("/v1/customers", json=customer(), headers=AUTH)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    assert stats(client)["records"]["customers"] == 0


def test_reset_clears_records_faults_and_counters(client):
    client.post("/v1/customers", json=customer(), headers=AUTH)
    add_fault(client, action="error_500")
    client.post("/_admin/reset")

    snapshot = stats(client)
    assert snapshot["records"] == {"customers": 0, "orders": 0}
    assert snapshot["requests_total"] == 0
    assert client.get("/_admin/faults").json()["rules"] == []


# --- Real sockets: the timeout cases ---------------------------------------------------------


def test_timeout_after_commit_lands_the_write_and_a_keyed_replay_is_safe(app):
    with serve(app) as base:
        httpx.post(f"{base}/_admin/faults", json={"rules": [{"action": "timeout_after_commit", "delay_s": 1.5}]})
        with httpx.Client(base_url=base, headers=AUTH, timeout=0.3) as c:
            with pytest.raises(httpx.ReadTimeout):
                c.post("/v1/customers", json=customer("C-9"), headers={"Idempotency-Key": "key-9"})

            # The client cannot tell whether the write landed. The destination can.
            found = c.get("/v1/customers", params={"external_id": "C-9"}).json()["data"]
            assert len(found) == 1

            replay = c.post("/v1/customers", json=customer("C-9"), headers={"Idempotency-Key": "key-9"})
            assert replay.status_code == 201
            assert replay.headers["Idempotent-Replayed"] == "true"
            assert replay.json()["id"] == found[0]["id"]

        snapshot = httpx.get(f"{base}/_admin/stats").json()
        assert snapshot["records"]["customers"] == 1
        assert snapshot["duplicate_count"] == 0


def test_timeout_before_commit_writes_nothing(app):
    with serve(app) as base:
        httpx.post(f"{base}/_admin/faults", json={"rules": [{"action": "timeout_before_commit", "delay_s": 1.0}]})
        with httpx.Client(base_url=base, headers=AUTH, timeout=0.3) as c:
            with pytest.raises(httpx.ReadTimeout):
                c.post("/v1/customers", json=customer("C-5"), headers={"Idempotency-Key": "key-5"})
            assert c.get("/v1/customers", params={"external_id": "C-5"}).json()["data"] == []
