import os
import pandas as pd
from django.core.management.base import BaseCommand
from routing.models import FuelStation
from routing.data_builder import build_geocoded_fuel_dataset, GEOCODED_CSV

class Command(BaseCommand):
    help = 'Load pre-geocoded fuel stations into the database'

    def handle(self, *args, **options):
        if not os.path.exists(GEOCODED_CSV):
            self.stdout.write(self.style.WARNING("Geocoded CSV not found. Generating..."))
            build_geocoded_fuel_dataset()

        self.stdout.write(f"Reading from {GEOCODED_CSV}...")
        df = pd.read_csv(GEOCODED_CSV)

        stations_to_create = []
        existing_ids = set(FuelStation.objects.values_list('opis_id', flat=True))

        for _, row in df.iterrows():
            opis_id = int(row['opis_id'])
            if opis_id in existing_ids:
                continue

            stations_to_create.append(
                FuelStation(
                    opis_id=opis_id,
                    name=str(row['name'])[:255],
                    address=str(row['address'])[:255] if pd.notna(row['address']) else '',
                    city=str(row['city'])[:100],
                    state=str(row['state'])[:10],
                    rack_id=int(row['rack_id']) if pd.notna(row['rack_id']) else None,
                    retail_price=round(float(row['retail_price']), 3),
                    latitude=float(row['latitude']),
                    longitude=float(row['longitude'])
                )
            )

        if stations_to_create:
            self.stdout.write(f"Inserting {len(stations_to_create)} stations...")
            FuelStation.objects.bulk_create(stations_to_create, batch_size=1000)
            self.stdout.write(self.style.SUCCESS(f"Successfully loaded {len(stations_to_create)} fuel stations!"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Database already up to date with {len(existing_ids)} stations."))
