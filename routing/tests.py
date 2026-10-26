import json
from unittest import mock
from django.test import TestCase, Client
from django.urls import reverse
from routing.models import FuelStation
from routing.services.geocoding import geocode_location, parse_coordinates, is_within_usa
from routing.services.fuel_optimizer import (
    optimize_fuel_route, RouteInfeasibleError, _plan_purchases
)
from routing.services.osrm import simplify_coordinates

# Chicago -> Indianapolis -> Cincinnati -> Charlotte -> Jacksonville -> Miami
CHICAGO_MIAMI_COORDS = [
    [-87.6298, 41.8781],
    [-86.1581, 39.7684],
    [-84.5120, 39.1031],
    [-80.8431, 35.2271],
    [-81.6557, 30.3322],
    [-80.1918, 25.7617],
]


def fake_route(start_lat, start_lon, finish_lat, finish_lon):
    """Stand-in for the OSRM call so API tests don't depend on the network."""
    coords = [[start_lon, start_lat], [finish_lon, finish_lat]]
    return {
        'distance_miles': 195.0,
        'duration_hours': 3.1,
        'coordinates': coords,
        'geojson': {'type': 'LineString', 'coordinates': coords},
        'from_cache': False,
    }


class GeocodingTestCase(TestCase):
    def test_parse_coordinates(self):
        coords = parse_coordinates("40.7128, -74.0060")
        self.assertIsNotNone(coords)
        self.assertAlmostEqual(coords[0], 40.7128)
        self.assertAlmostEqual(coords[1], -74.0060)

    def test_within_usa(self):
        self.assertTrue(is_within_usa(40.7128, -74.0060))  # NY
        self.assertTrue(is_within_usa(34.0522, -118.2437))  # LA
        self.assertFalse(is_within_usa(51.5074, -0.1278))  # London, UK
        self.assertFalse(is_within_usa(48.8566, 2.3522))  # Paris, France

    def test_local_city_geocoding(self):
        result = geocode_location("Austin, TX")
        self.assertAlmostEqual(result['lat'], 30.3, delta=0.3)
        self.assertAlmostEqual(result['lon'], -97.7, delta=0.3)

    def test_full_state_name_and_zip(self):
        by_name = geocode_location("Austin, Texas")
        by_zip = geocode_location("Austin, TX 78701")
        by_code = geocode_location("Austin, TX")
        self.assertEqual((by_name['lat'], by_name['lon']), (by_code['lat'], by_code['lon']))
        self.assertEqual((by_zip['lat'], by_zip['lon']), (by_code['lat'], by_code['lon']))

    @mock.patch('routing.services.geocoding._nominatim', side_effect=OSError("offline"))
    def test_ambiguous_city_falls_back_offline(self, _):
        result = geocode_location("Springfield")
        self.assertTrue(is_within_usa(result['lat'], result['lon']))

    def test_outside_usa_coordinates_rejected(self):
        with self.assertRaises(ValueError):
            geocode_location("51.5074, -0.1278")

    def test_invalid_location_raises(self):
        with self.assertRaises(ValueError):
            geocode_location("")


class PurchasePlanTestCase(TestCase):
    def test_fills_up_at_cheap_station_and_tops_up_at_expensive_one(self):
        # Stations at mile 0 ($4), 100 ($3), 450 ($5); destination at mile 700.
        # Optimal: buy 10 gal at $4 to reach the $3 station, fill the 50 gal tank
        # there, then buy only the 10 gal still needed at the $5 station.
        plan = _plan_purchases(
            miles=[0, 100, 450, 700], prices=[4.0, 3.0, 5.0, 0.0],
            stop_penalties=[0, 0, 0, 0], max_range=500, mpg=10,
        )
        bought = {node: round(gal, 1) for node, gal, _ in plan}
        self.assertEqual(bought, {0: 10.0, 1: 50.0, 2: 10.0})

    def test_stop_penalty_skips_tiny_savings(self):
        # A station 1 cent cheaper 10 miles ahead is not worth a $10 stop.
        plan = _plan_purchases(
            miles=[0, 10, 300], prices=[3.00, 2.99, 0.0],
            stop_penalties=[10, 10, 0], max_range=500, mpg=10,
        )
        self.assertEqual([node for node, _, _ in plan], [0])


