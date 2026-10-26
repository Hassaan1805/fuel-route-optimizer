"""
Fuel Optimization Service.

Finds the cheapest way to buy fuel along a fixed driving route without the
vehicle ever running dry (range <= max_range miles on a full tank).

Model:
    * The vehicle leaves the origin with an empty tank and fills up at a station
      near the origin, so every gallon burned on the trip is paid for and the
      reported stop costs add up exactly to the total cost.
    * Fuel can be bought in any amount (not necessarily a full tank).
    * Each stop carries a small penalty (fixed stop cost + fuel for the detour
      off the highway). The penalty is only used to choose stops: it keeps the
      plan from pulling over to save a few cents, and is not part of the
      reported fuel cost.

Algorithm (exact dynamic programming over stations x fuel-in-tank):
    Stations within a corridor around the route are found with a 3D KD-tree
    (one vectorized query of every station against the route polyline) and
    sorted by route mile. Driving past a station is the same as stopping and
    buying nothing, so the DP only moves from each station to the next one.
    State = fuel in the tank on arrival, discretized to 0.1 gal. At each
    station, "buy up to level g" is a prefix-minimum over arrival levels, so
    one station costs O(L) numpy operations and the whole route O(n * L):
    a few milliseconds for a 2,800-mile route with ~450 stations.
"""

import os
import time
import logging
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from django.conf import settings

logger = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.8

# Cost assigned to pulling over for fuel, used only when choosing stops.
STOP_PENALTY_DOLLARS = 10.0
# Stations this close to the start point are candidates for the first fill-up.
ORIGIN_RADIUS_MILES = 25.0
# Widest corridor tried before declaring a route infeasible.
MAX_CORRIDOR_MILES = 35.0

_STATION_CACHE = None


class RouteInfeasibleError(ValueError):
    """Raised when the route has a stretch longer than the vehicle range with no stations."""


def _to_cartesian(lats, lons):
    lat_rad = np.radians(lats)
    lon_rad = np.radians(lons)
    return np.column_stack((
        EARTH_RADIUS_MILES * np.cos(lat_rad) * np.cos(lon_rad),
        EARTH_RADIUS_MILES * np.cos(lat_rad) * np.sin(lon_rad),
        EARTH_RADIUS_MILES * np.sin(lat_rad),
    ))


def _get_fuel_stations_index():
    """
    Loads geocoded fuel stations from data/fuel_stations_geocoded.csv
    and constructs a 3D spatial KD-tree in memory (built once per process).
    """
    global _STATION_CACHE
    if _STATION_CACHE is not None:
        return _STATION_CACHE

    csv_path = os.path.join(settings.BASE_DIR, 'data', 'fuel_stations_geocoded.csv')
    if not os.path.exists(csv_path):
        from routing.data_builder import build_geocoded_fuel_dataset
        build_geocoded_fuel_dataset()

    df = pd.read_csv(csv_path)
    df['address'] = df['address'].fillna('')
    coords_3d = _to_cartesian(df['latitude'].values, df['longitude'].values)

    _STATION_CACHE = {
        'df': df,
        'coords_3d': coords_3d,
        'tree': cKDTree(coords_3d),
    }
    logger.info(f"Loaded {len(df)} geocoded fuel stations into spatial index.")
    return _STATION_CACHE


def haversine_distances(lats, lons):
    """Calculates haversine distance in miles between consecutive coordinates."""
    dlat = np.radians(lats[1:] - lats[:-1])
    dlon = np.radians(lons[1:] - lons[:-1])
    a = (np.sin(dlat / 2.0) ** 2 +
         np.cos(np.radians(lats[:-1])) * np.cos(np.radians(lats[1:])) * np.sin(dlon / 2.0) ** 2)
    c = 2.0 * np.arcsin(np.sqrt(a))
    return EARTH_RADIUS_MILES * c


