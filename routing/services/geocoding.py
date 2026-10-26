"""
Geocoding service for resolving US locations to latitude/longitude coordinates.
Uses an in-memory database of ~30,000 US cities as the primary lookup (no network),
with fallback to OpenStreetMap Nominatim for street addresses, landmarks and
ambiguous bare city names (e.g. "Springfield"). All results are cached.
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

STATE_NAME_TO_CODE = {
    'ALABAMA': 'AL', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR', 'CALIFORNIA': 'CA',
    'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE', 'DISTRICT OF COLUMBIA': 'DC',
    'FLORIDA': 'FL', 'GEORGIA': 'GA', 'IDAHO': 'ID', 'ILLINOIS': 'IL', 'INDIANA': 'IN',
    'IOWA': 'IA', 'KANSAS': 'KS', 'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME',
    'MARYLAND': 'MD', 'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN',
    'MISSISSIPPI': 'MS', 'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE',
    'NEVADA': 'NV', 'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM',
    'NEW YORK': 'NY', 'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH',
    'OKLAHOMA': 'OK', 'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI',
    'SOUTH CAROLINA': 'SC', 'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX',
    'UTAH': 'UT', 'VERMONT': 'VT', 'VIRGINIA': 'VA', 'WASHINGTON': 'WA',
    'WEST VIRGINIA': 'WV', 'WISCONSIN': 'WI', 'WYOMING': 'WY',
    'ALASKA': 'AK', 'HAWAII': 'HI',
}

_US_CITIES_INDEX = None


def _load_us_cities_index():
    """
    Builds two lookups from data/us_cities.csv:
      'by_city_state': {("AUSTIN", "TX"): {...}}
      'by_city':       {"AUSTIN": [{...}, ...]}  (every state with that city name)
    """
    global _US_CITIES_INDEX
    if _US_CITIES_INDEX is not None:
        return _US_CITIES_INDEX

    by_city_state, by_city = {}, {}
    cities_csv = os.path.join(settings.BASE_DIR, 'data', 'us_cities.csv')
    if os.path.exists(cities_csv):
        import pandas as pd
        from routing.data_builder import dedupe_city_rows
        df = dedupe_city_rows(pd.read_csv(cities_csv))
        df['key_city'] = df['CITY'].astype(str).str.strip().str.upper()
        df['key_state'] = df['STATE_CODE'].astype(str).str.strip().str.upper()

        for city, state, name, lat, lon in zip(
            df['key_city'], df['key_state'], df['CITY'], df['LATITUDE'], df['LONGITUDE']
        ):
            entry = {'lat': float(lat), 'lon': float(lon), 'display_name': f"{name}, {state}, USA"}
            by_city_state[(city, state)] = entry
            by_city.setdefault(city, []).append(entry)

    _US_CITIES_INDEX = {'by_city_state': by_city_state, 'by_city': by_city}
    logger.info(f"Loaded {len(by_city_state)} US city location records into memory.")
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


def _parse_state(text):
    """Turns 'TX', 'tx 78701', 'Texas' or 'Texas, USA' into a 2-letter code, or None."""
    text = re.sub(r'\b\d{5}(-\d{4})?\b', '', text).strip().upper()
    if text in STATE_NAME_TO_CODE:
        return STATE_NAME_TO_CODE[text]
    if re.fullmatch(r'[A-Z]{2}', text):
        return text
    return None


def _lookup_local(query):
    """
    Resolves "City, ST", "City, State Name" or a bare city name against the local DB.
    Returns (entry, ambiguous_matches) where entry may be None.
    """
    index = _load_us_cities_index()
    parts = [p.strip() for p in query.upper().split(',') if p.strip()]
    if parts and parts[-1] in ('USA', 'US', 'UNITED STATES', 'UNITED STATES OF AMERICA'):
        parts = parts[:-1]
    if not parts:
        return None, []

    if len(parts) >= 2:
        state = _parse_state(parts[1])
        if state:
            return index['by_city_state'].get((parts[0], state)), []
        return None, []

    matches = index['by_city'].get(parts[0], [])
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def _nominatim(query):
    clean_q = query if 'usa' in query.lower() or 'united states' in query.lower() else f"{query}, USA"
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({'q': clean_q, 'format': 'json', 'countrycodes': 'us', 'limit': 1})
    )
    req = urllib.request.Request(
        url, headers={'User-Agent': 'FuelRoutingAssistantDjango/1.0 (assessment)'}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if not data:
        return None
    return {
        'lat': float(data[0]['lat']),
        'lon': float(data[0]['lon']),
        'display_name': data[0].get('display_name', query),
    }


def geocode_location(query):
    """
    Geocodes a location string into {'lat': float, 'lon': float, 'display_name': str}.
    Raises ValueError if location cannot be found or is outside the continental USA.
    """
    if not query or not isinstance(query, str) or not query.strip():
        raise ValueError("Location cannot be empty.")

    query = query.strip()
    cache_key = "geocode_" + re.sub(r'[^a-z0-9]+', '_', query.lower())
    cached = cache.get(cache_key)
    if cached:
        return cached

    result = None

    # 1. Raw "lat,lon" coordinates
    coords = parse_coordinates(query)
    if coords:
        lat, lon = coords
        result = {'lat': lat, 'lon': lon, 'display_name': f"{lat:.4f}, {lon:.4f}"}

    # 2. Local US cities database (no network)
    ambiguous = []
    if result is None:
        result, ambiguous = _lookup_local(query)

    # 3. Nominatim for addresses, landmarks and ambiguous city names
    if result is None:
        try:
            result = _nominatim(query)
        except Exception as e:
            logger.warning(f"Nominatim geocoding failed for '{query}': {e}")
        if result is None and ambiguous:
            # Offline fallback for a bare name like "Springfield": pick the first match
            result = ambiguous[0]

    if result is None:
        raise ValueError(
            f"Could not locate '{query}' in the United States. "
            "Try 'City, ST' (e.g. 'Denver, CO'), a full address, or 'lat,lon' coordinates."
        )
    if not is_within_usa(result['lat'], result['lon']):
        raise ValueError(
            f"'{query}' resolves to ({result['lat']:.4f}, {result['lon']:.4f}), "
            "which is outside the continental United States."
        )

    cache.set(cache_key, result, timeout=86400)
    return result
