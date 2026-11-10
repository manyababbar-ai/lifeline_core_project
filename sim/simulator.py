import copy
import heapq
import numpy as np
from typing import Dict, Any, List, Optional
from sim.city_model import CityModel
from sim.policies import RoutingPolicy, NearestHospitalPolicy, PortalCountPolicy, LifelineCorePolicy

class SimulationEngine:
    def __init__(
        self,
        city: CityModel,
        seed: int = 42,
        feed_delay_mean_sec: float = 600.0, # 10 minute average reporting lag
        chaos_freeze_hospitals: Optional[List[str]] = None,
        redirect_penalty_sec: float = 1080.0 # 18 minute redirect penalty on bed miss
    ):
        self.city = city
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.feed_delay_mean_sec = feed_delay_mean_sec
        self.chaos_freeze_hospitals = set(chaos_freeze_hospitals or [])
        self.redirect_penalty_sec = redirect_penalty_sec

    def _init_ground_truth(self) -> Dict[str, Dict[str, Any]]:
        ground_truth = {}
        for hosp in self.city.hospitals:
            h_id = hosp['id']
            # Realistic starting occupancy: 65% to 85% full
            icu_total = hosp['icu']
            vent_total = hosp['vent']
            oxy_total = hosp['oxy']
            gen_total = hosp['gen']

            ground_truth[h_id] = {
                'ICU-BED': {'total': icu_total, 'free': int(icu_total * self.rng.uniform(0.15, 0.35)), 'reserved': 0},
                'VENTILATOR': {'total': vent_total, 'free': int(vent_total * self.rng.uniform(0.15, 0.40)), 'reserved': 0},
                'OXYGEN-BED': {'total': oxy_total, 'free': int(oxy_total * self.rng.uniform(0.20, 0.45)), 'reserved': 0},
                'GENERAL-BED': {'total': gen_total, 'free': int(gen_total * self.rng.uniform(0.25, 0.50)), 'reserved': 0},
                'walk_in_lambda': hosp.get('walk_in_lambda', 0.15)
            }
        return ground_truth

    def _init_reported_state(self, ground_truth: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        reported = {}
        for h_id, res_map in ground_truth.items():
            reported[h_id] = {
                'last_report_sec': -self.rng.uniform(60.0, 900.0),
                'resources': {r: res_map[r]['free'] for r in ['ICU-BED', 'VENTILATOR', 'OXYGEN-BED', 'GENERAL-BED']},
                'active_holds_after_report': {r: 0 for r in ['ICU-BED', 'VENTILATOR', 'OXYGEN-BED', 'GENERAL-BED']}
            }
        return reported

    def run_simulation(
        self,
        requests: List[Dict[str, Any]],
        policy: RoutingPolicy,
        hold_ttl_sec: float = 900.0 # 15 minutes TTL
    ) -> Dict[str, Any]:
        ground_truth = self._init_ground_truth()
        reported_state = self._init_reported_state(ground_truth)

        # Event queue: (time_sec, event_type, priority_id, payload)
        # Event types: 'REQUEST', 'AMBULANCE_ARRIVAL', 'FEED_UPDATE', 'WALK_IN', 'HOLD_EXPIRY'
        event_queue = []
        event_id = 0

        # Schedule initial requests
        for req in requests:
            event_id += 1
            heapq.heappush(event_queue, (req['timestamp_sec'], 'REQUEST', event_id, req))

        # Schedule background hospital feed updates
        sim_duration = requests[-1]['timestamp_sec'] + 7200.0 if requests else 3600.0
        for h_id in ground_truth.keys():
            if h_id in self.chaos_freeze_hospitals:
                continue # Frozen feeds never update
            t = self.rng.uniform(30.0, 300.0)
            while t < sim_duration:
                event_id += 1
                heapq.heappush(event_queue, (t, 'FEED_UPDATE', event_id, {'hospital_id': h_id}))
                t += self.rng.exponential(self.feed_delay_mean_sec)

        # Active reservations: res_id -> {hospital_id, resource, req_id, expires_at, state}
        active_holds = {}
        hold_counter = 0

        # Outcome tracking
        outcomes = []
        bed_miss_count = 0
        redirect_count = 0
        double_allocation_count = 0
        reservation_failure_count = 0
        expiry_count = 0

        current_time = 0.0

        while event_queue:
            t, event_type, _, payload = heapq.heappop(event_queue)
            current_time = t

            if event_type == 'FEED_UPDATE':
                h_id = payload['hospital_id']
                if h_id not in self.chaos_freeze_hospitals:
                    reported_state[h_id]['last_report_sec'] = current_time
                    for r in ['ICU-BED', 'VENTILATOR', 'OXYGEN-BED', 'GENERAL-BED']:
                        # Reports currently free beds (ground truth)
                        reported_state[h_id]['resources'][r] = ground_truth[h_id][r]['free']
                        # Reset active holds created after this report
                        reported_state[h_id]['active_holds_after_report'][r] = 0

            elif event_type == 'REQUEST':
                req = payload
                res_type = req['required_resource']

                # Generate background walk-ins since last tick for hospitals
                # To simulate concurrent consumption of beds
                for h_id, h_data in ground_truth.items():
                    lam = h_data['walk_in_lambda']
                    # Small probability of walk-in consumption
                    if self.rng.random() < (lam * 0.15) and h_data[res_type]['free'] > 0:
                        h_data[res_type]['free'] -= 1

                # Make routing decision
                decision = policy.decide(req, reported_state, self.city, current_time)
                chosen_hosp_id = decision['chosen_hospital_id']
                travel_time_sec = decision['travel_time_sec']
                reserved_success = False
                res_id = None
                fallback_hops = 0

                if decision.get('reserved', False) and 'ranked_candidates' in decision:
                    # Lifeline Policy: Fallback reservation attempt
                    for cand in decision['ranked_candidates']:
                        c_id = cand['hospital_id']
                        # Atomic reservation check against ground truth under hospital lock
                        if ground_truth[c_id][res_type]['free'] > 0:
                            ground_truth[c_id][res_type]['free'] -= 1
                            ground_truth[c_id][res_type]['reserved'] += 1

                            hold_counter += 1
                            res_id = f'RES-{hold_counter}'
                            expires_at = current_time + hold_ttl_sec

                            active_holds[res_id] = {
                                'id': res_id,
                                'hospital_id': c_id,
                                'resource': res_type,
                                'request_id': req['request_id'],
                                'state': 'HELD',
                                'expires_at': expires_at
                            }

                            # Update reported active holds counter for that hospital
                            reported_state[c_id]['active_holds_after_report'][res_type] += 1

                            # Schedule auto-expiry event
                            event_id += 1
                            heapq.heappush(event_queue, (expires_at, 'HOLD_EXPIRY', event_id, {'res_id': res_id}))

                            chosen_hosp_id = c_id
                            travel_time_sec = cand['travel_time_sec']
                            reserved_success = True
                            break
                        else:
                            fallback_hops += 1

                    if not reserved_success:
                        reservation_failure_count += 1
                        # Requeue or fallback to best effort
                        chosen_hosp_id = decision['ranked_candidates'][0]['hospital_id']
                        travel_time_sec = decision['ranked_candidates'][0]['travel_time_sec']

                # Schedule ambulance arrival event
                arrival_time = current_time + travel_time_sec
                event_id += 1
                arrival_payload = {
                    'request': req,
                    'policy': policy.name,
                    'chosen_hospital_id': chosen_hosp_id,
                    'res_id': res_id,
                    'departure_time_sec': current_time,
                    'initial_travel_time_sec': travel_time_sec,
                    'fallback_hops': fallback_hops,
                    'redirect_count': 0
                }
                heapq.heappush(event_queue, (arrival_time, 'AMBULANCE_ARRIVAL', event_id, arrival_payload))

            elif event_type == 'HOLD_EXPIRY':
                res_id = payload['res_id']
                if res_id in active_holds and active_holds[res_id]['state'] == 'HELD':
                    hold = active_holds[res_id]
                    hold['state'] = 'EXPIRED'
                    h_id = hold['hospital_id']
                    r = hold['resource']
                    # Bed returned to pool exactly once
                    if ground_truth[h_id][r]['reserved'] > 0:
                        ground_truth[h_id][r]['reserved'] -= 1
                    ground_truth[h_id][r]['free'] += 1
                    expiry_count += 1

            elif event_type == 'AMBULANCE_ARRIVAL':
                arr = payload
                req = arr['request']
                h_id = arr['chosen_hospital_id']
                r = req['required_resource']
                res_id = arr['res_id']

                bed_secured = False

                if res_id and res_id in active_holds and active_holds[res_id]['state'] == 'HELD':
                    # Lifeline hold confirmed!
                    active_holds[res_id]['state'] = 'CONFIRMED'
                    ground_truth[h_id][r]['reserved'] -= 1 # Bed occupied
                    bed_secured = True
                else:
                    # Policy A or B (or expired hold): must take an unreserved free bed from ground truth
                    if ground_truth[h_id][r]['free'] > 0:
                        ground_truth[h_id][r]['free'] -= 1
                        bed_secured = True
                    else:
                        # ARRIVED BUT NO BED!
                        bed_miss_count += 1
                        arr['redirect_count'] += 1
                        redirect_count += 1

                        # Forced redirect to an alternative hospital
                        alt_candidates = [h for h in self.city.hospitals if h['id'] != h_id]
                        if alt_candidates:
                            next_hosp = self.rng.choice(alt_candidates)
                            next_time, _ = self.city.calculate_travel_time_sec(
                                self.city.hospital_map[h_id]['lat'], self.city.hospital_map[h_id]['lon'],
                                next_hosp['lat'], next_hosp['lon']
                            )
                            # Re-schedule ambulance arrival after redirect transit + handoff delay
                            new_arrival = current_time + next_time + self.redirect_penalty_sec
                            arr['chosen_hospital_id'] = next_hosp['id']
                            arr['res_id'] = None
                            event_id += 1
                            heapq.heappush(event_queue, (new_arrival, 'AMBULANCE_ARRIVAL', event_id, arr))
                            continue

                total_time_min = (current_time - arr['departure_time_sec']) / 60.0
                outcomes.append({
                    'request_id': req['request_id'],
                    'hospital_id': h_id,
                    'bed_missed': not bed_secured,
                    'redirects': arr['redirect_count'],
                    'time_to_treatment_min': total_time_min,
                    'fallback_hops': arr['fallback_hops']
                })

        # Calculate metrics
        num_completed = len(outcomes)
        times = [o['time_to_treatment_min'] for o in outcomes] if outcomes else [0.0]

        miss_rate = (bed_miss_count / len(requests) * 100.0) if requests else 0.0
        mean_time = float(np.mean(times))
        p95_time = float(np.percentile(times, 95))
        p99_time = float(np.percentile(times, 99))

        return {
            'policy': policy.name,
            'total_requests': len(requests),
            'completed_requests': num_completed,
            'bed_miss_count': bed_miss_count,
            'bed_miss_rate_pct': round(miss_rate, 2),
            'mean_time_to_treatment_min': round(mean_time, 2),
            'p95_time_to_treatment_min': round(p95_time, 2),
            'p99_time_to_treatment_min': round(p99_time, 2),
            'double_allocation_count': double_allocation_count,
            'redirect_count': redirect_count,
            'reservation_failure_count': reservation_failure_count,
            'expiry_count': expiry_count
        }
