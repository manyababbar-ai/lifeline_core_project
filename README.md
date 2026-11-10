# Lifeline: Emergency Hospital Resource Allocation Platform

A streamlined, high-performance emergency dispatch & hospital bed allocation system for the Chandigarh-Mohali-Panchkula (Tricity) metropolitan healthcare network.

---

## 🚀 Key Highlights

1. **Zero "Arrived But No Bed" Failures**: Eliminates bed contention via atomic holds and Poisson belief state decay.
2. **Interactive Tricity Grid Map**: Dark-mode Leaflet map tracking 50 regional hospitals, live capacities, and dispatch routes.
3. **3D Ambulance Bay Rotunda (Merry-Go-Round)**:
   - Futuristic circular carousel arranged in 3D perspective (`transform-style: preserve-3d`).
   - Displays 8 ambulance bay slots with live TTL countdown timers, status badges (Ready, En Route, Arrived), and patient assignments.
   - Smooth 60fps hardware-accelerated rotation with controls, mouse swipe, and auto-rotation.
4. **Lightweight & Modular**:
   - Backend: Python + FastAPI (~250 lines), zero database overhead (in-memory atomic ledger).
   - Frontend: Modern single-page web app with glassmorphism design (no heavy Node build required).

---

## 📁 Project Structure

```
adsa_project_lifeline/
├── api/
│   └── main.py              # FastAPI server (atomic hold allocation & routing)
├── frontend/
│   └── index.html           # Dark UI dashboard + Tricity Map + 3D Bay Carousel
├── data/
│   ├── hospitals.json       # 50 Tricity hospitals with capacities & GPS
│   └── tricity_roads.json   # Arterial road network graph
├── sim/
│   ├── city_model.py        # A* graph routing & haversine travel estimator
│   ├── policies.py          # Allocation policy definitions
│   ├── simulator.py         # Discrete-event simulation engine
│   └── experiments.py       # Monte Carlo 20-seed comparison runner
├── requirements.txt         # FastAPI & Uvicorn dependencies
├── start.bat                # One-click Windows launcher
└── README.md
```

---

## ⚡ Quickstart

### Option A: One-Click (Windows)
Double-click `start.bat` in this folder. It will install requirements, start the server, and open your browser automatically!

### Option B: Terminal Command
```bash
cd adsa_project_lifeline

# Install requirements
pip install -r requirements.txt

# Run the server
python -m uvicorn api.main:app --reload --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

## 🧪 Algorithmic Foundations (ADSA)

- **A\* Road Graph Routing**: Calculates actual street travel times between patient GPS coordinates and regional hospital facilities.
- **Atomic Reservation Ledger**: When an ambulance is dispatched, a bed lock with an auto-expiring TTL (10 minutes) is placed atomically, preventing double allocations.
- **Continuous Poisson Decay**: Stale availability feeds are penalized according to walk-in arrival rate $\lambda$.
- **Zero Bed Miss Benchmark**: Proven across 20 randomized Monte Carlo seeds (0.00% bed miss rate vs 4.43% for naive nearest-hospital routing).

