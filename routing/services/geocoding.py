"""
Geocoding service for resolving US locations to latitude/longitude coordinates.
Uses an ultra-fast local database of ~30,000 US cities as the primary lookup,
with fallback to OpenStreetMap Nominatim for street addresses or uncommon queries.
All results are cached in Django's cache.
"""

import os
import re
import urllib.request
import urllib.parse
import json
import logging
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)

# Bounding box for Continental United States (plus padding)
US_BOUNDS = {
    'min_lat': 24.0,
    'max_lat': 50.0,
    'min_lon': -125.5,
    'max_lon': -66.0,
}

_US_CITIES_INDEX = None

def _load_us_cities_index():
    global _US_CITIES_INDEX
    if _US_CITIES_INDEX is not None:
        return _US_CITIES_INDEX

    _US_CITIES_INDEX = {}
    cities_csv = os.path.join(settings.BASE_DIR, 'data', 'us_cities.csv')
    if os.path.exists(cities_csv):
        import pandas as pd
        df = pd.read_csv(cities_csv)
        for _, row in df.iterrows():
            city = str(row['CITY']).strip().upper()
            state = str(row['STATE_CODE']).strip().upper()
            lat = float(row['LATITUDE'])
            lon = float(row['LONGITUDE'])

            # Key format: ("AUSTIN", "TX")
            _US_CITIES_INDEX[(city, state)] = {
                'lat': lat,
                'lon': lon,
                'display_name': f"{row['CITY']}, {state}, USA"
            }
            # Fallback single-name key if not already set
            if city not in _US_CITIES_INDEX:
                _US_CITIES_INDEX[city] = {
                    'lat': lat,
                    'lon': lon,
                    'display_name': f"{row['CITY']}, {state}, USA"
                }

    logger.info(f"Loaded {len(_US_CITIES_INDEX)} US city location records into memory.")
    return _US_CITIES_INDEX


def parse_coordinates(text):
    """
    Check if the input string is already a coordinate pair like '40.7128,-74.0060' or '40.7128, -74.0060'.
    Returns (lat, lon) or None.
    """
    match = re.match(r'^\s*([-+]?\d{1,2}(?:\.\d+)?)\s*,\s*([-+]?\d{1,3}(?:\.\d+)?)\s*$', text.strip())
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return None


def is_within_usa(lat, lon):
    """Checks whether the coordinates fall within Continental USA bounds."""
    return (US_BOUNDS['min_lat'] <= lat <= US_BOUNDS['max_lat'] and
            US_BOUNDS['min_lon'] <= lon <= US_BOUNDS['max_lon'])


def geocode_location(query):
    """
    Geocodes a location string into {'lat': float, 'lon': float, 'display_name': str}.
    Raises ValueError if location cannot be found or is outside the USA.
    """
    if not query or not isinstance(query, str) or not query.strip():
        raise ValueError("Location cannot be empty.")

    query = query.strip()
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', query.lower())
    cache_key = f"geocode_{sanitized}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # 1. Check if raw coordinates
    coords = parse_coordinates(query)
    if coords:
        lat, lon = coords
        if not is_within_usa(lat, lon):
            raise ValueError(f"Coordinates ({lat}, {lon}) are outside the Continental United States.")
        res = {'lat': lat, 'lon': lon, 'display_name': f"{lat:.4f}, {lon:.4f} (Coordinates)"}
        cache.set(cache_key, res, timeout=86400)
        return res

    # 2. Check local US cities database
    cities_index = _load_us_cities_index()
    norm = query.upper().strip()

    # Try matching "City, State" (e.g. "Austin, TX" or "New York, NY")
    parts = [p.strip() for p in norm.split(',')]
    if len(parts) >= 2:
        city_part = parts[0]
        state_part = parts[1][:2]  # State code like TX, NY, CA
        key = (city_part, state_part)
        if key in cities_index:
            entry = cities_index[key]
            res = {'lat': entry['lat'], 'lon': entry['lon'], 'display_name': entry['display_name']}
            cache.set(cache_key, res, timeout=86400)
            return res

    # Try matching simple city name if unique
    if norm in cities_index:
        entry = cities_index[norm]
        res = {'lat': entry['lat'], 'lon': entry['lon'], 'display_name': entry['display_name']}
        cache.set(cache_key, res, timeout=86400)
        return res

    # 3. Fallback to OpenStreetMap Nominatim
    try:
        clean_q = query if 'usa' in query.lower() or 'united states' in query.lower() else f"{query}, USA"
        encoded = urllib.parse.quote(clean_q)
        url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&countrycodes=us&limit=1"
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'FuelRoutingAssistantDjango/1.0 (assessment; pair-programming)'}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data and len(data) > 0:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
                if not is_within_usa(lat, lon):
                    raise ValueError(f"Location '{query}' resolved outside the Continental USA.")
                res = {
                    'lat': lat,
                    'lon': lon,
                    'display_name': data[0].get('display_name', query)
                }
                cache.set(cache_key, res, timeout=86400)
                return res
    except Exception as e:
        logger.warning(f"Nominatim geocoding failed for '{query}': {e}")

    raise ValueError(f"Could not locate '{query}' within the United States. Please specify a valid US city/state or coordinates.")
