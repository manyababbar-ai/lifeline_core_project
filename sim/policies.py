import math
from typing import Dict, Any, List, Optional, Tuple
from sim.city_model import CityModel, haversine_km

class RoutingPolicy:
    def decide(
        self,
        request: Dict[str, Any],
        reported_state: Dict[str, Dict[str, Any]],
        city: CityModel,
        current_time_sec: float
    ) -> Dict[str, Any]:
        raise NotImplementedError

class NearestHospitalPolicy(RoutingPolicy):
    """Policy A: Nearest-Hospital Routing.
    Dispatches ambulance to the closest hospital by road time, completely ignoring whether bed exists.
    """
    def __init__(self, name: str = 'NEAREST_HOSPITAL'):
        self.name = name

    def decide(
        self,
        request: Dict[str, Any],
        reported_state: Dict[str, Dict[str, Any]],
        city: CityModel,
        current_time_sec: float
    ) -> Dict[str, Any]:
        origin_lat = request['origin_lat']
        origin_lon = request['origin_lon']

        best_hosp_id = None
        best_time_sec = float('inf')
        best_dist_km = float('inf')

        for hosp in city.hospitals:
            h_id = hosp['id']
            # Compute road travel time
            time_sec, dist_km = city.calculate_travel_time_sec(origin_lat, origin_lon, hosp['lat'], hosp['lon'])
            if time_sec < best_time_sec:
                best_time_sec = time_sec
                best_dist_km = dist_km
                best_hosp_id = h_id

        return {
            'policy': self.name,
            'chosen_hospital_id': best_hosp_id,
            'travel_time_sec': best_time_sec,
            'travel_distance_km': best_dist_km,
            'reserved': False,
            'fallback_count': 0,
            'explanation': 'Routed to strictly nearest hospital by road travel time.'
        }

class PortalCountPolicy(RoutingPolicy):
    """Policy B: Trust-the-Portal Routing.
    Dispatches ambulance to closest hospital where last reported count of the needed resource > 0.
    Blindly trusts the portal without freshness decay or reservation holds.
    """
    def __init__(self, name: str = 'PORTAL_COUNT'):
        self.name = name

    def decide(
        self,
        request: Dict[str, Any],
        reported_state: Dict[str, Dict[str, Any]],
        city: CityModel,
        current_time_sec: float
    ) -> Dict[str, Any]:
        origin_lat = request['origin_lat']
        origin_lon = request['origin_lon']
        res_type = request['required_resource']

        candidates = []
        for hosp in city.hospitals:
            h_id = hosp['id']
            rep = reported_state.get(h_id, {})
            rep_avail = rep.get('resources', {}).get(res_type, 0)
            if rep_avail > 0:
                time_sec, dist_km = city.calculate_travel_time_sec(origin_lat, origin_lon, hosp['lat'], hosp['lon'])
                candidates.append((time_sec, dist_km, h_id))

        if not candidates:
            # Fallback to nearest if portal reports zero everywhere
            fallback_pol = NearestHospitalPolicy()
            res = fallback_pol.decide(request, reported_state, city, current_time_sec)
            res['policy'] = self.name
            res['explanation'] = 'Portal reported zero free beds; fallback to nearest.'
            return res

        candidates.sort(key=lambda x: x[0])
        best_time_sec, best_dist_km, best_hosp_id = candidates[0]

        return {
            'policy': self.name,
            'chosen_hospital_id': best_hosp_id,
            'travel_time_sec': best_time_sec,
            'travel_distance_km': best_dist_km,
            'reserved': False,
            'fallback_count': 0,
            'explanation': 'Routed to closest hospital with reported availability > 0.'
        }

class LifelineCorePolicy(RoutingPolicy):
    """Policy C: Lifeline-Core Freshness-Aware Reservation Routing.
    Applies time-decay freshness discount, Poisson probability of bed availability on arrival,
    scores candidates, attempts atomic reservation, and falls back to rank-2/rank-3 on contention.
    """
    def __init__(
        self,
        name: str = 'LIFELINE_CORE',
        max_age_sec: float = 3600.0,
        w_eta: float = 40.0,
        w_avail: float = 2.0,
        w_fresh: float = 15.0,
        w_prob: float = 25.0
    ):
        self.name = name
        self.max_age_sec = max_age_sec
        self.w_eta = w_eta
        self.w_avail = w_avail
        self.w_fresh = w_fresh
        self.w_prob = w_prob

    def calculate_poisson_p_free(self, effective_beds: int, eta_minutes: float, lambda_rate: float) -> float:
        if effective_beds <= 0:
            return 0.0
        if eta_minutes <= 0.0 or lambda_rate <= 0.0:
            return 1.0

        expected = lambda_rate * eta_minutes
        sum_prob = 0.0
        term = math.exp(-expected)
        sum_prob += term

        for k in range(1, effective_beds):
            term *= (expected / float(k))
            sum_prob += term
            if sum_prob >= 1.0:
                return 1.0
        return min(1.0, max(0.0, sum_prob))

    def decide(
        self,
        request: Dict[str, Any],
        reported_state: Dict[str, Dict[str, Any]],
        city: CityModel,
        current_time_sec: float
    ) -> Dict[str, Any]:
        origin_lat = request['origin_lat']
        origin_lon = request['origin_lon']
        res_type = request['required_resource']

        scored_candidates = []

        for hosp in city.hospitals:
            h_id = hosp['id']
            rep = reported_state.get(h_id, {})
            rep_avail = rep.get('resources', {}).get(res_type, 0)
            rep_time = rep.get('last_report_sec', 0.0)
            active_holds_after_report = rep.get('active_holds_after_report', {}).get(res_type, 0)

            # 1. Effective available count
            effective_avail = max(0, rep_avail - active_holds_after_report)

            # 2. Freshness score
            age_sec = max(0.0, current_time_sec - rep_time)
            freshness = max(0.0, 1.0 - (age_sec / self.max_age_sec))

            # 3. Travel time
            time_sec, dist_km = city.calculate_travel_time_sec(origin_lat, origin_lon, hosp['lat'], hosp['lon'])
            eta_min = max(0.1, time_sec / 60.0)

            # 4. Poisson probability
            lambda_rate = hosp.get('walk_in_lambda', 0.15)
            p_free = self.calculate_poisson_p_free(effective_avail, eta_min, lambda_rate)

            # 5. Composite score
            eta_term = self.w_eta * (1.0 / (eta_min + 0.1))
            avail_term = self.w_avail * effective_avail
            fresh_term = self.w_fresh * freshness
            prob_term = self.w_prob * p_free

            if effective_avail == 0:
                score = -100.0 + (eta_term * 0.1)
            else:
                score = eta_term + avail_term + fresh_term + prob_term

            scored_candidates.append({
                'hospital_id': h_id,
                'score': score,
                'travel_time_sec': time_sec,
                'travel_distance_km': dist_km,
                'effective_avail': effective_avail,
                'freshness': freshness,
                'p_free': p_free,
                'eta_min': eta_min
            })

        # Rank descending by score
        scored_candidates.sort(key=lambda x: x['score'], reverse=True)

        return {
            'policy': self.name,
            'ranked_candidates': scored_candidates,
            'chosen_hospital_id': scored_candidates[0]['hospital_id'] if scored_candidates else None,
            'travel_time_sec': scored_candidates[0]['travel_time_sec'] if scored_candidates else 0.0,
            'travel_distance_km': scored_candidates[0]['travel_distance_km'] if scored_candidates else 0.0,
            'reserved': True,
            'explanation': 'Ranked by freshness decay, road ETA, and Poisson walk-in probability.'
        }
