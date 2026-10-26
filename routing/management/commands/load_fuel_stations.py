import os
import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
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

        # The CSV is the source of truth: replace the table so re-running after a
        # data rebuild picks up corrected prices and coordinates.
        stations_to_create = []
        for _, row in df.iterrows():
            opis_id = int(row['opis_id'])
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

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations_to_create, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(stations_to_create)} fuel stations."))
