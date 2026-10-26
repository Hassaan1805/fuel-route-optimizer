import time
import urllib.parse
from django.shortcuts import render
from django.views.generic import TemplateView
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

from .models import FuelStation, RouteQueryLog
from .serializers import (
    RouteRequestSerializer,
    RouteResponseSerializer,
    FuelStationSerializer
)
from .services import geocode_location, get_driving_route, optimize_fuel_route

class RouteOptimizationAPIView(APIView):
    """
    Core Route & Fuel Optimization API Endpoint.
    Accepts start and finish locations within the USA, computes the road route via OSRM,
    and calculates optimal, cost-effective fuel stops based on retail fuel prices.
    Supports both GET and POST requests.
    """

    @extend_schema(
        summary="Optimize route and fuel stops (GET)",
        description=(
            "Takes start and finish locations in the USA as query parameters, computes the road route, "
            "and returns optimal fuel stops, fuel costs, and route geometry."
        ),
        parameters=[
            OpenApiParameter('start', OpenApiTypes.STR, description="Start location (e.g. 'New York, NY' or '40.7128,-74.0060')", required=True),
            OpenApiParameter('finish', OpenApiTypes.STR, description="Destination location (e.g. 'Los Angeles, CA' or '34.0522,-118.2437')", required=True),
            OpenApiParameter('max_range', OpenApiTypes.FLOAT, description="Vehicle maximum range in miles (default: 500.0)", required=False),
            OpenApiParameter('mpg', OpenApiTypes.FLOAT, description="Fuel efficiency in miles per gallon (default: 10.0)", required=False),
            OpenApiParameter('max_deviation_miles', OpenApiTypes.FLOAT, description="Search corridor distance off route in miles (default: 10.0)", required=False),
        ],
        responses={200: RouteResponseSerializer}
    )
    def get(self, request):
        return self._process_route_request(request, request.query_params)

    @extend_schema(
        summary="Optimize route and fuel stops (POST)",
        description=(
            "Takes start and finish locations in the USA in JSON payload, computes the road route, "
            "and returns optimal fuel stops, fuel costs, and route geometry."
        ),
        request=RouteRequestSerializer,
        responses={200: RouteResponseSerializer}
    )
    def post(self, request):
        return self._process_route_request(request, request.data)

    def _process_route_request(self, request, data):
        t0 = time.time()
        serializer = RouteRequestSerializer(data=data)
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        start_query = serializer.validated_data['start']
        finish_query = serializer.validated_data['finish']
        max_range = serializer.validated_data.get('max_range', 500.0)
        mpg = serializer.validated_data.get('mpg', 10.0)
        max_deviation = serializer.validated_data.get('max_deviation_miles', 10.0)

        # 1. Geocode Start & Finish Locations within the USA
        try:
            start_geo = geocode_location(start_query)
        except Exception as e:
            return Response({
                'success': False,
                'error': f"Failed to geocode start location '{start_query}': {str(e)}"
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            finish_geo = geocode_location(finish_query)
        except Exception as e:
            return Response({
                'success': False,
                'error': f"Failed to geocode finish location '{finish_query}': {str(e)}"
            }, status=status.HTTP_400_BAD_REQUEST)

        # 2. Compute Road Route via OSRM (1 single API call)
        try:
            route_data = get_driving_route(
                start_lat=start_geo['lat'],
                start_lon=start_geo['lon'],
                finish_lat=finish_geo['lat'],
                finish_lon=finish_geo['lon']
            )
        except Exception as e:
            return Response({
                'success': False,
                'error': f"Route calculation failed: {str(e)}"
            }, status=status.HTTP_502_BAD_GATEWAY)

        # 3. Calculate Optimal Fuel Stops along Route
        try:
            optimization_result = optimize_fuel_route(
                route_coordinates=route_data['coordinates'],
                total_road_miles=route_data['distance_miles'],
                max_range=max_range,
                mpg=mpg,
                max_deviation_miles=max_deviation
            )
        except Exception as e:
            return Response({
                'success': False,
                'error': f"Fuel optimization failed: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        t_end = time.time()
        total_time_ms = round((t_end - t0) * 1000, 2)

        # Build Interactive Map URL with pre-filled query params
        map_params = urllib.parse.urlencode({
            'start': start_query,
            'finish': finish_query,
            'range': max_range,
            'mpg': mpg
        })
        interactive_map_url = f"{request.build_absolute_uri('/')}?{map_params}"

        # Construct final response
        response_data = {
            'success': True,
            'start': {
                'query': start_query,
                'display_name': start_geo['display_name'],
                'latitude': start_geo['lat'],
                'longitude': start_geo['lon']
            },
            'finish': {
                'query': finish_query,
                'display_name': finish_geo['display_name'],
                'latitude': finish_geo['lat'],
                'longitude': finish_geo['lon']
            },
            'total_distance_miles': optimization_result['total_distance_miles'],
            'total_duration_hours': route_data['duration_hours'],
            'total_fuel_gallons': optimization_result['total_fuel_gallons'],
            'total_fuel_cost': optimization_result['total_fuel_cost'],
            'average_price_per_gallon': optimization_result['average_price_per_gallon'],
            'vehicle_specs': {
                'max_vehicle_range_miles': max_range,
                'fuel_efficiency_mpg': mpg,
                'tank_capacity_gallons': round(max_range / mpg, 1)
            },
            'fuel_stops_count': optimization_result['fuel_stops_count'],
            'fuel_stops': optimization_result['fuel_stops'],
            'route_geometry': route_data['geojson'],
            'interactive_map_url': interactive_map_url,
            'api_execution_time_ms': total_time_ms
        }

        # Save to query audit log asynchronously/safely
        try:
            RouteQueryLog.objects.create(
                start_location=start_query,
                finish_location=finish_query,
                total_distance_miles=optimization_result['total_distance_miles'],
                total_duration_hours=route_data['duration_hours'],
                total_fuel_cost=optimization_result['total_fuel_cost'],
                total_fuel_gallons=optimization_result['total_fuel_gallons'],
                stops_count=optimization_result['fuel_stops_count']
            )
        except Exception:
            pass

        return Response(response_data, status=status.HTTP_200_OK)


class FuelStationsListAPIView(APIView):
    """List and search fuel stations loaded in the database."""

    @extend_schema(
        summary="List fuel stations",
        parameters=[
            OpenApiParameter('state', OpenApiTypes.STR, description="Filter by US State code (e.g. TX, CA)", required=False),
            OpenApiParameter('city', OpenApiTypes.STR, description="Filter by city name", required=False),
            OpenApiParameter('limit', OpenApiTypes.INT, description="Maximum number of stations to return (default: 50)", required=False),
        ],
        responses={200: FuelStationSerializer(many=True)}
    )
    def get(self, request):
        qs = FuelStation.objects.all()
        state = request.query_params.get('state')
        if state:
            qs = qs.filter(state__iexact=state.strip())
        city = request.query_params.get('city')
        if city:
            qs = qs.filter(city__icontains=city.strip())

        limit = min(int(request.query_params.get('limit', 50)), 500)
        stations = qs[:limit]
        serializer = FuelStationSerializer(stations, many=True)
        return Response({
            'count': qs.count(),
            'returned': len(serializer.data),
            'results': serializer.data
        })


class HealthCheckAPIView(APIView):
    """Health check endpoint to verify system and database status."""

    @extend_schema(
        summary="System health check",
        description="Verifies API and database connectivity, loaded fuel stations, and routing engines.",
        responses={200: OpenApiTypes.OBJECT}
    )
    def get(self, request):
        station_count = FuelStation.objects.count()
        return Response({
            'status': 'healthy',
            'database': 'connected',
            'fuel_stations_loaded': station_count,
            'routing_engine': 'OSRM Public Demo Server',
            'geocoder': 'US Cities Local DB + OpenStreetMap Nominatim'
        }, status=status.HTTP_200_OK)


def interactive_map_view(request):
    """Renders the interactive Leaflet.js map and control dashboard."""
    start = request.GET.get('start', 'New York, NY')
    finish = request.GET.get('finish', 'Los Angeles, CA')
    max_range = request.GET.get('range', '500')
    mpg = request.GET.get('mpg', '10')

    context = {
        'initial_start': start,
        'initial_finish': finish,
        'initial_range': max_range,
        'initial_mpg': mpg,
    }
    return render(request, 'routing/map.html', context)
