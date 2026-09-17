from math import asin, cos, radians, sin, sqrt

MAX_MATCH_DISTANCE_KM = 500.0
DISTANCE_SCORE_MAX = 60.0
DAY_BONUS = 10.0
TIGHT_FIT_BONUS = 10.0
TIGHT_FIT_THRESHOLD = 0.8  # utilization (load weight / truck capacity)

# Ghana city coordinates (used by the marketplace frontend)
CITY_COORDS = {
    "accra": (5.6037, -0.1870),
    "kumasi": (6.6885, -1.6244),
    "tema": (5.6698, -0.0166),
    "takoradi": (4.8982, -1.7603),
    "tamale": (9.4034, -0.8424),
    "ho": (6.6000, 0.4667),
    "cape coast": (5.1053, -1.2466),
    "sunyani": (7.3399, -2.3268),
}


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))


def score_match(load, truck, distance_km: float) -> float:
    score = DISTANCE_SCORE_MAX * (1 - distance_km / MAX_MATCH_DISTANCE_KM)
    if truck.available_from.date() <= load.pickup_time.date() <= truck.available_until.date():
        score += DAY_BONUS
    utilization = load.weight_kg / truck.capacity_kg
    if utilization >= TIGHT_FIT_THRESHOLD:
        score += TIGHT_FIT_BONUS
    return score


def compatible_trucks(load, trucks):
    # Hard filters: equipment, capacity, availability window, truck available, within 500 km.
    results = []
    for truck in trucks:
        if truck.status != "available":
            continue
        if truck.equipment_type != load.equipment_type:
            continue
        if truck.capacity_kg < load.weight_kg:
            continue
        if not (truck.available_from <= load.pickup_time <= truck.available_until):
            continue
        distance_km = haversine_km(load.origin_lat, load.origin_lng, truck.current_lat, truck.current_lng)
        if distance_km > MAX_MATCH_DISTANCE_KM:
            continue
        score = score_match(load, truck, distance_km)
        results.append({"truck": truck, "distance_km": round(distance_km, 1), "score": round(score, 1)})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def is_reverse_route(truck, load) -> bool:
    # The truck is heading A -> B empty; the load needs B -> A.
    return (
        truck.origin_city and truck.dest_city
        and truck.origin_city.strip().lower() == load.dest_city.strip().lower()
        and truck.dest_city.strip().lower() == load.origin_city.strip().lower()
    )


def run_marketplace_matching(loads, trucks, base_score: float = 0.0):
    # Score every (truck, load) pair. Backhaul (reverse-route) pairs get a big bonus;
    # regular capacity-compatible pairs still match at lower priority.
    candidates = []
    for truck in trucks:
        for load in loads:
            if truck.status != "available" or load.status != "open":
                continue
            if truck.capacity_kg < load.weight_kg:
                continue
            if not (truck.available_from <= load.pickup_time <= truck.available_until):
                continue
            score = base_score
            reverse = is_reverse_route(truck, load)
            if reverse:
                score += 40.0  # backhaul bonus
                distance_km = haversine_km(
                    load.origin_lat, load.origin_lng, truck.dest_lat or truck.origin_lat, truck.dest_lng or truck.origin_lng
                )
            else:
                distance_km = haversine_km(load.origin_lat, load.origin_lng, truck.origin_lat, truck.origin_lng)
                if distance_km > MAX_MATCH_DISTANCE_KM:
                    continue
                score += DISTANCE_SCORE_MAX * (1 - distance_km / MAX_MATCH_DISTANCE_KM)
            candidates.append({
                "truck": truck,
                "load": load,
                "score": round(score, 1),
                "reverse": reverse,
                "distance_km": round(distance_km, 1),
            })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def estimate_price_ghs(distance_km: float, rate_per_km_ghs: float | None, weight_kg: float | None = None, minimum: float = 300.0) -> float:
    rate = rate_per_km_ghs if rate_per_km_ghs else 5.0
    return max(minimum, round(distance_km * rate, 2))
