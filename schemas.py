from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------- users & auth ----------

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(pattern="^(shipper|carrier)$")
    company_name: str = ""
    owner_name: str = ""
    phone: str = ""


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    company_name: str
    owner_name: str
    phone: str
    verified: str

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


# ---------- loads ----------

class LoadCreate(BaseModel):
    title: str = ""
    description: str = ""
    origin_city: str
    origin_lat: float
    origin_lng: float
    dest_city: str
    dest_lat: float
    dest_lng: float
    equipment_type: str
    weight_kg: float = Field(gt=0)
    volume_m3: float | None = Field(default=None, gt=0)
    budget_ghs: float | None = Field(default=None, gt=0)
    pickup_time: datetime
    delivery_time: datetime | None = None


class LoadUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    origin_city: str | None = None
    origin_lat: float | None = None
    origin_lng: float | None = None
    dest_city: str | None = None
    dest_lat: float | None = None
    dest_lng: float | None = None
    equipment_type: str | None = None
    weight_kg: float | None = Field(default=None, gt=0)
    volume_m3: float | None = Field(default=None, gt=0)
    budget_ghs: float | None = Field(default=None, gt=0)
    pickup_time: datetime | None = None
    delivery_time: datetime | None = None


class LoadOut(BaseModel):
    id: int
    shipper_id: int
    title: str
    description: str
    origin_city: str
    origin_lat: float
    origin_lng: float
    dest_city: str
    dest_lat: float
    dest_lng: float
    equipment_type: str
    weight_kg: float
    volume_m3: float | None
    budget_ghs: float | None
    pickup_time: datetime
    delivery_time: datetime | None
    status: str
    assigned_truck_id: int | None
    assigned_carrier_id: int | None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- trucks ----------

class TruckCreate(BaseModel):
    name: str = ""
    plate_no: str = ""
    equipment_type: str
    capacity_kg: float = Field(gt=0)
    rate_per_km_ghs: float | None = Field(default=None, gt=0)
    origin_city: str | None = None
    origin_lat: float
    origin_lng: float
    dest_city: str | None = None
    dest_lat: float | None = None
    dest_lng: float | None = None
    available_from: datetime
    available_until: datetime


class TruckUpdate(BaseModel):
    name: str | None = None
    plate_no: str | None = None
    equipment_type: str | None = None
    capacity_kg: float | None = Field(default=None, gt=0)
    rate_per_km_ghs: float | None = Field(default=None, gt=0)
    origin_city: str | None = None
    origin_lat: float | None = None
    origin_lng: float | None = None
    dest_city: str | None = None
    dest_lat: float | None = None
    dest_lng: float | None = None
    available_from: datetime | None = None
    available_until: datetime | None = None


class TruckOut(BaseModel):
    id: int
    carrier_id: int
    name: str
    plate_no: str
    equipment_type: str
    capacity_kg: float
    rate_per_km_ghs: float | None
    origin_city: str | None
    origin_lat: float
    origin_lng: float
    dest_city: str | None
    dest_lat: float | None
    dest_lng: float | None
    available_from: datetime
    available_until: datetime
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- offers ----------

class OfferCreate(BaseModel):
    amount: float = Field(gt=0)
    truck_id: int | None = None


class OfferOut(BaseModel):
    id: int
    load_id: int
    carrier_id: int
    truck_id: int | None
    amount: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- marketplace dialect (for the Empty-Trackload frontend) ----------

class ApiRegisterIn(BaseModel):
    role: str = Field(pattern="^(shipper|carrier)$")
    ownerName: str = ""
    company: str = ""
    email: EmailStr
    phone: str = ""
    password: str = Field(min_length=8)


class ApiUserOut(BaseModel):
    id: int
    email: str
    role: str
    companyName: str
    ownerName: str
    phone: str
    verified: str


class ApiLoginIn(BaseModel):
    email: EmailStr
    password: str


class ApiTokenOut(BaseModel):
    token: str
    user: ApiUserOut


class ApiTruckIn(BaseModel):
    plateNo: str = ""
    truckType: str
    fromCity: str | None = None
    toCity: str | None = None
    capacityKg: float = Field(gt=0)
    volumeM3: float | None = Field(default=None, gt=0)
    ratePerKmGhs: float | None = Field(default=None, gt=0)
    availableFrom: datetime
    availableTo: datetime


class ApiLoadIn(BaseModel):
    description: str = ""
    fromCity: str
    toCity: str
    weightKg: float = Field(gt=0)
    volumeM3: float | None = Field(default=None, gt=0)
    budgetGhs: float | None = Field(default=None, gt=0)
    pickupDate: datetime
    deliveryDate: datetime | None = None


class MatchCreateIn(BaseModel):
    truckId: int
    loadId: int


class SignContractIn(BaseModel):
    signatureName: str


class TrackingIn(BaseModel):
    lat: float
    lng: float
    note: str = ""


class PodVerifyIn(BaseModel):
    otp: str