class FuelOptimizerTestCase(TestCase):
    def test_short_route_buys_exactly_trip_fuel(self):
        res = optimize_fuel_route(
            route_coordinates=[[-97.7431, 30.2672], [-96.7970, 32.7767]],
            total_road_miles=195.0,
            max_range=500.0,
            mpg=10.0
        )
        self.assertGreaterEqual(res['fuel_stops_count'], 1)
        self.assertEqual(res['fuel_stops'][0]['stop_type'], 'origin')
        self.assertAlmostEqual(res['total_fuel_gallons'], 19.5, delta=0.1)
        self.assertAlmostEqual(
            sum(s['cost_at_stop'] for s in res['fuel_stops']), res['total_fuel_cost'], places=2
        )

    def test_long_route_respects_range_and_tank(self):
        res = optimize_fuel_route(
            route_coordinates=CHICAGO_MIAMI_COORDS,
            total_road_miles=1380.0,
            max_range=500.0,
            mpg=10.0
        )
        self.assertGreater(res['fuel_stops_count'], 2)
        self.assertAlmostEqual(res['total_fuel_gallons'], 138.0, delta=0.1)
        self.assertAlmostEqual(
            sum(s['cost_at_stop'] for s in res['fuel_stops']), res['total_fuel_cost'], places=2
        )

        prev_mile = 0.0
        for stop in res['fuel_stops']:
            self.assertLessEqual(stop['route_mile'] - prev_mile, 500.0)
            self.assertGreaterEqual(stop['fuel_on_arrival_gallons'], 0.0)
            self.assertLessEqual(stop['fuel_on_arrival_gallons'] + stop['gallons_pumped'], 50.0 + 1e-6)
            prev_mile = stop['route_mile']
        self.assertLessEqual(res['final_leg_miles'], 500.0)

    def test_route_without_stations_is_infeasible(self):
        # Straight line across the Gulf of Mexico: no stations anywhere near it
        with self.assertRaises(RouteInfeasibleError):
            optimize_fuel_route(
                route_coordinates=[[-97.4, 27.8], [-90.0, 26.0], [-82.5, 27.9]],
                total_road_miles=900.0,
            )


class GeometryTestCase(TestCase):
    def test_simplify_keeps_endpoints_and_drops_collinear_points(self):
        line = [[-100.0 + i * 0.01, 40.0] for i in range(101)]
        simplified = simplify_coordinates(line)
        self.assertEqual(simplified, [[-100.0, 40.0], [-99.0, 40.0]])


@mock.patch('routing.views.get_driving_route', side_effect=fake_route)
class APITestCase(TestCase):
    def setUp(self):
        self.client = Client()
        FuelStation.objects.get_or_create(
            opis_id=999999,
            defaults={
                'name': 'TEST STOP',
                'address': 'I-35 Exit 100',
                'city': 'Dallas',
                'state': 'TX',
                'retail_price': 3.199,
                'latitude': 32.7767,
                'longitude': -96.7970
            }
        )

    def test_health_check(self, _):
        response = self.client.get(reverse('api_health_check'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'healthy')
        self.assertGreaterEqual(data['fuel_stations_loaded'], 1)

    def test_stations_list_api(self, _):
        response = self.client.get(reverse('api_fuel_stations') + '?limit=5')
        self.assertEqual(response.status_code, 200)
        self.assertIn('results', response.json())

    def test_stations_list_bad_limit(self, _):
        response = self.client.get(reverse('api_fuel_stations') + '?limit=abc')
        self.assertEqual(response.status_code, 400)

    def test_route_api_get_success(self, route_mock):
        url = reverse('api_route_optimization') + '?start=Austin,TX&finish=Dallas,TX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(route_mock.call_count, 1)
        self.assertEqual(data['meta']['routing_api_calls'], 1)
        for key in ('total_distance_miles', 'total_fuel_cost', 'fuel_stops', 'route_geometry'):
            self.assertIn(key, data)

    def test_route_api_post_success(self, _):
        url = reverse('api_route_optimization')
        payload = {'start': 'Austin, TX', 'finish': 'Dallas, TX', 'max_range': 500, 'mpg': 10}
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_route_api_missing_param_error(self, _):
        response = self.client.get(reverse('api_route_optimization') + '?start=Austin,TX')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])

    def test_route_api_location_outside_usa(self, _):
        url = reverse('api_route_optimization') + '?start=51.5,-0.12&finish=Dallas,TX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertIn('outside', response.json()['error'])

    def test_route_api_infeasible_route_is_422(self, route_mock):
        def gulf_route(*args, **kwargs):
            coords = [[-97.4, 27.8], [-90.0, 26.0], [-82.5, 27.9]]
            return {**fake_route(27.8, -97.4, 27.9, -82.5), 'coordinates': coords, 'distance_miles': 900.0}
        route_mock.side_effect = gulf_route
        url = reverse('api_route_optimization') + '?start=27.8,-97.4&finish=27.9,-82.5'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.json()['success'])

    def test_map_ui_view(self, _):
        response = self.client.get(reverse('map_home') + '?start=Austin,TX&max_range=400')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FuelPath')
        self.assertContains(response, 'leaflet')
        self.assertContains(response, 'value="400"')
