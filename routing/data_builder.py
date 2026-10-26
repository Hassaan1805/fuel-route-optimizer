"""
Data builder script to generate pre-geocoded fuel stations dataset.
Merges fuel-prices-for-be-assessment.csv with US Cities geographic database.
Outputs data/fuel_stations_geocoded.csv for high-speed in-memory indexing.
"""

import os
import io
import urllib.request
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

RAW_FUEL_CSV = os.path.join(BASE_DIR, 'fuel-prices-for-be-assessment.csv')
CITIES_CSV = os.path.join(DATA_DIR, 'us_cities.csv')
GEOCODED_CSV = os.path.join(DATA_DIR, 'fuel_stations_geocoded.csv')

def download_cities_database():
    if not os.path.exists(CITIES_CSV):
        print(f"Downloading US Cities database to {CITIES_CSV}...")
        url = 'https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
            with open(CITIES_CSV, 'wb') as f:
                f.write(content)
        print("US Cities database downloaded successfully.")
    else:
        print("US Cities database already exists locally.")

def dedupe_city_rows(cities_df):
    """
    Returns one row per (CITY, STATE_CODE). The cities database lists some cities
    once per county they touch (e.g. Austin, TX in Hays, Travis and Williamson);
    prefer the county named after the city, otherwise the row nearest the others.
    """
    key = [cities_df['CITY'].str.strip().str.upper(), cities_df['STATE_CODE'].str.strip().str.upper()]
    dup_mask = cities_df.duplicated(subset=['CITY', 'STATE_CODE'], keep=False)

    def pick(group):
        same_county = group[group['COUNTY'].astype(str).str.upper() == group['CITY'].iloc[0].upper()]
        if len(same_county):
            return same_county.index[0]
        pts = group[['LATITUDE', 'LONGITUDE']].values
        spread = np.abs(pts[:, None, :] - pts[None, :, :]).sum(axis=(1, 2))
        return group.index[int(np.argmin(spread))]

    dups = cities_df[dup_mask]
    keep_idx = dups.groupby([k[dup_mask] for k in key]).apply(pick, include_groups=False) if len(dups) else []
    return pd.concat([cities_df[~dup_mask], cities_df.loc[list(keep_idx)]]).sort_index()


def build_geocoded_fuel_dataset():
    download_cities_database()

    print("Loading data files...")
    fuel_df = pd.read_csv(RAW_FUEL_CSV)
    cities_df = dedupe_city_rows(pd.read_csv(CITIES_CSV))

    # Standardize city and state strings
    fuel_df['city_clean'] = fuel_df['City'].astype(str).str.strip().str.upper()
    fuel_df['state_clean'] = fuel_df['State'].astype(str).str.strip().str.upper()
    cities_df['city_clean'] = cities_df['CITY'].astype(str).str.strip().str.upper()
    cities_df['state_clean'] = cities_df['STATE_CODE'].astype(str).str.strip().str.upper()

    cities_map = cities_df.drop_duplicates(subset=['city_clean', 'state_clean']).set_index(
        ['city_clean', 'state_clean']
    )[['LATITUDE', 'LONGITUDE']].to_dict(orient='index')

    # Known edge case cities in continental US
    overrides = {
        ('PORT WENTWORTH', 'GA'): (32.1494, -81.1632),
        ('ELIZABETHPORT', 'NJ'): (40.6482, -74.1957),
        ('BROOKPARK', 'OH'): (41.3995, -81.8157),
        ('EVERGREEN', 'AL'): (31.4338, -86.9544),
        ('HENRICO', 'VA'): (37.5538, -77.4042),
        ('UNIVERSITY PARK', 'IL'): (41.4442, -87.6864),
    }

    lats, lons = [], []
    for _, row in fuel_df.iterrows():
        key = (row['city_clean'], row['state_clean'])
        if key in overrides:
            lat, lon = overrides[key]
        elif key in cities_map:
            lat, lon = cities_map[key]['LATITUDE'], cities_map[key]['LONGITUDE']
        else:
            lat, lon = None, None
        lats.append(lat)
        lons.append(lon)

    fuel_df['latitude'] = lats
    fuel_df['longitude'] = lons

    # Filter to valid US stations with coordinates
    us_stations = fuel_df[fuel_df['latitude'].notna()].copy()

    # Deduplicate stations by OPIS ID, keeping the minimum (most competitive) retail price
    deduped = us_stations.sort_values('Retail Price').groupby(
        'OPIS Truckstop ID', as_index=False
    ).first()

    clean_columns = {
        'OPIS Truckstop ID': 'opis_id',
        'Truckstop Name': 'name',
        'Address': 'address',
        'City': 'city',
        'State': 'state',
        'Rack ID': 'rack_id',
        'Retail Price': 'retail_price',
        'latitude': 'latitude',
        'longitude': 'longitude'
    }

    result_df = deduped[list(clean_columns.keys())].rename(columns=clean_columns)
    result_df['name'] = result_df['name'].str.strip()
    result_df['address'] = result_df['address'].str.strip()
    result_df['city'] = result_df['city'].str.strip()
    result_df['state'] = result_df['state'].str.strip()

    result_df.to_csv(GEOCODED_CSV, index=False)
    print(f"Built geocoded fuel dataset at {GEOCODED_CSV} with {len(result_df)} unique US stations.")
    print("Price stats:")
    print(result_df['retail_price'].describe())

if __name__ == '__main__':
    build_geocoded_fuel_dataset()
