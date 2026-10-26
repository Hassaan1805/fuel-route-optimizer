"""
OSRM Routing Service.
Calls the Open Source Routing Machine (OSRM) public driving API once per trip to get
the route, total road distance, driving duration, and GeoJSON polyline geometry.
Results are cached in Django's cache so repeated trips make zero external calls.
"""

import urllib.error
import urllib.request
import json
import logging
import numpy as np
from django.core.cache import cache

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"

# ~50 m in degrees; keeps the drawn line visually identical while cutting
# a cross-country route from ~35k points to a few thousand.
DISPLAY_TOLERANCE_DEG = 0.0005


def simplify_coordinates(coords, tolerance=DISPLAY_TOLERANCE_DEG):
    """Ramer-Douglas-Peucker simplification of a [lon, lat] polyline."""
    pts = np.asarray(coords, dtype=float)
    n = len(pts)
    if n < 3:
        return pts.round(5).tolist()

    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        seg = pts[end] - pts[start]
        rel = pts[start + 1:end] - pts[start]
        seg_len = np.hypot(seg[0], seg[1])
        if seg_len == 0:
            dists = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dists = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / seg_len
        k = int(np.argmax(dists))
        if dists[k] > tolerance:
            mid = start + 1 + k
            keep[mid] = True
            stack.append((start, mid))
            stack.append((mid, end))
    return pts[keep].round(5).tolist()


def get_driving_route(start_lat, start_lon, finish_lat, finish_lon):
    """
    Computes road route between two US points using OSRM.
    Returns:
        {
            'distance_miles': float,
            'duration_hours': float,
            'coordinates': list of [lon, lat] (full resolution, used for the fuel search),
            'geojson': dict (simplified LineString for drawing the map),
            'from_cache': bool (True when no external call was made)
        }
    """
    # Round coordinates to 4 decimal places for caching (~11m precision)
    cache_key = f"osrm_route_{round(start_lat, 4)}_{round(start_lon, 4)}_{round(finish_lat, 4)}_{round(finish_lon, 4)}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"Returning cached OSRM route for {cache_key}")
        return {**cached, 'from_cache': True}

    # OSRM expects coordinates in {lon},{lat};{lon},{lat} order
    url = f"{OSRM_BASE_URL}/{start_lon},{start_lat};{finish_lon},{finish_lat}?overview=full&geometries=geojson"

    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'FuelRoutingAssistantDjango/1.0 (assessment)'}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # OSRM reports "no route" as a 400 with a JSON body
        try:
            data = json.loads(e.read().decode('utf-8'))
        except Exception:
            raise RuntimeError(f"Routing engine returned HTTP {e.code}.")
    except Exception as e:
        logger.error(f"OSRM request failed: {e}")
        raise RuntimeError(f"Could not reach the routing engine: {e}")

    if data.get('code') != 'Ok' or not data.get('routes'):
        err_msg = data.get('message', 'No driving route found between the specified locations.')
        raise ValueError(f"Routing error: {err_msg}")

    route = data['routes'][0]
    coordinates = route.get('geometry', {}).get('coordinates', [])
    if not coordinates:
        raise ValueError("Routing service returned empty path coordinates.")

    result = {
        'distance_miles': round(route.get('distance', 0.0) * 0.000621371, 2),
        'duration_hours': round(route.get('duration', 0.0) / 3600.0, 2),
        'coordinates': coordinates,
        'geojson': {
            'type': 'LineString',
            'coordinates': simplify_coordinates(coordinates),
        },
    }

    cache.set(cache_key, result, timeout=86400)
    return {**result, 'from_cache': False}
