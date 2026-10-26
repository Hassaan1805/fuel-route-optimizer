from django.urls import path
from .views import (
    RouteOptimizationAPIView,
    FuelStationsListAPIView,
    HealthCheckAPIView,
    interactive_map_view,
)

urlpatterns = [
    # Web UI
    path('', interactive_map_view, name='map_home'),
    path('map/', interactive_map_view, name='map_page'),

    # REST API endpoints
    path('api/route/', RouteOptimizationAPIView.as_view(), name='api_route_optimization'),
    path('api/stations/', FuelStationsListAPIView.as_view(), name='api_fuel_stations'),
    path('api/health/', HealthCheckAPIView.as_view(), name='api_health_check'),
]
