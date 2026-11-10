"""
Lifeline FastAPI Backend
Run with: uvicorn main:app --reload --port 8000
"""

import json, math, time, uuid, sys, os, threading, random
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_DIR   = os.path.join(BASE_DIR, "sim")
DATA_DIR  = os.path.join(BASE_DIR, "data")
FRONT_DIR = os.path.join(BASE_DIR, "frontend")
sys.path.insert(0, SIM_DIR)

from city_model import CityModel, haversine_km

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Lifeline API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Load data ──────────────────────────────────────────────────────────────────
city = CityModel(
    hospitals_path=os.path.join(DATA_DIR, "hospitals.json"),
    roads_path=os.path.join(DATA_DIR, "tricity_roads.json"),
)
RAW_HOSPITALS = city.hospitals   # list of dicts from JSON

AMBULANCE_NAMES = [
    "AMB-01","AMB-02","AMB-03","AMB-04","AMB-05",
    "AMB-06","AMB-07","AMB-08","AMB-09","AMB-10",
]
PATIENT_NAMES = [
    "Arjun Sharma","Priya Singh","Ravi Patel","Neha Verma","Karan Mehta",
    "Simran Kaur","Rahul Gupta","Ananya Bose","Vikram Nair","Pooja Yadav",
    "Amit Kumar","Deepika Reddy","Suresh Iyer","Meena Joshi","Rohit Thakur",
]
RESOURCE_TYPES = ["ICU Bed", "Ventilator", "Oxygen Bed", "General Bed"]

# ── In-memory state ────────────────────────────────────────────────────────────
_lock = threading.Lock()

# Hospital live state
hospital_state: dict = {}
for h in RAW_HOSPITALS:
    total = h["base_capacity"]
    avail = max(1, int(total * random.uniform(0.55, 0.90)))
    hospital_state[h["id"]] = {
        "id":            h["id"],
        "name":          h["name"],
        "lat":           h["lat"],
        "lon":           h["lon"],
        "total_beds":    total,
        "available_beds": avail,
        "icu_total":     h["icu"],
        "icu_available": max(0, int(h["icu"] * random.uniform(0.4, 0.85))),
        "vent_total":    h["vent"],
        "vent_available": max(0, int(h["vent"] * random.uniform(0.3, 0.80))),
        "status":        "AVAILABLE",
        "walk_in_lambda": h["walk_in_lambda"],
        "last_updated":  time.time(),
    }

# Active reservations: id -> dict
reservations: dict = {}

# Seed 3 demo reservations at startup
def _seed_demo():
    time.sleep(1.5)
    demo_patients = random.sample(PATIENT_NAMES, 3)
    demo_ambs     = random.sample(AMBULANCE_NAMES, 3)
    demo_hosps    = random.sample(list(hospital_state.keys()), 3)
    ttls = [420, 600, 300]
    statuses = ["EN_ROUTE", "EN_ROUTE", "ARRIVED"]
    for i in range(3):
        rid = str(uuid.uuid4())[:8].upper()
        hid = demo_hosps[i]
        h   = hospital_state[hid]
        with _lock:
            if h["available_beds"] > 0:
                h["available_beds"] -= 1
                if h["available_beds"] == 0:
                    h["status"] = "FULL"
        reservations[rid] = {
            "id":           rid,
            "patient_name": demo_patients[i],
            "ambulance":    demo_ambs[i],
            "hospital_id":  hid,
            "hospital_name": h["name"],
            "resource_type": random.choice(RESOURCE_TYPES),
            "status":       statuses[i],
            "expires_at":   time.time() + ttls[i],
            "created_at":   time.time() - random.randint(30, 180),
            "eta_sec":      random.randint(180, 600),
        }

threading.Thread(target=_seed_demo, daemon=True).start()

# ── TTL Sweeper ────────────────────────────────────────────────────────────────
def _ttl_sweeper():
    while True:
        now = time.time()
        with _lock:
            expired = [k for k, v in reservations.items() if v["expires_at"] < now]
            for k in expired:
                res = reservations.pop(k)
                hid = res["hospital_id"]
                if hid in hospital_state:
                    hospital_state[hid]["available_beds"] = min(
                        hospital_state[hid]["available_beds"] + 1,
                        hospital_state[hid]["total_beds"],
                    )
                    if hospital_state[hid]["available_beds"] > 0:
                        hospital_state[hid]["status"] = "AVAILABLE"
        time.sleep(5)

threading.Thread(target=_ttl_sweeper, daemon=True).start()

# ── Slow walk-in simulator (background) ───────────────────────────────────────
def _walk_in_simulator():
    """Randomly consume/release beds to keep the dashboard feeling alive."""
    while True:
        time.sleep(random.uniform(8, 15))
        with _lock:
            hid = random.choice(list(hospital_state.keys()))
            h   = hospital_state[hid]
            # Randomly consume or restore a bed
            if random.random() < 0.6 and h["available_beds"] > 2:
                h["available_beds"] -= 1
            elif h["available_beds"] < h["total_beds"]:
                h["available_beds"] += 1
            if h["available_beds"] == 0:
                h["status"] = "FULL"
            elif h["available_beds"] < h["total_beds"] * 0.15:
                h["status"] = "CRITICAL"
            else:
                h["status"] = "AVAILABLE"
            h["last_updated"] = time.time()

threading.Thread(target=_walk_in_simulator, daemon=True).start()

