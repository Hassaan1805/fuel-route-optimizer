"""
OSRM Routing Service.
Calls the Open Source Routing Machine (OSRM) public driving API to compute the route,
total road distance, driving duration, and GeoJSON polyline geometry.
Results are cached in Django's cache to minimize external network requests.
"""

import urllib.request
import json
import logging
from django.core.cache import cache

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"

def get_driving_route(start_lat, start_lon, finish_lat, finish_lon):
    """
    Computes road route between two US points using OSRM.
    Returns:
        {
            'distance_miles': float,
            'duration_hours': float,
            'coordinates': list of [lon, lat],
            'geojson': dict
        }
    """
    # Round coordinates to 4 decimal places for caching (~11m precision)
    cache_key = f"osrm_route_{round(start_lat, 4)}_{round(start_lon, 4)}_{round(finish_lat, 4)}_{round(finish_lon, 4)}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"Returning cached OSRM route for {cache_key}")
        return cached

    # OSRM expects coordinates in {lon},{lat};{lon},{lat} order
    url = f"{OSRM_BASE_URL}/{start_lon},{start_lat};{finish_lon},{finish_lat}?overview=full&geometries=geojson"

    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'FuelRoutingAssistantDjango/1.0 (assessment; pair-programming)'}
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.error(f"OSRM request failed: {e}")
        raise RuntimeError(f"Failed to fetch route from routing engine: {str(e)}")

    if data.get('code') != 'Ok' or not data.get('routes'):
        err_msg = data.get('message', 'No driving route found between specified locations.')
        raise ValueError(f"Routing error: {err_msg}")

    route = data['routes'][0]
    distance_meters = route.get('distance', 0.0)
    duration_seconds = route.get('duration', 0.0)
    coordinates = route.get('geometry', {}).get('coordinates', [])

    if not coordinates:
        raise ValueError("Routing service returned empty path coordinates.")

    distance_miles = round(distance_meters * 0.000621371, 2)
    duration_hours = round(duration_seconds / 3600.0, 2)

    result = {
        'distance_miles': distance_miles,
        'duration_hours': duration_hours,
        'coordinates': coordinates,
        'geojson': {
            'type': 'LineString',
            'coordinates': coordinates
        }
    }

    cache.set(cache_key, result, timeout=86400)
    return result
