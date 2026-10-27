import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATABASE_PATH"] = str(
    Path(tempfile.gettempdir()) / f"creditcart-test-{os.getpid()}.db"
)

import pytest
from fastapi.testclient import TestClient
import app as creditcart

client = TestClient(creditcart.app)


@pytest.fixture(autouse=True)
def reset_test_data():
    with creditcart.db() as conn:
        conn.execute("DELETE FROM credit_ledger")
        conn.execute("DELETE FROM purchases")
        conn.execute("DELETE FROM subscriptions")
        conn.execute("DELETE FROM processed_events")
        conn.execute(
            "UPDATE users SET credits=0 WHERE id='demo-user'"
        )
    yield


def test_home_page_returns_success():
    response = client.get("/")
    assert response.status_code == 200


def test_state_returns_expected_fields():
    response = client.get("/api/state")
    assert response.status_code == 200
    assert {
        "credits", "email", "purchases", "subscriptions"
    } <= response.json().keys()


def test_use_credit_without_balance_returns_400():
    response = client.post("/api/use-credit")
    assert response.status_code == 400
    assert response.json()["detail"] == "Not enough credits"


def test_unknown_checkout_product_returns_404(monkeypatch):
    monkeypatch.setattr(creditcart, "require_stripe", lambda: None)
    response = client.post("/api/checkout/invalid")
    assert response.status_code == 404