# ── Helpers ────────────────────────────────────────────────────────────────────
def _best_hospital(req_lat: float, req_lon: float, resource: str):
    """
    Lifeline allocation policy:
    Score = (availability_ratio * 0.4) - (travel_time_norm * 0.6)
    Pick highest score among hospitals with available_beds > 0.
    """
    candidates = []
    with _lock:
        for hid, h in hospital_state.items():
            if h["available_beds"] <= 0:
                continue
            t_sec, dist_km = city.calculate_travel_time_sec(
                req_lat, req_lon, h["lat"], h["lon"]
            )
            avail_ratio = h["available_beds"] / max(h["total_beds"], 1)
            candidates.append((hid, h.copy(), t_sec, dist_km, avail_ratio))

    if not candidates:
        return None, None, None

    # Normalise travel times
    max_t = max(c[2] for c in candidates) or 1
    scored = []
    for hid, h, t_sec, dist_km, avail_ratio in candidates:
        score = avail_ratio * 0.4 - (t_sec / max_t) * 0.6
        scored.append((score, hid, h, t_sec, dist_km))

    scored.sort(reverse=True)
    best = scored[0]
    return best[1], best[2], best[3]   # hid, h_snapshot, eta_sec

# ── Pydantic models ────────────────────────────────────────────────────────────
class EmergencyRequest(BaseModel):
    lat:           float
    lon:           float
    patient_name:  Optional[str] = None
    resource_type: Optional[str] = "General Bed"
    ambulance:     Optional[str] = None

class ReleaseRequest(BaseModel):
    reservation_id: str

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse(os.path.join(FRONT_DIR, "index.html"))

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Lifeline API v1"}

@app.get("/hospitals")
def get_hospitals():
    with _lock:
        return list(hospital_state.values())

@app.get("/hospitals/{hospital_id}")
def get_hospital(hospital_id: str):
    with _lock:
        h = hospital_state.get(hospital_id)
    if not h:
        raise HTTPException(404, "Hospital not found")
    return h

@app.get("/reservations")
def get_reservations():
    now = time.time()
    with _lock:
        return [
            {**v, "ttl_remaining": max(0, int(v["expires_at"] - now))}
            for v in reservations.values()
        ]

@app.post("/request")
def create_request(req: EmergencyRequest):
    """Allocate the best hospital and create a reservation (TTL = 10 min)."""
    hid, h_snap, eta_sec = _best_hospital(req.lat, req.lon, req.resource_type)
    if not hid:
        raise HTTPException(503, "No beds available in any hospital")

    patient = req.patient_name or random.choice(PATIENT_NAMES)
    ambulance = req.ambulance or random.choice(AMBULANCE_NAMES)
    rid = str(uuid.uuid4())[:8].upper()
    TTL = 600   # 10 minutes

    with _lock:
        hospital_state[hid]["available_beds"] -= 1
        if hospital_state[hid]["available_beds"] == 0:
            hospital_state[hid]["status"] = "FULL"
        elif hospital_state[hid]["available_beds"] < hospital_state[hid]["total_beds"] * 0.15:
            hospital_state[hid]["status"] = "CRITICAL"
        hospital_state[hid]["last_updated"] = time.time()

        reservations[rid] = {
            "id":            rid,
            "patient_name":  patient,
            "ambulance":     ambulance,
            "hospital_id":   hid,
            "hospital_name": h_snap["name"],
            "resource_type": req.resource_type,
            "status":        "EN_ROUTE",
            "expires_at":    time.time() + TTL,
            "created_at":    time.time(),
            "eta_sec":       int(eta_sec),
            "req_lat":       req.lat,
            "req_lon":       req.lon,
        }

    return {
        "reservation_id":  rid,
        "hospital_id":     hid,
        "hospital_name":   h_snap["name"],
        "hospital_lat":    h_snap["lat"],
        "hospital_lon":    h_snap["lon"],
        "ambulance":       ambulance,
        "eta_sec":         int(eta_sec),
        "ttl_sec":         TTL,
        "resource_type":   req.resource_type,
        "available_after": h_snap["available_beds"] - 1,
    }

@app.post("/release")
def release_reservation(req: ReleaseRequest):
    with _lock:
        res = reservations.pop(req.reservation_id, None)
        if not res:
            raise HTTPException(404, "Reservation not found")
        hid = res["hospital_id"]
        if hid in hospital_state:
            hospital_state[hid]["available_beds"] = min(
                hospital_state[hid]["available_beds"] + 1,
                hospital_state[hid]["total_beds"],
            )
            if hospital_state[hid]["available_beds"] > 0:
                hospital_state[hid]["status"] = "AVAILABLE"
    return {"released": True, "reservation_id": req.reservation_id}

@app.get("/stats")
def get_stats():
    with _lock:
        total_beds    = sum(h["total_beds"]     for h in hospital_state.values())
        avail_beds    = sum(h["available_beds"] for h in hospital_state.values())
        active_res    = len(reservations)
        full_count    = sum(1 for h in hospital_state.values() if h["status"] == "FULL")
        critical_count = sum(1 for h in hospital_state.values() if h["status"] == "CRITICAL")
        now           = time.time()
        avg_ttl       = (
            sum(max(0, v["expires_at"] - now) for v in reservations.values()) / active_res
            if active_res else 0
        )
    return {
        "total_beds":      total_beds,
        "available_beds":  avail_beds,
        "occupancy_pct":   round((1 - avail_beds / max(total_beds, 1)) * 100, 1),
        "active_requests": active_res,
        "full_hospitals":  full_count,
        "critical_hospitals": critical_count,
        "total_hospitals": len(hospital_state),
        "avg_ttl_sec":     int(avg_ttl),
    }

# Serve frontend
app.mount("/app", StaticFiles(directory=FRONT_DIR, html=True), name="frontend")

@app.get("/")
def serve_root():
    return FileResponse(os.path.join(FRONT_DIR, "index.html"))

@app.get("/ui")
def serve_ui():
    return FileResponse(os.path.join(FRONT_DIR, "index.html"))

