import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_loadmatch.db"
os.environ.setdefault("STATIC_DIR", "./test_static")

import pytest
from fastapi.testclient import TestClient

import main
from database import Base, engine

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def auth_headers(email, role, company="Test Co"):
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "role": role, "company_name": company},
    )
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", data={"username": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def demo_load(pickup_days=1):
    pickup = (datetime.utcnow() + timedelta(days=pickup_days)).isoformat()
    return {
        "title": "Test freight",
        "origin_city": "Chicago, IL",
        "origin_lat": 41.8781,
        "origin_lng": -87.6298,
        "dest_city": "Columbus, OH",
        "dest_lat": 39.9612,
        "dest_lng": -82.9988,
        "equipment_type": "flatbed",
        "weight_kg": 20000,
        "pickup_time": pickup,
    }


def demo_truck(pickup_days=1):
    now = datetime.utcnow()
    return {
        "name": "T-1",
        "equipment_type": "flatbed",
        "capacity_kg": 24000,
        "current_lat": 41.75,
        "current_lng": -87.55,
        "available_from": now.isoformat(),
        "available_until": (now + timedelta(days=pickup_days + 3)).isoformat(),
    }


def test_register_login_me():
    headers = auth_headers("a@example.com", "shipper")
    r = client.get("/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"


def test_duplicate_registration_rejected():
    auth_headers("dup@example.com", "carrier")
    r = client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "password123", "role": "carrier"},
    )
    assert r.status_code == 409


def test_full_match_accept_deliver_flow():
    shipper = auth_headers("s@example.com", "shipper")
    carrier = auth_headers("c@example.com", "carrier")

    r = client.post("/loads", headers=shipper, json=demo_load())
    assert r.status_code == 201, r.text
    load_id = r.json()["id"]

    r = client.post("/trucks", headers=carrier, json=demo_truck())
    assert r.status_code == 201, r.text
    truck_id = r.json()["id"]

    r = client.get("/loads/{}/matches".format(load_id))
    assert r.status_code == 200
    matches = r.json()
    assert len(matches) == 1
    assert matches[0]["truck"]["id"] == truck_id

    r = client.post("/loads/{}/offers".format(load_id), headers=carrier, json={"amount": 1500, "truck_id": truck_id})
    assert r.status_code == 201, r.text
    offer_id = r.json()["id"]

    r = client.post("/offers/{}/accept".format(offer_id), headers=shipper)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    r = client.get("/loads/{}".format(load_id), headers=shipper)
    assert r.json()["status"] == "assigned"

    r = client.post("/loads/{}/pickup".format(load_id), headers=carrier)
    assert r.status_code == 200
    assert r.json()["status"] == "in_transit"

    r = client.post("/loads/{}/deliver".format(load_id), headers=shipper)
    assert r.status_code == 200
    assert r.json()["status"] == "delivered"

    r = client.get("/trucks/{}".format(truck_id), headers=carrier)
    truck = r.json()
    assert truck["status"] == "available"
    assert truck["current_lat"] == 39.9612  # relocated to destination


def test_carrier_cannot_post_load():
    carrier = auth_headers("c2@example.com", "carrier")
    r = client.post("/loads", headers=carrier, json=demo_load())
    assert r.status_code == 403