def _route_candidates(index_data, route_tree, route_cum_miles, corridor_miles, exclude):
    """Returns stations within `corridor_miles` of the route, sorted by route mile."""
    deviations, nearest_vertex = route_tree.query(
        index_data['coords_3d'], distance_upper_bound=corridor_miles
    )
    mask = np.isfinite(deviations)
    candidates = index_data['df'][mask].copy()
    candidates['deviation_miles'] = deviations[mask]
    candidates['route_mile'] = route_cum_miles[nearest_vertex[mask]]
    candidates = candidates[~candidates.index.isin(exclude)]
    return candidates.sort_values(['route_mile', 'retail_price'])


def _origin_candidates(index_data, start_3d):
    """Stations near the start point (or the single nearest one if none are close)."""
    idx = index_data['tree'].query_ball_point(start_3d, r=ORIGIN_RADIUS_MILES)
    if not idx:
        idx = [int(index_data['tree'].query(start_3d)[1])]
    origin = index_data['df'].iloc[idx].copy()
    origin['deviation_miles'] = np.linalg.norm(index_data['coords_3d'][idx] - start_3d, axis=1)
    origin['route_mile'] = 0.0
    return origin


def _find_largest_gap(miles):
    gaps = np.diff(miles)
    k = int(np.argmax(gaps))
    return float(miles[k]), float(miles[k + 1]), float(gaps[k])


def _plan_purchases(miles, prices, stop_penalties, max_range, mpg):
    """
    Minimum-cost purchase plan (fuel cost + stop penalties).

    `miles` / `prices` / `stop_penalties` describe stations in route order; the
    last entry is the destination (its price is ignored). The vehicle reaches
    the first station with an empty tank. Returns a list of
    (station_index, gallons_bought, fuel_on_arrival) tuples, or None if no
    feasible plan exists.
    """
    tank = max_range / mpg
    step = max(0.1, tank / 1000.0)
    n_levels = int(round(tank / step)) + 1
    levels = np.arange(n_levels) * step
    level_idx = np.arange(n_levels)
    # Cumulative fuel burned, in grid units; rounding the running total (not
    # each leg) keeps the discretization error below half a step overall.
    burn = np.round(np.asarray(miles) / mpg / step).astype(int)

    dest = len(miles) - 1
    value = np.full(n_levels, np.inf)
    value[0] = 0.0
    sources = []

    for i in range(dest):
        # Buy phase: best[g] = min(value[g], penalty + p*g + min_{f<=g}(value[f] - p*f))
        adjusted = value - prices[i] * levels
        prefix_min = np.minimum.accumulate(adjusted)
        prefix_arg = np.maximum.accumulate(np.where(adjusted == prefix_min, level_idx, 0))
        buy_value = prefix_min + prices[i] * levels + stop_penalties[i]
        buy = buy_value < value
        sources.append(np.where(buy, prefix_arg, level_idx))
        after_buy = np.where(buy, buy_value, value)

        # Drive phase: burn fuel to reach the next station
        shift = burn[i + 1] - burn[i]
        value = np.full(n_levels, np.inf)
        if shift < n_levels:
            value[:n_levels - shift] = after_buy[shift:]
        if not np.isfinite(value).any():
            return None

    level = int(np.argmin(value))
    plan = []
    for i in range(dest - 1, -1, -1):
        level += burn[i + 1] - burn[i]
        arrival = int(sources[i][level])
        if arrival != level:
            plan.append((i, (level - arrival) * step, arrival * step))
        level = arrival
    plan.reverse()
    return plan


