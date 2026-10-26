from django.db import models

class FuelStation(models.Model):
    """
    Represents an OPIS truckstop station with geographic location and retail fuel price.
    """
    opis_id = models.IntegerField(unique=True, db_index=True, verbose_name="OPIS ID")
    name = models.CharField(max_length=255, verbose_name="Truckstop Name")
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=10, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=6, decimal_places=3, db_index=True)
    latitude = models.FloatField(db_index=True)
    longitude = models.FloatField(db_index=True)

    class Meta:
        ordering = ['state', 'city', 'retail_price']
        indexes = [
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['state', 'retail_price']),
        ]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state} (${self.retail_price}/gal)"


class RouteQueryLog(models.Model):
    """
    Audit log of routes calculated, useful for analytics, history, and testing.
    """
    start_location = models.CharField(max_length=255)
    finish_location = models.CharField(max_length=255)
    total_distance_miles = models.FloatField()
    total_duration_hours = models.FloatField()
    total_fuel_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total_fuel_gallons = models.FloatField()
    stops_count = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.start_location} -> {self.finish_location} (${self.total_fuel_cost})"
