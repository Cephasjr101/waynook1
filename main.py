import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

import firebase_auth
import matching
import models
import schemas
import security
from database import Base, engine, get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loadmatch")

app = FastAPI(title="LoadMatch API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

STATIC_DIR = Path(os.getenv("STATIC_DIR", "./static"))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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


# ---------- JSON auth models (frontend contract) ----------

class RegisterBody(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "shipper"              # or "carrier" for drivers


class LoginBody(BaseModel):
    email: EmailStr
    password: str


# ---------- auth ----------

@app.post("/auth/register", status_code=201)
def register(body: RegisterBody, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    role = body.role if body.role in ("shipper", "carrier") else "shipper"
    user = models.User(
        email=email,
        password_hash=security.hash_password(body.password),
        role=role,
        company_name=body.name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {
        "access_token": security.create_token(user.id, user.role),
        "token_type": "bearer",
        "email": user.email,
        "name": user.company_name,
    }


@app.post("/auth/login", response_model=schemas.Token)
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email.lower()).first()
    if user is None or not security.verify_password(body.password, user.password_hash):
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
    trucks = db.query(models.Truck).filter(models.Truck.owner_id == owner_id).all()
@app.get("/loads/{load_id}/matches")
def get_matches(load_id: int, db: Session = Depends(get_db)):
    load = db.get(models.Load, load_id)
    if load is None or load.status == "cancelled":
        raise HTTPException(status_code=404, detail="Load not found")
    trucks = db.query(models.Truck).all()
    results = matching.compatible_trucks(load, trucks)
    return [
        {
            "truck": schemas.TruckOut.model_validate(r["truck"]),
            "distance_km": r["distance_km"],
            "score": r["score"],
        }
        for r in results
    ]
