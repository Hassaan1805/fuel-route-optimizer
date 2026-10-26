# Loom walkthrough (≤ 5 minutes)

**Before recording**
1. `python manage.py runserver`
2. In Postman, import `Fuel_Route_API.postman_collection.json` (`baseUrl` = `http://127.0.0.1:8000`).
3. Send each request once so the routes are cached and the on-camera responses come back in ~50–80 ms.
4. Open the code editor at `routing/services/`.

| Time | Section | On screen |
| :-- | :-- | :-- |
| 0:00–0:30 | The problem | Map page or README |
| 0:30–2:15 | Postman demo | Postman |
| 2:15–4:00 | Code tour | Editor |
| 4:00–4:45 | Map UI | Browser at `/` |
| 4:45–5:00 | Wrap-up | Swagger at `/api/docs/` |

---

### 0:00 – 0:30 · The problem
> "The API takes a start and finish anywhere in the US, routes the trip, and picks the cheapest places to buy fuel from the OPIS price list. It assumes a 500-mile range and 10 MPG, and returns the total fuel cost. It's Django 6.1 with DRF, and it makes one call to the free OSRM routing API per trip."

### 0:30 – 2:15 · Postman

**1. NY → LA (GET).** Send, then point out:
- `total_distance_miles` 2,800.6, `total_fuel_gallons` 280.1 (= miles / 10), `total_fuel_cost` **$859.17**
- 7 `fuel_stops`. Every `leg_distance_miles` and `final_leg_miles` is under 500, with the longest leg 482 miles (Utica, IL → Waco, NE).
- Stop 4 at Waco, NE is the cheapest diesel on the route ($2.799), so it **fills the 50-gal tank**. Later stops in CO, UT and NV are pricier, so the plan only tops up there: look at `fuel_on_arrival_gallons` vs `gallons_pumped`.
- `meta.routing_api_calls: 0` because the route is cached; it's 1 on the first request. `api_execution_time_ms` is about 70.
- Open the **Tests** tab: all checks pass (legs ≤ range, stop costs add up to the total).

**2. Chicago → Miami (POST JSON).**
> "The same endpoint takes a JSON body. 1,345 miles, 4 stops, $391.70. The plan fills up completely in Chattanooga at $2.786 because that's the cheapest station within reach."

**3. Seattle → San Diego (expects 422).**
> "The price list has almost no stations in California and Oregon, so there's a ~1,000-mile stretch with no fuel. Instead of returning an impossible plan, the API answers 422 and says exactly which miles have the gap."

**4. Austin → Dallas.**
> "A short trip: 200 miles, so one fill-up near the start, 20.1 gallons for $58.94."

### 2:15 – 4:00 · Code tour
1. **`views.py`**: the request flow is geocode → one OSRM call → optimize. Errors map to 400 / 422 / 502.
2. **`services/geocoding.py`**: "City, ST" resolves from an in-memory table of ~30k US cities, with no network call. Nominatim is only a fallback for street addresses.
3. **`services/osrm.py`**: the single routing call, cached on disk. The returned line is simplified with Douglas–Peucker, which cuts NY → LA from 790 KB to 56 KB.
4. **`services/fuel_optimizer.py`**:
   - All stations sit in a 3D KD-tree. One vectorized query finds the stations within 10 miles of the route and their mile markers.
   - `_plan_purchases` is a dynamic program over (station, fuel in tank). Passing a station equals buying zero there, so it only steps station to station. Buying is a prefix-minimum, so a cross-country plan takes a few milliseconds.
   - A $10-per-stop penalty, plus the detour fuel, keeps it from stopping to save cents. That's 7 stops instead of 20 for about 1% more cost, and the penalty is not counted in the reported cost.
5. **`tests.py`**: 22 tests, including hand-checked DP cases. OSRM is mocked so they run offline.

### 4:00 – 4:45 · Map UI
> "The same API drives this map. Here is the route, numbered fuel stops and a trip timeline showing how far each fill-up lasts. Clicking a stop zooms the map to it. Errors such as the Seattle → San Diego gap show inline, and the page works on a phone."

### 4:45 – 5:00 · Wrap-up
> "The endpoint is documented in Swagger at `/api/docs`. To recap: US start and finish in; out come the route, the cheapest fuel stops within a 500-mile range, and the total cost at 10 MPG, with one routing call and tens of milliseconds of compute. Thanks!"
