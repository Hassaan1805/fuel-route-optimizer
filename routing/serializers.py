from rest_framework import serializers
from .models import FuelStation

class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Starting location in the USA (e.g., 'New York, NY' or '40.7128, -74.0060')"
    )
    finish = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Destination location in the USA (e.g., 'Los Angeles, CA' or '34.0522, -118.2437')"
    )
    max_range = serializers.FloatField(
        default=500.0,
        required=False,
        min_value=50.0,
        max_value=2000.0,
        help_text="Vehicle range on a full tank in miles (default: 500.0 miles)"
    )
    mpg = serializers.FloatField(
        default=10.0,
        required=False,
        min_value=1.0,
        max_value=100.0,
        help_text="Fuel efficiency in miles per gallon (default: 10.0 mpg)"
    )
    max_deviation_miles = serializers.FloatField(
        default=10.0,
        required=False,
        min_value=1.0,
        max_value=50.0,
        help_text="Maximum search corridor deviation off highway in miles (default: 10.0 miles)"
    )


class FuelStopSerializer(serializers.Serializer):
    stop_number = serializers.IntegerField(help_text="Sequential stop number along route")
    opis_id = serializers.IntegerField(help_text="OPIS Truckstop ID")
    name = serializers.CharField(help_text="Station brand/name")
    address = serializers.CharField(help_text="Physical address or highway exit")
    city = serializers.CharField(help_text="City")
    state = serializers.CharField(help_text="State abbreviation")
    retail_price = serializers.FloatField(help_text="Retail fuel price ($/gallon)")
    route_mile = serializers.FloatField(help_text="Distance from route start in miles")
    leg_distance_miles = serializers.FloatField(help_text="Distance driven since previous stop/origin")
    gallons_pumped = serializers.FloatField(help_text="Gallons refueled at this stop")
    cost_at_stop = serializers.FloatField(help_text="Total money spent at this stop ($)")
    miles_off_route = serializers.FloatField(help_text="Perpendicular distance from highway path in miles")
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class LocationPointSerializer(serializers.Serializer):
    query = serializers.CharField()
    display_name = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    start = LocationPointSerializer()
    finish = LocationPointSerializer()
    total_distance_miles = serializers.FloatField(help_text="Total driving distance in miles")
    total_duration_hours = serializers.FloatField(help_text="Total estimated driving time in hours")
    total_fuel_gallons = serializers.FloatField(help_text="Total gallons of fuel consumed")
    total_fuel_cost = serializers.FloatField(help_text="Total money spent on fuel ($)")
    average_price_per_gallon = serializers.FloatField(help_text="Average price per gallon paid ($/gal)")
    vehicle_specs = serializers.DictField(help_text="Vehicle range and fuel economy parameters")
    fuel_stops_count = serializers.IntegerField(help_text="Number of fuel stops on route")
    fuel_stops = FuelStopSerializer(many=True, help_text="Optimal fuel stop locations and details")
    route_geometry = serializers.DictField(help_text="GeoJSON LineString coordinates for rendering the map")
    interactive_map_url = serializers.CharField(help_text="URL to view the visual interactive Leaflet map")
    api_execution_time_ms = serializers.FloatField(help_text="Total API processing time in milliseconds")


class FuelStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = FuelStation
        fields = '__all__'
