import numpy as np
import pandas as pd
from typing import List, Dict, Any

class RequestGenerator:
    """Generates synthetic emergency requests matching Blueprint Section 5 distributions."""

    RESOURCES = ['ICU-BED', 'VENTILATOR', 'OXYGEN-BED', 'GENERAL-BED']
    TRAUMA_SCORES = [1, 2, 3, 4, 5]
    TRAUMA_PROBS = [0.10, 0.20, 0.30, 0.25, 0.15]

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def generate_workload(
        self,
        num_requests: int = 500,
        start_timestamp_sec: float = 0.0,
        base_rate_per_hour: float = 60.0,
        include_surge: bool = False
    ) -> List[Dict[str, Any]]:
        requests = []
        current_time = start_timestamp_sec

        # Urban clusters for Tricity emergency origins (Chandigarh center, Mohali tech hub, Panchkula)
        clusters = [
            {'lat': 30.7333, 'lon': 76.7794, 'weight': 0.40}, # Chandigarh
            {'lat': 30.7046, 'lon': 76.7179, 'weight': 0.35}, # Mohali
            {'lat': 30.6942, 'lon': 76.8606, 'weight': 0.25}  # Panchkula
        ]
        cluster_weights = [c['weight'] for c in clusters]

        for i in range(num_requests):
            # Poisson inter-arrival time with peak hour modulation
            sim_hour = (current_time / 3600.0) % 24.0
            # Higher arrival rate during morning/evening peak hours
            rate_factor = 1.0
            if (8.0 <= sim_hour <= 11.0) or (18.0 <= sim_hour <= 21.0):
                rate_factor = 1.6
            elif 1.0 <= sim_hour <= 5.0:
                rate_factor = 0.4

            # Contention surge scenario injection
            if include_surge and (150 <= i <= 250):
                rate_factor *= 3.0

            arrival_rate_sec = (base_rate_per_hour * rate_factor) / 3600.0
            inter_arrival = self.rng.exponential(1.0 / arrival_rate_sec)
            current_time += inter_arrival

            # Age: normal distribution (mean 45.6, std 18.0) clipped to [1, 90]
            age = int(np.clip(self.rng.normal(45.6, 18.0), 1, 90))

            # Trauma Score: categorical [1..5]
            trauma = int(self.rng.choice(self.TRAUMA_SCORES, p=self.TRAUMA_PROBS))

            # Required Resource: uniform 25% each
            resource = str(self.rng.choice(self.RESOURCES))

            # Sample origin coordinate near an urban cluster
            c_idx = self.rng.choice(len(clusters), p=cluster_weights)
            cluster = clusters[c_idx]
            lat = float(cluster['lat'] + self.rng.normal(0.0, 0.025))
            lon = float(cluster['lon'] + self.rng.normal(0.0, 0.025))

            req = {
                'request_id': f'PAT-{100001 + i}',
                'timestamp_sec': round(current_time, 2),
                'timestamp_ms': int(current_time * 1000),
                'patient_age': age,
                'trauma_score': trauma,
                'required_resource': resource,
                'origin_lat': round(lat, 5),
                'origin_lon': round(lon, 5)
            }
            requests.append(req)

        return requests

    def save_workload_csv(self, requests: List[Dict[str, Any]], filepath: str):
        df = pd.DataFrame(requests)
        df.to_csv(filepath, index=False)
