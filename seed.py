from datetime import datetime, timedelta

import models
import security
from database import Base, SessionLocal, engine


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(models.User).filter(models.User.email == "shipper@demo.io").first():
            print("Database already seeded.")
            return

        shipper = models.User(
            email="shipper@demo.io",
            password_hash=security.hash_password("password123"),
            role="shipper",
            company_name="Acme Manufacturing",
        )
        carrier = models.User(
            email="carrier@demo.io",
            password_hash=security.hash_password("password123"),
            role="carrier",
            company_name="FastHaul Logistics",
        )
        db.add_all([shipper, carrier])
        db.flush()

        now = datetime.utcnow()
        loads = [
            models.Load(
                shipper_id=shipper.id,
                title="Steel coils",
                origin_city="Chicago, IL",
                origin_lat=41.8781,
                origin_lng=-87.6298,
                dest_city="Columbus, OH",
                dest_lat=39.9612,
                dest_lng=-82.9988,
                equipment_type="flatbed",
                weight_kg=22000,
                pickup_time=now + timedelta(days=2),
            ),
            models.Load(
                shipper_id=shipper.id,
                title="Frozen produce",
                origin_city="Chicago, IL",
                origin_lat=41.8781,
                origin_lng=-87.6298,
                dest_city="Indianapolis, IN",
                dest_lat=39.7684,
                dest_lng=-86.1581,
                equipment_type="reefer",
                weight_kg=18000,
                pickup_time=now + timedelta(days=3),
            ),
            models.Load(
                shipper_id=shipper.id,
                title="Palletized retail goods",
                origin_city="Columbus, OH",
                origin_lat=39.9612,
                origin_lng=-82.9988,
                dest_city="Nashville, TN",
                dest_lat=36.1627,
                dest_lng=-86.7816,
                equipment_type="dry_van",
                weight_kg=15000,
                pickup_time=now + timedelta(days=4),
            ),
        ]
        trucks = [
            models.Truck(
                carrier_id=carrier.id,
                name="FL-101",
                equipment_type="flatbed",
                capacity_kg=24000,
                current_lat=41.72,
                current_lng=-87.54,
                available_from=now,
                available_until=now + timedelta(days=6),
            ),
            models.Truck(
                carrier_id=carrier.id,
                name="RF-202",
                equipment_type="reefer",
                capacity_kg=20000,
                current_lat=41.9,
                current_lng=-87.68,
                available_from=now,
                available_until=now + timedelta(days=5),
            ),
            models.Truck(
                carrier_id=carrier.id,
                name="DV-303",
                equipment_type="dry_van",
                capacity_kg=16000,
                current_lat=39.95,
                current_lng=-82.9,
                available_from=now,
                available_until=now + timedelta(days=7),
            ),
        ]
        db.add_all(loads + trucks)
        db.commit()
        print("Seeded demo data.")
        print("  shipper@demo.io / password123  (Acme Manufacturing)")
        print("  carrier@demo.io / password123  (FastHaul Logistics)")
    finally:
        db.close()


if __name__ == "__main__":
    run()
