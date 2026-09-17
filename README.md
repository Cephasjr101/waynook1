# waynook - Backend MVP

Freight load-matching platform. Shippers post loads, carriers post truck
capacity, a matching engine pairs them, and offers flow through acceptance
to delivery.

## Stack

- **FastAPI** (Python 3.10+) - API layer
- **SQLAlchemy 2.0 + SQLite** - ORM & dev database (set `DATABASE_URL` for Postgres)
- **Stdlib-only security** - PBKDF2 password hashing + HS256 JWT (no passlib/python-jose)
- **Optional Firebase Auth** - verifies Firebase ID tokens when configured

## Quick start

```
pip install -r requirements.txt
python seed.py          # creates fresh DB with demo data
uvicorn main:app --reload
```

Demo accounts (password `password123`):

- `shipper@demo.io` - Acme Manufacturing
- `carrier@demo.io` - FastHaul Logistics

Interactive docs: http://127.0.0.1:8000/docs

## API overview

| Method | Endpoint | Who | Description |
| --- | --- | --- | --- |
| POST | `/auth/register` | public | Create shipper/carrier account |
| POST | `/auth/login` | public | OAuth2 form login -> JWT |
| GET | `/me` | any | Current user profile |
| POST | `/loads` | shipper | Post a load |
| GET | `/loads` | public | List/filter loads by status & equipment |
| GET/PATCH/DELETE | `/loads/{id}` | shipper (own) | Manage load |
| POST | `/trucks` | carrier | Register truck capacity |
| GET | `/trucks` | public | List/filter trucks |
| GET/PATCH | `/trucks/{id}` | carrier (own) | Manage truck |
| GET | `/loads/{id}/matches` | public | Ranked compatible trucks |
| POST | `/loads/{id}/offers` | carrier | Bid on a load |
| GET | `/loads/{id}/offers` | shipper (own) / carrier | List offers |
| POST | `/offers/{id}/accept` | shipper | Accept: load assigned, other offers auto-rejected, truck booked |
| POST | `/offers/{id}/reject` | shipper | Reject an offer |
| POST | `/loads/{id}/pickup` | owner/assigned carrier | assigned -> in_transit |
| POST | `/loads/{id}/deliver` | owner/assigned carrier | in_transit -> delivered, truck freed & relocated |

## Matching engine (matching.py)

Hard filters: equipment type, weight capacity, availability window covering
pickup time, status = available, truck within 500 km of origin.

Scoring (max ~80):

- up to 60 pts by distance to origin (linear, closer = higher)
- +10 pts if truck is available on the pickup day
- +10 pts for tight capacity fit (>= 80% utilization)

## Status machines

- **Load:** open -> assigned -> in_transit -> delivered (or cancelled)
- **Truck:** available <-> assigned (freed on delivery, relocated to destination)
- **Offer:** pending -> accepted | rejected | withdrawn

Accepting an offer atomically: marks offer accepted, rejects all other
pending offers, books the truck, assigns the load.

## Firebase Auth (optional)

The API accepts Firebase ID tokens as Bearer tokens in addition to local
JWTs. If a Firebase token verifies for an email with no local user, a local
`User` row is auto-provisioned (role from a `role` custom claim, defaulting
to `shipper`).

```
pip install firebase-admin
export FIREBASE_SERVICE_ACCOUNT=/path/to/serviceAccount.json
```

If firebase-admin is missing or credentials are absent/invalid, the app
logs a note and continues with local JWT only.

## Static files

The `static/` directory is served at `/static/*`; `static/index.html` is
served at `/`. Override the location with the `STATIC_DIR` env var.

## Tests

```
pytest tests/ -v
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./loadmatch.db` | SQLAlchemy DB URL |
| `JWT_SECRET` | dev-only value | JWT signing secret - set in production! |
| `STATIC_DIR` | `./static` | Static files directory |
| `FIREBASE_SERVICE_ACCOUNT` | unset | Path to Firebase service account JSON |

## MVP limitations / next steps

- No payments, documents (BOL/POD), or notifications yet
- Matching is synchronous, single-leg; no multi-stop or backhaul optimization
- SQLite single-writer; move to Postgres for production
- Add rate limiting, refresh tokens, email verification