def optimize_fuel_route(route_coordinates, total_road_miles, max_range=500.0, mpg=10.0, max_deviation_miles=10.0):
    """
    Calculates the optimal fuel stops and total fuel cost for a given route.

    Args:
        route_coordinates: list of [lon, lat] from OSRM
        total_road_miles: total road distance in miles from OSRM
        max_range: maximum vehicle range on a single tank (default 500.0)
        mpg: miles per gallon fuel economy (default 10.0)
        max_deviation_miles: max allowed distance of a fuel station off the route.
            Widened automatically (up to 35 mi) if the route has a gap longer than the range.

    Returns:
        dict containing total_fuel_cost, total_fuel_gallons, fuel_stops list, etc.

    Raises:
        RouteInfeasibleError if no refueling plan exists with the available stations.
    """
    t_start = time.perf_counter()
    index_data = _get_fuel_stations_index()

    coords = np.asarray(route_coordinates, dtype=float)
    route_lons = coords[:, 0]
    route_lats = coords[:, 1]

    # Cumulative distance along the polyline, scaled to the router's road mileage
    seg_dists = np.zeros(len(coords))
    if len(coords) > 1:
        seg_dists[1:] = haversine_distances(route_lats, route_lons)
    haversine_cum = np.cumsum(seg_dists)
    hav_total = haversine_cum[-1] if haversine_cum[-1] > 0 else 1.0
    route_cum_miles = haversine_cum * (total_road_miles / hav_total)

    route_3d = _to_cartesian(route_lats, route_lons)
    route_tree = cKDTree(route_3d)

    origin = _origin_candidates(index_data, route_3d[0])

    corridor = max_deviation_miles
    while True:
        en_route = _route_candidates(index_data, route_tree, route_cum_miles, corridor, origin.index)
        miles = np.concatenate((
            origin['route_mile'].values, en_route['route_mile'].values, [total_road_miles]
        ))
        gap_start, gap_end, gap = _find_largest_gap(miles)
        if gap <= max_range:
            break
        if corridor >= MAX_CORRIDOR_MILES:
            raise RouteInfeasibleError(
                f"No refueling plan is possible: the fuel price list has no stations between "
                f"mile {gap_start:.0f} and mile {gap_end:.0f} of this route ({gap:.0f} miles), "
                f"which is more than the vehicle's {max_range:.0f}-mile range."
            )
        corridor = min(MAX_CORRIDOR_MILES, corridor + 12.5)
        logger.info(f"Route gap of {gap:.0f} mi exceeds range; widening corridor to {corridor} mi")

    stations = pd.concat([origin, en_route])
    station_prices = stations['retail_price'].values
    detour_fuel_cost = 2.0 * stations['deviation_miles'].values / mpg * station_prices
    prices = np.append(station_prices, 0.0)
    stop_penalties = np.append(STOP_PENALTY_DOLLARS + detour_fuel_cost, 0.0)

    plan = _plan_purchases(miles, prices, stop_penalties, max_range, mpg)
    if plan is None:
        raise RouteInfeasibleError(
            f"No refueling plan keeps every leg within the vehicle's {max_range:.0f}-mile range."
        )

    fuel_stops = []
    total_cost = 0.0
    total_gallons = 0.0
    prev_mile = 0.0
    for node, gallons, arrival_fuel in plan:
        station = stations.iloc[node]
        price = float(station['retail_price'])
        gallons = round(gallons, 2)
        cost = round(gallons * price, 2)
        total_cost += cost
        total_gallons += gallons
        fuel_stops.append({
            'stop_number': len(fuel_stops) + 1,
            'stop_type': 'origin' if node < len(origin) else 'en_route',
            'opis_id': int(station['opis_id']),
            'name': str(station['name']),
            'address': str(station['address']),
            'city': str(station['city']),
            'state': str(station['state']),
            'retail_price': round(price, 3),
            'route_mile': round(float(miles[node]), 1),
            'leg_distance_miles': round(float(miles[node]) - prev_mile, 1),
            'fuel_on_arrival_gallons': round(arrival_fuel, 2),
            'gallons_pumped': gallons,
            'cost_at_stop': cost,
            'miles_off_route': round(float(station['deviation_miles']), 2),
            'latitude': float(station['latitude']),
            'longitude': float(station['longitude']),
        })
        prev_mile = float(miles[node])

    total_cost = round(total_cost, 2)
    total_gallons = round(total_gallons, 2)
    return {
        'total_distance_miles': total_road_miles,
        'total_fuel_gallons': total_gallons,
        'total_fuel_cost': total_cost,
        'average_price_per_gallon': round(total_cost / total_gallons, 3) if total_gallons else 0.0,
        'fuel_stops_count': len(fuel_stops),
        'fuel_stops': fuel_stops,
        'final_leg_miles': round(total_road_miles - prev_mile, 1),
        'stations_considered': len(stations),
        'search_corridor_miles': corridor,
        'mpg': mpg,
        'max_vehicle_range_miles': max_range,
        'calculation_time_ms': round((time.perf_counter() - t_start) * 1000, 2),
    }
