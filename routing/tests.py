import json
from django.test import TestCase, Client
from django.urls import reverse
from routing.models import FuelStation
from routing.services.geocoding import geocode_location, parse_coordinates, is_within_usa
from routing.services.fuel_optimizer import optimize_fuel_route

class GeocodingTestCase(TestCase):
    def test_parse_coordinates(self):
        coords = parse_coordinates("40.7128, -74.0060")
        self.assertIsNotNone(coords)
        self.assertAlmostEqual(coords[0], 40.7128)
        self.assertAlmostEqual(coords[1], -74.0060)

    def test_within_usa(self):
        self.assertTrue(is_within_usa(40.7128, -74.0060)) # NY
        self.assertTrue(is_within_usa(34.0522, -118.2437)) # LA
        self.assertFalse(is_within_usa(51.5074, -0.1278)) # London, UK
        self.assertFalse(is_within_usa(48.8566, 2.3522)) # Paris, France

    def test_local_city_geocoding(self):
        result = geocode_location("Austin, TX")
        self.assertIn('lat', result)
        self.assertIn('lon', result)
        self.assertTrue(is_within_usa(result['lat'], result['lon']))

    def test_invalid_location_raises(self):
        with self.assertRaises(ValueError):
            geocode_location("")


class FuelOptimizerTestCase(TestCase):
    def test_short_route_under_500_miles(self):
        # 200 miles straight line (or mock route)
        mock_coords = [[-97.7431, 30.2672], [-96.7970, 32.7767]]
        res = optimize_fuel_route(
            route_coordinates=mock_coords,
            total_road_miles=195.0,
            max_range=500.0,
            mpg=10.0
        )
        self.assertEqual(res['fuel_stops_count'], 0)
        self.assertEqual(res['total_fuel_gallons'], 19.5)
        self.assertGreater(res['total_fuel_cost'], 0)
        self.assertIn("within the vehicle's 500", res.get('note', ''))

    def test_long_route_refueling_constraint(self):
        # 1200 miles route
        mock_coords = [
            [-87.6298, 41.8781], # Chicago
            [-86.1581, 39.7684], # Indianapolis
            [-84.5120, 39.1031], # Cincinnati
            [-80.8431, 35.2271], # Charlotte
            [-81.6557, 30.3322], # Jacksonville
            [-80.1918, 25.7617], # Miami
        ]
        res = optimize_fuel_route(
            route_coordinates=mock_coords,
            total_road_miles=1380.0,
            max_range=500.0,
            mpg=10.0
        )
        self.assertGreater(res['fuel_stops_count'], 0)
        self.assertAlmostEqual(res['total_fuel_gallons'], 138.0, places=1)
        self.assertGreater(res['total_fuel_cost'], 0)

        # Check that distance between any consecutive stops is <= 500 miles
        prev_mile = 0.0
        for stop in res['fuel_stops']:
            leg = stop['route_mile'] - prev_mile
            self.assertLessEqual(leg, 500.0)
            prev_mile = stop['route_mile']

        final_leg = 1380.0 - prev_mile
        self.assertLessEqual(final_leg, 500.0)


class APITestCase(TestCase):
    def setUp(self):
        self.client = Client()
        # Ensure at least 1 mock station in DB if needed
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

    def test_health_check(self):
        response = self.client.get(reverse('api_health_check'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'healthy')
        self.assertGreaterEqual(data['fuel_stations_loaded'], 1)

    def test_stations_list_api(self):
        response = self.client.get(reverse('api_fuel_stations') + '?limit=5')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('results', data)

    def test_route_api_get_success(self):
        url = reverse('api_route_optimization') + '?start=Austin,TX&finish=Dallas,TX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('total_distance_miles', data)
        self.assertIn('total_fuel_cost', data)
        self.assertIn('fuel_stops', data)
        self.assertIn('route_geometry', data)

    def test_route_api_post_success(self):
        url = reverse('api_route_optimization')
        payload = {
            'start': 'Austin, TX',
            'finish': 'Dallas, TX',
            'max_range': 500,
            'mpg': 10
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('total_distance_miles', data)

    def test_route_api_missing_param_error(self):
        url = reverse('api_route_optimization') + '?start=Austin,TX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

    def test_map_ui_view(self):
        response = self.client.get(reverse('map_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FuelPath')
        self.assertContains(response, 'leaflet')
