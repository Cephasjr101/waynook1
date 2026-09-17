import logging
import os
import random
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import firebase_auth
import matching
import models
import schemas
import security
from database import Base, engine, get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("waynook")

app = FastAPI(title="waynook API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

STATIC_DIR = Path(os.getenv("STATIC_DIR", "./static"))
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# The multi-page frontend (index / marketplace / post-load / post-truck /
# dashboard / privacy / terms) is served as flat files from the repo root.
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", "./frontend"))
FRONTEND_DIR.mkdir(parents=True, exist_ok=True)

# Keep the single-file app available under /app for backwards compatibility.
legacy_dir = STATIC_DIR / "app"
legacy_dir.mkdir(parents=True, exist_ok=True)
app.mount("/app", StaticFiles(directory=str(legacy_dir)), name="app")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

bearer_scheme = HTTPBearer(auto_error=False)


# ---------- auth helpers ----------

def _unauthorized(detail="Invalid or missing authentication token"):
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise _unauthorized("Not authenticated")
    token = credentials.credentials

    # 1) Try Firebase ID token (only when Firebase is configured)
    decoded = firebase_auth.verify_firebase_token(token)
    if decoded is not None:
        email = (decoded.get("email") or "").lower()
        if not email:
            raise _unauthorized("Firebase token has no email claim")
        user = db.query(models.User).filter(models.User.email == email).first()
        if user is None:
            # Auto-provision a local user for this Firebase identity.
            role = decoded.get("role") or "shipper"
            if role not in ("shipper", "carrier"):
                role = "shipper"
            company_name = decoded.get("company_name") or email.split("@")[0]
            user = models.User(
                email=email,
                password_hash=security.hash_password(os.urandom(16).hex()),
                role=role,
                company_name=company_name,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    # 2) Fall back to local JWT
    payload = security.decode_token(token)
    if payload is None:
        raise _unauthorized()
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized()
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise _unauthorized("User no longer exists")
    return user


def require_shipper(user):
    if user.role != "shipper":
        raise HTTPException(status_code=403, detail="Shipper account required")


def require_carrier(user):
    if user.role != "carrier":
        raise HTTPException(status_code=403, detail="Carrier account required")


# ---------- auth ----------

@app.post("/auth/register", response_model=schemas.UserOut, status_code=201)
def register(body: schemas.UserCreate, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = models.User(
        email=email,
        password_hash=security.hash_password(body.password),
        role=body.role,
        company_name=body.company_name,
        owner_name=body.owner_name,
        phone=body.phone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=schemas.Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form.username.lower()).first()
    if user is None or not security.verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": security.create_token(user.id, user.role), "token_type": "bearer"}


@app.get("/me", response_model=schemas.UserOut)
def read_me(user=Depends(get_current_user)):
    return user


# ---------- loads ----------

@app.post("/loads", response_model=schemas.LoadOut, status_code=201)
def create_load(
    body: schemas.LoadCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    require_shipper(user)
    load = models.Load(shipper_id=user.id, **body.model_dump())
    db.add(load)
    db.commit()
    db.refresh(load)
    return load


@app.get("/loads", response_model=list[schemas.LoadOut])
def list_loads(
    status: str | None = None,
    equipment_type: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.Load)
    if status:
        q = q.filter(models.Load.status == status)
    if equipment_type:
        q = q.filter(models.Load.equipment_type == equipment_type)
    return q.order_by(models.Load.created_at.desc()).all()


def _get_owned_load(load_id: int, user, db: Session):
    load = db.get(models.Load, load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    if user.role != "shipper" or load.shipper_id != user.id:
        raise HTTPException(status_code=403, detail="You do not own this load")
    return load


@app.get("/loads/{load_id}", response_model=schemas.LoadOut)
def get_load(load_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return _get_owned_load(load_id, user, db)


@app.patch("/loads/{load_id}", response_model=schemas.LoadOut)
def update_load(
    load_id: int,
    body: schemas.LoadUpdate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    load = _get_owned_load(load_id, user, db)
    if load.status != "open":
        raise HTTPException(status_code=400, detail="Only open loads can be edited")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(load, field, value)
    db.commit()
    db.refresh(load)
    return load


@app.delete("/loads/{load_id}")
def delete_load(load_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    load = _get_owned_load(load_id, user, db)
    if load.status in ("in_transit", "delivered"):
        raise HTTPException(status_code=400, detail="Cannot cancel a load that is in transit or delivered")
    for offer in load.offers:
        if offer.status == "pending":
            offer.status = "rejected"
    if load.assigned_truck_id:
        truck = db.get(models.Truck, load.assigned_truck_id)
        if truck is not None:
            truck.status = "available"
    load.status = "cancelled"
    db.commit()
    return {"ok": True, "status": "cancelled"}


# ---------- trucks ----------

@app.post("/trucks", response_model=schemas.TruckOut, status_code=201)
def create_truck(
    body: schemas.TruckCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    require_carrier(user)
    truck = models.Truck(carrier_id=user.id, **body.model_dump())
    db.add(truck)
    db.commit()
    db.refresh(truck)
    return truck


@app.get("/trucks", response_model=list[schemas.TruckOut])
def list_trucks(
    status: str | None = None,
    equipment_type: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.Truck)
    if status:
        q = q.filter(models.Truck.status == status)
    if equipment_type:
        q = q.filter(models.Truck.equipment_type == equipment_type)
    return q.order_by(models.Truck.created_at.desc()).all()


def _get_owned_truck(truck_id: int, user, db: Session):
    truck = db.get(models.Truck, truck_id)
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    if user.role != "carrier" or truck.carrier_id != user.id:
        raise HTTPException(status_code=403, detail="You do not own this truck")
    return truck


@app.get("/trucks/{truck_id}", response_model=schemas.TruckOut)
def get_truck(truck_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return _get_owned_truck(truck_id, user, db)


@app.patch("/trucks/{truck_id}", response_model=schemas.TruckOut)
def update_truck(
    truck_id: int,
    body: schemas.TruckUpdate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    truck = _get_owned_truck(truck_id, user, db)
    if truck.status != "available":
        raise HTTPException(status_code=400, detail="Assigned trucks cannot be edited")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(truck, field, value)
    db.commit()
    db.refresh(truck)
    return truck


# ---------- matching ----------

@app.get("/loads/{load_id}/matches")
def get_matches(load_id: int, db: Session = Depends(get_db)):
    load = db.get(models.Load, load_id)
    if load is None or load.status == "cancelled":
        raise HTTPException(status_code=404, detail="Load not found")
    trucks = db.query(models.Truck).filter(models.Truck.status == "available").all()
    results = matching.compatible_trucks(load, trucks)
    return [
        {
            "truck": schemas.TruckOut.model_validate(r["truck"]),
            "distance_km": r["distance_km"],
            "score": r["score"],
        }
        for r in results
    ]


# ---------- offers ----------

@app.post("/loads/{load_id}/offers", response_model=schemas.OfferOut, status_code=201)
def create_offer(
    load_id: int,
    body: schemas.OfferCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    require_carrier(user)
    load = db.get(models.Load, load_id)
    if load is None or load.status == "cancelled":
        raise HTTPException(status_code=404, detail="Load not found")
    if load.status != "open":
        raise HTTPException(status_code=400, detail="Load is not open for offers")
    if body.truck_id is not None:
        truck = db.get(models.Truck, body.truck_id)
        if truck is None or truck.carrier_id != user.id:
            raise HTTPException(status_code=400, detail="Truck not found or not yours")
        if truck.status != "available":
            raise HTTPException(status_code=400, detail="That truck is not available")
    offer = models.Offer(load_id=load.id, carrier_id=user.id, truck_id=body.truck_id, amount=body.amount)
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


@app.get("/loads/{load_id}/offers", response_model=list[schemas.OfferOut])
def list_offers(load_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    load = db.get(models.Load, load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    q = db.query(models.Offer).filter(models.Offer.load_id == load.id)
    if user.role == "shipper":
        if load.shipper_id != user.id:
            raise HTTPException(status_code=403, detail="You do not own this load")
    else:
        q = q.filter(models.Offer.carrier_id == user.id)
    return q.order_by(models.Offer.created_at.desc()).all()


@app.post("/offers/{offer_id}/accept", response_model=schemas.OfferOut)
def accept_offer(offer_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_shipper(user)
    offer = db.get(models.Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    if offer.load.shipper_id != user.id:
        raise HTTPException(status_code=403, detail="You do not own this load")
    if offer.status != "pending":
        raise HTTPException(status_code=400, detail="Offer is no longer pending")
    load = offer.load
    if load.status != "open":
        raise HTTPException(status_code=400, detail="Load is no longer open")

    # Resolve the truck: the one named on the offer, else best compatible of carrier's fleet.
    truck = None
    if offer.truck_id is not None:
        truck = db.get(models.Truck, offer.truck_id)
    if truck is None or truck.status != "available":
        fleet = db.query(models.Truck).filter(models.Truck.carrier_id == offer.carrier_id).all()
        candidates = matching.compatible_trucks(load, fleet)
        truck = candidates[0]["truck"] if candidates else None
    if truck is None:
        raise HTTPException(status_code=400, detail="Carrier has no available compatible truck")

    # Atomic accept: mark offer accepted, reject other pending offers, book truck, assign load.
    offer.status = "accepted"
    for other in load.offers:
        if other.id != offer.id and other.status == "pending":
            other.status = "rejected"
    truck.status = "assigned"
    load.status = "assigned"
    load.assigned_truck_id = truck.id
    load.assigned_carrier_id = offer.carrier_id
    db.commit()
    db.refresh(offer)
    return offer


@app.post("/offers/{offer_id}/reject", response_model=schemas.OfferOut)
def reject_offer(offer_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_shipper(user)
    offer = db.get(models.Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    if offer.load.shipper_id != user.id:
        raise HTTPException(status_code=403, detail="You do not own this load")
    if offer.status != "pending":
        raise HTTPException(status_code=400, detail="Offer is no longer pending")
    offer.status = "rejected"
    db.commit()
    db.refresh(offer)
    return offer


# ---------- load lifecycle ----------

def _get_load_for_transition(load_id: int, user, db: Session, allowed: set):
    load = db.get(models.Load, load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    is_owner = user.role == "shipper" and load.shipper_id == user.id
    is_assigned_carrier = load.assigned_carrier_id is not None and user.id == load.assigned_carrier_id
    if not (is_owner or is_assigned_carrier):
        raise HTTPException(status_code=403, detail="Not authorized for this load")
    if load.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Load status '{}' does not allow this transition".format(load.status),
        )
    return load


@app.post("/loads/{load_id}/pickup", response_model=schemas.LoadOut)
def pickup_load(load_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    load = _get_load_for_transition(load_id, user, db, {"assigned"})
    load.status = "in_transit"
    db.commit()
    db.refresh(load)
    return load


@app.post("/loads/{load_id}/deliver", response_model=schemas.LoadOut)
def deliver_load(load_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    load = _get_load_for_transition(load_id, user, db, {"in_transit"})
    load.status = "delivered"
    if load.assigned_truck_id:
        truck = db.get(models.Truck, load.assigned_truck_id)
        if truck is not None:
            # Free the truck and relocate it to the destination.
            truck.status = "available"
            truck.current_lat = load.dest_lat
            truck.current_lng = load.dest_lng
    db.commit()
    db.refresh(load)
    return load


# ---------- marketplace dialect (serves the Empty-Trackload frontend) ----------

def _user_out(u) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "role": u.role,
        "companyName": u.company_name or "",
        "ownerName": u.owner_name or "",
        "phone": u.phone or "",
        "verified": u.verified or "pending",
    }


def _truck_out(t) -> dict:
    return {
        "id": t.id,
        "owner_name": t.carrier.company_name or t.carrier.owner_name or "Carrier",
        "status": t.carrier.verified or "pending",
        "truck_type": t.equipment_type,
        "from_city": t.origin_city or "",
        "to_city": t.dest_city or "",
        "capacity_kg": t.capacity_kg,
        "volume_m3": None,
        "plate_no": t.plate_no or "",
        "rate_per_km_ghs": t.rate_per_km_ghs,
        "available_from": t.available_from.isoformat(),
        "available_to": t.available_until.isoformat(),
        "current_lat": t.origin_lat,
        "current_lng": t.origin_lng,
    }


def _load_out(l) -> dict:
    return {
        "id": l.id,
        "shipper_name": l.shipper.company_name or "Shipper",
        "status": l.status,
        "description": l.description or l.title or "",
        "from_city": l.origin_city,
        "to_city": l.dest_city,
        "weight_kg": l.weight_kg,
        "volume_m3": l.volume_m3,
        "budget_ghs": l.budget_ghs,
        "pickup_date": l.pickup_time.isoformat(),
        "delivery_date": l.delivery_time.isoformat() if l.delivery_time else None,
        "equipment_type": l.equipment_type,
        "origin_lat": l.origin_lat,
        "origin_lng": l.origin_lng,
        "dest_lat": l.dest_lat,
        "dest_lng": l.dest_lng,
    }


def _match_out(m) -> dict:
    return {
        "id": m.id,
        "truck_id": m.truck_id,
        "load_id": m.load_id,
        "estimated_price_ghs": m.estimated_price_ghs,
        "commission_pct": m.commission_pct,
        "status": m.status,
        "payment_status": m.payment_status,
        "signed": bool(m.signature_name),
    }


def _city_coords(name: str | None):
    if not name:
        return None
    return matching.CITY_COORDS.get(name.strip().lower())


@app.post("/api/auth/register")
def api_register(body: schemas.ApiRegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, error="Email already registered",
                            detail="Email already registered")
    user = models.User(
        email=email,
        password_hash=security.hash_password(body.password),
        role=body.role,
        company_name=body.company,
        owner_name=body.ownerName,
        phone=body.phone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": security.create_token(user.id, user.role), "user": _user_out(user)}


@app.post("/api/auth/login")
def api_login(body: schemas.ApiLoginIn, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email.lower()).first()
    if user is None or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, error="Incorrect email or password",
                            detail="Incorrect email or password")
    return {"token": security.create_token(user.id, user.role), "user": _user_out(user)}


@app.get("/api/me")
def api_me(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return {"user": _user_out(user)}


@app.post("/api/trucks")
def api_post_truck(body: schemas.ApiTruckIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_carrier(user)
    o = _city_coords(body.fromCity)
    d = _city_coords(body.toCity)
    truck = models.Truck(
        carrier_id=user.id,
        plate_no=body.plateNo,
        equipment_type=body.truckType,
        origin_city=body.fromCity,
        origin_lat=o[0] if o else 5.6037,
        origin_lng=o[1] if o else -0.1870,
        dest_city=body.toCity,
        dest_lat=d[0] if d else None,
        dest_lng=d[1] if d else None,
        capacity_kg=body.capacityKg,
        rate_per_km_ghs=body.ratePerKmGhs,
        available_from=body.availableFrom,
        available_until=body.availableTo,
    )
    db.add(truck)
    db.commit()
    db.refresh(truck)
    return {"truck": _truck_out(truck)}


@app.get("/api/trucks")
def api_list_trucks(db: Session = Depends(get_db)):
    trucks = db.query(models.Truck).filter(models.Truck.status == "available").all()
    return {"trucks": [_truck_out(t) for t in trucks]}


@app.post("/api/loads")
def api_post_load(body: schemas.ApiLoadIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_shipper(user)
    o = _city_coords(body.fromCity)
    d = _city_coords(body.toCity)
    if o is None or d is None:
        raise HTTPException(status_code=422, detail="Unknown city. Use one of: " + ", ".join(matching.CITY_COORDS.keys()))
    load = models.Load(
        shipper_id=user.id,
        description=body.description,
        origin_city=body.fromCity,
        origin_lat=o[0],
        origin_lng=o[1],
        dest_city=body.toCity,
        dest_lat=d[0],
        dest_lng=d[1],
        equipment_type="dry_van",
        weight_kg=body.weightKg,
        volume_m3=body.volumeM3,
        budget_ghs=body.budgetGhs,
        pickup_time=body.pickupDate,
        delivery_time=body.deliveryDate,
    )
    db.add(load)
    db.commit()
    db.refresh(load)
    return {"load": _load_out(load)}


@app.get("/api/loads")
def api_list_loads(db: Session = Depends(get_db)):
    loads = db.query(models.Load).filter(models.Load.status == "open").all()
    return {"loads": [_load_out(l) for l in loads]}


@app.get("/api/pricing/estimate")
def api_estimate(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    ratePerKmGhs: float | None = None,
    weightKg: float | None = None,
    volumeM3: float | None = None,
    verified: bool | None = None,
):
    o = _city_coords(from_)
    d = _city_coords(to)
    if o is None or d is None:
        raise HTTPException(status_code=422, detail="Unknown city")
    distance_km = matching.haversine_km(o[0], o[1], d[0], d[1])
    price = matching.estimate_price_ghs(distance_km, ratePerKmGhs, weightKg)
    commission = round(price * 0.05, 2)
    return {"distance_km": round(distance_km, 1), "estimated_price_ghs": price,
            "commission_pct": 5.0, "commission_ghs": commission, "payout_ghs": round(price - commission, 2)}


@app.get("/api/matches/run")
def api_run_matching(db: Session = Depends(get_db)):
    loads = db.query(models.Load).filter(models.Load.status == "open").all()
    trucks = db.query(models.Truck).filter(models.Truck.status == "available").all()
    candidates = matching.run_marketplace_matching(loads, trucks)
    results = []
    for c in candidates[:25]:
        price = matching.estimate_price_ghs(c["distance_km"], c["truck"].rate_per_km_ghs)
        results.append({
            "truck": _truck_out(c["truck"]),
            "load": _load_out(c["load"]),
            "score": c["score"],
            "reverse": c["reverse"],
            "estimatedPriceGhs": price,
        })
    return {"candidates": results}


@app.post("/api/matches")
def api_create_match(body: schemas.MatchCreateIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    truck = db.get(models.Truck, body.truckId)
    load = db.get(models.Load, body.loadId)
    if truck is None or load is None:
        raise HTTPException(status_code=404, detail="Truck or load not found")
    distance_km = matching.haversine_km(
        load.origin_lat, load.origin_lng,
        truck.dest_lat if truck.dest_lat is not None else truck.origin_lat,
        truck.dest_lng if truck.dest_lng is not None else truck.origin_lng,
    )
    match = models.Match(
        truck_id=truck.id,
        load_id=load.id,
        proposer_id=user.id,
        estimated_price_ghs=matching.estimate_price_ghs(distance_km, truck.rate_per_km_ghs),
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return {"match": _match_out(match)}


def _own_match(match_id: int, user, db: Session):
    match = db.get(models.Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    involved = {
        match.proposer_id,
        match.truck.carrier_id,
        match.load.shipper_id,
    }
    if user.id not in involved:
        raise HTTPException(status_code=403, detail="Not a party to this match")
    return match


@app.get("/api/matches")
def api_my_matches(db: Session = Depends(get_db), user=Depends(get_current_user)):
    matches = db.query(models.Match).filter(
        (models.Match.proposer_id == user.id)
        | (models.Match.truck.has(carrier_id=user.id))
        | (models.Match.load.has(shipper_id=user.id))
    ).order_by(models.Match.created_at.desc()).all()
    return {"matches": [_match_out(m) for m in matches]}


@app.get("/api/matches/{match_id}")
def api_get_match(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    out = _match_out(match)
    out["truck"] = _truck_out(match.truck)
    out["load"] = _load_out(match.load)
    out["tracking"] = [
        {"lat": e.lat, "lng": e.lng, "note": e.note, "created_at": e.created_at.isoformat()}
        for e in match.tracking_events
    ]
    return out


@app.post("/api/matches/{match_id}/accept")
def api_accept_match(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    if match.status != "proposed":
        raise HTTPException(status_code=400, detail="Match is no longer proposed")
    # Accepting a match assigns load and books the truck, mirroring offer acceptance.
    load = match.load
    truck = match.truck
    if load.status != "open":
        raise HTTPException(status_code=400, detail="Load is no longer open")
    if truck.status != "available":
        raise HTTPException(status_code=400, detail="Truck is no longer available")
    match.status = "accepted"
    load.status = "assigned"
    load.assigned_truck_id = truck.id
    load.assigned_carrier_id = truck.carrier_id
    truck.status = "assigned"
    db.commit()
    db.refresh(match)
    return {"match": _match_out(match)}


@app.post("/api/matches/{match_id}/contract")
def api_generate_contract(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    terms = (
        "CARRIAGE AGREEMENT\n"
        "==================\n"
        "Truck: {truck} ({plate}) operated by {carrier}\n"
        "Load: {load} ({desc})\n"
        "Route: {from_city} -> {to_city}\n"
        "Agreed price: GHS {price}\n"
        "Commission: {pct}%\n\n"
        "1. The carrier agrees to transport the load from origin to destination.\n"
        "2. Payment is held in escrow and released on proof of delivery.\n"
        "3. Either party may cancel before pickup with written notice."
    ).format(
        truck=match.truck.name or "Truck #{}".format(match.truck.id),
        plate=match.truck.plate_no or "n/a",
        carrier=match.truck.carrier.company_name or match.truck.carrier.owner_name or "Carrier",
        load="Load #{}".format(match.load.id),
        desc=match.load.description or match.load.title or "freight",
        from_city=match.load.origin_city,
        to_city=match.load.dest_city,
        price=match.estimated_price_ghs,
        pct=match.commission_pct,
    )
    match.contract_text = terms
    db.commit()
    return {"contract": {"match_id": match.id, "terms_text": terms}}


@app.post("/api/matches/{match_id}/contract/sign")
def api_sign_contract(match_id: int, body: schemas.SignContractIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    if not body.signatureName or len(body.signatureName.strip()) < 3:
        raise HTTPException(status_code=422, detail="Signature name too short")
    match.signature_name = body.signatureName.strip()
    from datetime import datetime as _dt
    match.signed_at = _dt.utcnow()
    db.commit()
    return {"ok": True, "signed_by": match.signature_name}


@app.post("/api/matches/{match_id}/escrow/fund")
def api_fund_escrow(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    if match.payment_status == "escrowed":
        raise HTTPException(status_code=400, detail="Escrow already funded")
    match.payment_status = "escrowed"
    db.commit()
    return {"ok": True, "status": "escrowed"}


@app.get("/api/matches/{match_id}/payment")
def api_get_payment(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    price = match.estimated_price_ghs or 0
    commission = round(price * (match.commission_pct or 5) / 100, 2)
    return {"payment": {
        "status": match.payment_status,
        "amount_ghs": price,
        "commission_ghs": commission,
        "payout_ghs": round(price - commission, 2),
    }}


@app.post("/api/matches/{match_id}/tracking")
def api_post_tracking(match_id: int, body: schemas.TrackingIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    event = models.TrackingEvent(match_id=match.id, lat=body.lat, lng=body.lng, note=body.note)
    db.add(event)
    db.commit()
    return {"ok": True}


@app.get("/api/matches/{match_id}/tracking")
def api_get_tracking(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    events = db.query(models.TrackingEvent).filter(models.TrackingEvent.match_id == match.id).order_by(models.TrackingEvent.created_at).all()
    return {"events": [{"lat": e.lat, "lng": e.lng, "note": e.note, "created_at": e.created_at.isoformat()} for e in events]}


@app.post("/api/matches/{match_id}/pod/request-otp")
def api_request_pod_otp(match_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    otp = "{:06d}".format(random.randint(0, 999999))
    match.pod_otp = otp
    db.commit()
    return {"ok": True, "devOnlyOtp": otp}


@app.post("/api/matches/{match_id}/pod/verify")
def api_verify_pod_otp(match_id: int, body: schemas.PodVerifyIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    match = _own_match(match_id, user, db)
    stored = getattr(match, "pod_otp", None) or getattr(match, "_pod_otp", None)
    if stored is None:
        raise HTTPException(status_code=400, detail="No OTP requested yet")
    if body.otp.strip() != stored:
        raise HTTPException(status_code=400, detail="Incorrect delivery code")
    # Delivery confirmed: release escrow, complete the load, free the truck.
    match.payment_status = "released"
    load = match.load
    load.status = "delivered"
    truck = match.truck
    truck.status = "available"
    truck.current_lat = load.dest_lat
    truck.current_lng = load.dest_lng
    truck.origin_lat = load.dest_lat
    truck.origin_lng = load.dest_lng
    db.commit()
    return {"ok": True, "status": "released"}


# ---------- health & root ----------

@app.get("/health")
def health():
    index = FRONTEND_DIR / "index.html"
    return {"status": "ok", "frontend": {"dir": str(FRONTEND_DIR), "index": index.exists()}}


@app.get("/")
def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    legacy = STATIC_DIR / "index.html"
    if legacy.exists():
        return HTMLResponse(legacy.read_text(encoding="utf-8"))
    return {"message": "waynook API", "docs": "/docs", "health": "/health"}
