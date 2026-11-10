import json
import math
import heapq
from typing import Dict, List, Tuple, Optional

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

class CityModel:
    def __init__(self, hospitals_path: str = 'data/hospitals.json', roads_path: str = 'data/tricity_roads.json'):
        with open(hospitals_path, 'r') as f:
            self.hospitals = json.load(f)
        self.hospital_map = {h['id']: h for h in self.hospitals}

        with open(roads_path, 'r') as f:
            roads_data = json.load(f)

        self.nodes = {n['id']: n for n in roads_data['nodes']}
        self.adj: Dict[str, List[Tuple[str, float, float]]] = {n['id']: [] for n in roads_data['nodes']}

        for e in roads_data['edges']:
            u = e['from']
            v = e['to']
            dist_km = e['length_m'] / 1000.0
            speed_kmh = max(10.0, e['speed_kmh'])
            time_sec = (dist_km / speed_kmh) * 3600.0

            self.adj[u].append((v, dist_km, time_sec))
            if e.get('bidirectional', True):
                self.adj[v].append((u, dist_km, time_sec))

    def find_nearest_node(self, lat: float, lon: float) -> str:
        best_node = None
        best_dist = float('inf')
        for nid, n in self.nodes.items():
            d = haversine_km(lat, lon, n['lat'], n['lon'])
            if d < best_dist:
                best_dist = d
                best_node = nid
        return best_node

    def calculate_travel_time_sec(self, origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> Tuple[float, float]:
        """Returns (travel_time_seconds, travel_distance_km) via road graph A* or realistic urban road model."""
        start_node = self.find_nearest_node(origin_lat, origin_lon)
        target_node = self.find_nearest_node(dest_lat, dest_lon)

        if not start_node or not target_node or start_node == target_node:
            straight_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
            road_km = max(0.5, straight_km * 1.35)
            time_sec = (road_km / 42.0) * 3600.0
            return time_sec, road_km

        # A* on road graph
        target_coords = (self.nodes[target_node]['lat'], self.nodes[target_node]['lon'])
        max_speed_kmh = 70.0

        def heuristic(u_id: str) -> float:
            d = haversine_km(self.nodes[u_id]['lat'], self.nodes[u_id]['lon'], target_coords[0], target_coords[1])
            return (d / max_speed_kmh) * 3600.0

        pq = [(heuristic(start_node), 0.0, 0.0, start_node)]
        dist_time = {start_node: 0.0}
        dist_km_map = {start_node: 0.0}

        found = False
        while pq:
            f, g_time, g_km, u = heapq.heappop(pq)
            if u == target_node:
                found = True
                break
            if g_time > dist_time.get(u, float('inf')):
                continue

            for v, e_km, e_time in self.adj.get(u, []):
                tentative_time = g_time + e_time
                if tentative_time < dist_time.get(v, float('inf')):
                    dist_time[v] = tentative_time
                    dist_km_map[v] = g_km + e_km
                    h = heuristic(v)
                    heapq.heappush(pq, (tentative_time + h, tentative_time, g_km + e_km, v))

        if found:
            ingress_km = haversine_km(origin_lat, origin_lon, self.nodes[start_node]['lat'], self.nodes[start_node]['lon'])
            egress_km = haversine_km(dest_lat, dest_lon, self.nodes[target_node]['lat'], self.nodes[target_node]['lon'])
            total_km = dist_km_map[target_node] + ingress_km + egress_km
            total_time = dist_time[target_node] + ((ingress_km + egress_km) / 30.0) * 3600.0
            return total_time, total_km

        # Fallback if disjoint
        straight_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
        road_km = straight_km * 1.35
        return (road_km / 42.0) * 3600.0, road_km
