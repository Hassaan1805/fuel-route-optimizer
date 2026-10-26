"""
Fuel Optimization Service.
Uses 3D Spatial KD-Tree indexing and Dynamic Programming (DAG Shortest Path)
to compute the most cost-effective sequence of fuel stops along a driving route.
Guarantees vehicle never exceeds its 500-mile range while minimizing total fuel expenditure.
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

_STATION_CACHE = None

def _get_fuel_stations_index():
    """
    Loads geocoded fuel stations from data/fuel_stations_geocoded.csv
    and constructs a 3D spatial KD-tree in memory (built once).
    """
    global _STATION_CACHE
    if _STATION_CACHE is not None:
        return _STATION_CACHE

    csv_path = os.path.join(settings.BASE_DIR, 'data', 'fuel_stations_geocoded.csv')
    if not os.path.exists(csv_path):
        from routing.data_builder import build_geocoded_fuel_dataset
        build_geocoded_fuel_dataset()

    df = pd.read_csv(csv_path)

    # Convert coordinates to 3D Cartesian coordinates on sphere
    lat_rad = np.radians(df['latitude'].values)
    lon_rad = np.radians(df['longitude'].values)
    x = EARTH_RADIUS_MILES * np.cos(lat_rad) * np.cos(lon_rad)
    y = EARTH_RADIUS_MILES * np.cos(lat_rad) * np.sin(lon_rad)
    z = EARTH_RADIUS_MILES * np.sin(lat_rad)
    coords_3d = np.column_stack((x, y, z))

    tree = cKDTree(coords_3d)

    _STATION_CACHE = {
        'df': df,
        'coords_3d': coords_3d,
        'tree': tree
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


def optimize_fuel_route(route_coordinates, total_road_miles, max_range=500.0, mpg=10.0, max_deviation_miles=10.0):
    """
    Calculates the optimal fuel stops and total fuel cost for a given route.

    Args:
        route_coordinates: list of [lon, lat] from OSRM
        total_road_miles: total road distance in miles from OSRM
        max_range: maximum vehicle range on a single tank (default 500.0)
        mpg: miles per gallon fuel economy (default 10.0)
        max_deviation_miles: max allowed distance of a fuel station off the highway route

    Returns:
        dict containing total_fuel_cost, total_gallons, fuel_stops list, etc.
    """
    t_start = time.time()
    index_data = _get_fuel_stations_index()
    stations_df = index_data['df']
    station_coords_3d = index_data['coords_3d']
    station_tree = index_data['tree']

    coords = np.array(route_coordinates)
    route_lons = coords[:, 0]
    route_lats = coords[:, 1]

    # Compute cumulative haversine distance and scale to OSRM road distance
    seg_dists = np.zeros(len(coords))
    seg_dists[1:] = haversine_distances(route_lats, route_lons)
    haversine_cum = np.cumsum(seg_dists)
    hav_total = haversine_cum[-1] if haversine_cum[-1] > 0 else 1.0

    # Scaling factor to reconcile great-circle segments with road network mileage
    scale_factor = total_road_miles / hav_total
    route_cum_miles = haversine_cum * scale_factor

    # Convert full route to 3D Cartesian coordinates
    rx = EARTH_RADIUS_MILES * np.cos(np.radians(route_lats)) * np.cos(np.radians(route_lons))
    ry = EARTH_RADIUS_MILES * np.cos(np.radians(route_lats)) * np.sin(np.radians(route_lons))
    rz = EARTH_RADIUS_MILES * np.sin(np.radians(route_lats))
    route_3d = np.column_stack((rx, ry, rz))
    route_tree = cKDTree(route_3d)

    # 1. Spatial search: find candidate stations along route corridor
    # Downsample route points to ~1000 sample points for high query speed
    step = max(1, len(route_3d) // 1000)
    sample_route_3d = route_3d[::step]

    # Search with adaptive corridor expansion if initial candidate count is low
    corridor_search_r = max_deviation_miles
    nearby_station_indices = set()
    for pt_indices in station_tree.query_ball_point(sample_route_3d, r=corridor_search_r):
        nearby_station_indices.update(pt_indices)

    # If corridor is sparse (e.g. rural routes), expand up to 20 miles
    if len(nearby_station_indices) < 5 and corridor_search_r < 20.0:
        corridor_search_r = 20.0
        for pt_indices in station_tree.query_ball_point(sample_route_3d, r=corridor_search_r):
            nearby_station_indices.update(pt_indices)

    candidate_indices = list(nearby_station_indices)
    if candidate_indices:
        candidates = stations_df.iloc[candidate_indices].copy()
        cand_coords_3d = station_coords_3d[candidate_indices]
        deviations, closest_route_indices = route_tree.query(cand_coords_3d)
        candidates['route_mile'] = route_cum_miles[closest_route_indices]
        candidates['deviation_miles'] = deviations
        valid_candidates = candidates[candidates['deviation_miles'] <= corridor_search_r].sort_values('route_mile').reset_index(drop=True)
    else:
        valid_candidates = pd.DataFrame()

    total_gallons = round(total_road_miles / mpg, 2)

    # CASE A: Short route within vehicle single tank range (<= 500 miles)
    if total_road_miles <= max_range:
        # Check if there is a cheap station along the route if driver wants to refuel
        if not valid_candidates.empty:
            cheapest_station = valid_candidates.sort_values('retail_price').iloc[0]
            avg_price = float(cheapest_station['retail_price'])
        else:
            avg_price = 3.25

        total_cost = round(total_gallons * avg_price, 2)
        return {
            'total_distance_miles': total_road_miles,
            'total_fuel_gallons': total_gallons,
            'total_fuel_cost': total_cost,
            'average_price_per_gallon': round(avg_price, 3),
            'fuel_stops_count': 0,
            'fuel_stops': [],
            'mpg': mpg,
            'max_vehicle_range_miles': max_range,
            'note': f"The total route distance ({total_road_miles} mi) is within the vehicle's {max_range}-mile maximum range. No refueling stops are strictly required to complete the trip.",
            'calculation_time_ms': round((time.time() - t_start) * 1000, 2)
        }

    # CASE B: Long route requiring multiple fuel stops (> 500 miles)
    if valid_candidates.empty:
        raise ValueError("No fuel stations found along the route within search corridor.")

    # Build Directed Acyclic Graph (DAG) for Penalized DP
    # Safe range leaves a small buffer so the driver never reaches 0% fuel on the highway
    SAFE_RANGE = min(max_range, max_range - 10.0) # e.g. 490 miles

    nodes = [{
        'id': 0,
        'opis_id': 0,
        'name': 'START',
        'address': 'Origin',
        'city': '',
        'state': '',
        'mile': 0.0,
        'price': float(valid_candidates['retail_price'].median()),
        'latitude': float(route_lats[0]),
        'longitude': float(route_lons[0]),
        'deviation': 0.0
    }]

    for _, row in valid_candidates.iterrows():
        nodes.append({
            'id': len(nodes),
            'opis_id': int(row['opis_id']),
            'name': str(row['name']),
            'address': str(row['address']) if pd.notna(row['address']) else '',
            'city': str(row['city']),
            'state': str(row['state']),
            'mile': float(row['route_mile']),
            'price': float(row['retail_price']),
            'latitude': float(row['latitude']),
            'longitude': float(row['longitude']),
            'deviation': float(row['deviation_miles'])
        })

    nodes.append({
        'id': len(nodes),
        'opis_id': -1,
        'name': 'FINISH',
        'address': 'Destination',
        'city': '',
        'state': '',
        'mile': float(total_road_miles),
        'price': 0.0,
        'latitude': float(route_lats[-1]),
        'longitude': float(route_lons[-1]),
        'deviation': 0.0
    })

    N = len(nodes)
    dp = [float('inf')] * N
    prev = [-1] * N
    dp[0] = 0.0

    # Stop penalty ($7.50) balances minimizing price while discouraging excessive, unnecessary stops
    STOP_PENALTY = 7.50

    for i in range(N):
        if dp[i] == float('inf'):
            continue
        mile_i = nodes[i]['mile']
        for j in range(i + 1, N):
            dist = nodes[j]['mile'] - mile_i
            if dist > SAFE_RANGE:
                # If no node was reachable before SAFE_RANGE, allow up to max_range
                if j == i + 1 and dist <= max_range:
                    pass
                else:
                    break
            if dist <= 0:
                continue

            # Price for fuel consumed on leg i -> j
            if j == N - 1:
                # Last leg to destination: fuel is purchased at station i
                leg_price = nodes[i]['price']
            else:
                # Fuel for segment i -> j purchased at station j
                leg_price = nodes[j]['price']

            leg_fuel_cost = (dist / mpg) * leg_price
            penalty = STOP_PENALTY if (j != N - 1 and i != 0) else 0.0
            edge_cost = leg_fuel_cost + penalty

            if dp[i] + edge_cost < dp[j]:
                dp[j] = dp[i] + edge_cost
                prev[j] = i

    # If DP could not reach destination within SAFE_RANGE, retry with strict max_range
    if prev[-1] == -1:
        dp = [float('inf')] * N
        prev = [-1] * N
        dp[0] = 0.0
        for i in range(N):
            if dp[i] == float('inf'):
                continue
            mile_i = nodes[i]['mile']
            for j in range(i + 1, N):
                dist = nodes[j]['mile'] - mile_i
                if dist > max_range:
                    break
                if dist <= 0:
                    continue
                leg_price = nodes[i]['price'] if j == N - 1 else nodes[j]['price']
                leg_fuel_cost = (dist / mpg) * leg_price
                if dp[i] + leg_fuel_cost < dp[j]:
                    dp[j] = dp[i] + leg_fuel_cost
                    prev[j] = i

    if prev[-1] == -1:
        if max_deviation_miles < 35.0:
            logger.info(f"Route had gap exceeding range with {max_deviation_miles} mi corridor. Expanding corridor...")
            return optimize_fuel_route(
                route_coordinates=route_coordinates,
                total_road_miles=total_road_miles,
                max_range=max_range,
                mpg=mpg,
                max_deviation_miles=max_deviation_miles + 15.0
            )
        raise ValueError(
            f"Unable to find a valid refueling path. The route contains a stretch exceeding {max_range} miles "
            "without any accessible fuel stops within the search corridor."
        )

    # Reconstruct optimal path
    path = []
    curr = N - 1
    while curr != -1:
        path.append(curr)
        curr = prev[curr]
    path.reverse()

    # Build fuel stops response list and calculate exact leg costs
    fuel_stops = []
    total_fuel_cost = 0.0
    accumulated_gallons = 0.0

    for k in range(len(path) - 1):
        u_idx = path[k]
        v_idx = path[k + 1]
        u = nodes[u_idx]
        v = nodes[v_idx]

        leg_dist = round(v['mile'] - u['mile'], 2)
        leg_gallons = round(leg_dist / mpg, 2)
        accumulated_gallons += leg_gallons

        if v_idx == N - 1:
            # Final leg to destination: fuel bought at stop u
            leg_price = u['price']
            leg_cost = round(leg_gallons * leg_price, 2)
            total_fuel_cost += leg_cost
        elif u_idx == 0:
            # First leg from start: refueled at stop v
            leg_price = v['price']
            leg_cost = round(leg_gallons * leg_price, 2)
            total_fuel_cost += leg_cost
            fuel_stops.append({
                'stop_number': len(fuel_stops) + 1,
                'opis_id': v['opis_id'],
                'name': v['name'],
                'address': v['address'],
                'city': v['city'],
                'state': v['state'],
                'retail_price': round(v['price'], 3),
                'route_mile': round(v['mile'], 1),
                'leg_distance_miles': leg_dist,
                'gallons_pumped': leg_gallons,
                'cost_at_stop': leg_cost,
                'miles_off_route': round(v['deviation'], 2),
                'latitude': v['latitude'],
                'longitude': v['longitude']
            })
        else:
            # Intermediate leg: refueled at stop v
            leg_price = v['price']
            leg_cost = round(leg_gallons * leg_price, 2)
            total_fuel_cost += leg_cost
            fuel_stops.append({
                'stop_number': len(fuel_stops) + 1,
                'opis_id': v['opis_id'],
                'name': v['name'],
                'address': v['address'],
                'city': v['city'],
                'state': v['state'],
                'retail_price': round(v['price'], 3),
                'route_mile': round(v['mile'], 1),
                'leg_distance_miles': leg_dist,
                'gallons_pumped': leg_gallons,
                'cost_at_stop': leg_cost,
                'miles_off_route': round(v['deviation'], 2),
                'latitude': v['latitude'],
                'longitude': v['longitude']
            })

    total_fuel_cost = round(total_fuel_cost, 2)
    avg_price = round(total_fuel_cost / total_gallons, 3) if total_gallons > 0 else 0.0

    return {
        'total_distance_miles': total_road_miles,
        'total_fuel_gallons': total_gallons,
        'total_fuel_cost': total_fuel_cost,
        'average_price_per_gallon': avg_price,
        'fuel_stops_count': len(fuel_stops),
        'fuel_stops': fuel_stops,
        'mpg': mpg,
        'max_vehicle_range_miles': max_range,
        'calculation_time_ms': round((time.time() - t_start) * 1000, 2)
    }
