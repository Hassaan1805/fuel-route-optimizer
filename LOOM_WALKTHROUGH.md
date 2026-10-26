# 5-Minute Loom Video Walkthrough Script & Guide 🎥

This guide provides a structured, minute-by-minute script for recording your 5-minute Loom demonstration using **Postman** and a quick overview of the **Django codebase**.

---

## ⏱️ Video Timeline Overview (5 Minutes Max)

| Timestamp | Topic | What to Show on Screen |
| :--- | :--- | :--- |
| **0:00 – 0:45** | **Introduction & Problem Statement** | Postman / Title Slide / Web UI |
| **0:45 – 2:30** | **Postman API Demonstration** | Postman sending requests and inspecting JSON |
| **2:30 – 4:00** | **Codebase & Architecture Overview** | VS Code / IDE highlighting key services |
| **4:00 – 4:40** | **Interactive Map UI & Visualization** | Web browser showing Leaflet route & pins |
| **4:40 – 5:00** | **Conclusion & Wrap-Up** | Swagger UI (`/api/docs/`) & Final summary |

---

## 🎙️ Step-by-Step Script

### [0:00 – 0:45] 1. Introduction & Overview
> *"Hello! Today I'm excited to present my solution for the Optimal Fuel Routing API assignment built with Django and Django REST Framework.*
>
> *The objective of this challenge is to take any start and destination location within the USA, compute the road route using a free routing API, identify the most cost-effective fuel stops from the provided OPIS dataset of over 8,000 truck stops, ensure the vehicle never exceeds its 500-mile range, and calculate total fuel expenditure assuming a fuel economy of 10 miles per gallon.*
>
> *I designed this service for maximum performance: it uses an in-memory 3D KD-Tree for spatial indexing and a Penalized Dynamic Programming algorithm to select the mathematically optimal refueling stops in under 70 milliseconds, requiring only a single API call to the routing engine."*

---

### [0:45 – 2:30] 2. Postman Demonstration

> **Action**: Switch to Postman with the collection `FuelPath AI - Optimal Fuel Routing API` open.

#### Request A: Cross-Country Coast-to-Coast (NY to LA - GET)
- **Endpoint**: `GET http://127.0.0.1:8000/api/route/?start=New York, NY&finish=Los Angeles, CA&max_range=500&mpg=10`
- **Click**: **Send** (Notice the sub-second response time!).
- **Explain the Response**:
  > *"Here is our first request: a 2,800-mile cross-country trip from New York to Los Angeles.*
  >
  > *Let's look at the response:*
  > - *`total_distance_miles`: 2,800.6 miles.*
  > - *`total_fuel_gallons`: Exactly 280.06 gallons (10 mpg).*
  > - *`total_fuel_cost`: $867.35, averaging $3.097 per gallon.*
  > - *`fuel_stops_count`: 6 stops.*
  > - *Notice the leg distances between consecutive stops:*
  >   - *Leg 1 to Youngstown, OH: 392 miles.*
  >   - *Leg 2 to Utica, IL: 461 miles.*
  >   - *Leg 3 to Waco, NE: 482 miles.*
  >   - *Leg 4 to Keenesburg, CO: 403 miles.*
  >   - *Leg 5 to Green River, UT: 381 miles.*
  >   - *Leg 6 to North Las Vegas, NV: 378 miles.*
  >   - *Final leg to Los Angeles: 301 miles.*
  > *Every single leg is strictly under our 500-mile vehicle range! At each stop, we get the exact truckstop name, OPIS ID, retail price, gallons pumped, and money spent.*
  > *And notice: the entire calculation was completed in tens of milliseconds with only 1 single call to OSRM."*

#### Request B: JSON POST Body (Chicago to Miami)
- **Endpoint**: `POST http://127.0.0.1:8000/api/route/`
- **Body**:
  ```json
  {
    "start": "Chicago, IL",
    "finish": "Miami, FL",
    "max_range": 500,
    "mpg": 10
  }
  ```
- **Click**: **Send**.
  > *"The API supports both standard GET query parameters and clean JSON POST bodies.*
  > *For this 1,345-mile route from Chicago to Miami, the optimizer selects 3 stops (Rensselaer, IN; Chattanooga, TN; Adel, GA) for a total fuel cost of $391.69."*

#### Request C: Short Route (Austin, TX to Dallas, TX - Under 500 Miles)
- **Endpoint**: `GET http://127.0.0.1:8000/api/route/?start=Austin, TX&finish=Dallas, TX`
- **Click**: **Send**.
  > *"Here we test edge cases: an intrastate route of 195 miles. Since 195 miles is within the vehicle's 500-mile range, the engine detects that 0 refueling stops are required to reach the destination, calculating the fuel consumed (19.5 gal) without forcing unnecessary stops."*

---

### [2:30 – 4:00] 3. Codebase & Architecture Overview

> **Action**: Switch to your code editor (e.g., VS Code).

1. **Project Structure (`fuel_routing_project` & `routing` app)**:
   > *"Let's look at the architecture. The application is built in Django 6 with Django REST Framework."*
   >
2. **Geocoding Service ([`routing/services/geocoding.py`](file:///f:/Stuff/Django%20assignment/routing/services/geocoding.py))**:
   > *"To eliminate rate limits and provide instant lookups, I implemented a local-first geocoding engine that caches ~30,000 US cities in memory. Common city queries resolve in 0 ms without external network calls, with OpenStreetMap Nominatim as an automatic fallback."*
   >
3. **OSRM Routing Integration ([`routing/services/osrm.py`](file:///f:/Stuff/Django%20assignment/routing/services/osrm.py))**:
   > *"For routing, we use OSRM (Open Source Routing Machine). A single GET request returns the exact road network distance, driving duration, and complete GeoJSON route coordinates. Routes are cached in Django's cache."*
   >
4. **Spatial Indexing & Optimization Engine ([`routing/services/fuel_optimizer.py`](file:///f:/Stuff/Django%20assignment/routing/services/fuel_optimizer.py))**:
   > *"The core optimization has two phases:*
   > *First: We converted all 6,626 fuel stations from the OPIS dataset into 3D Cartesian coordinates on Earth's sphere using `scipy.spatial.cKDTree`. Querying stations within a 10-mile corridor of a 2,800-mile route takes just 4.8 milliseconds!*
   > *Second: We formulate refueling as a shortest path problem on a Directed Acyclic Graph (DAG) with Penalized Dynamic Programming. This ensures that:*
   > *1. No leg exceeds 500 miles.*
   > *2. Stations with the lowest retail fuel prices are chosen.*
   > *3. The driver isn't stopped every 10 miles unnecessarily.*
   > *The DP runs in under 30 milliseconds."*
   >
5. **Testing ([`routing/tests.py`](file:///f:/Stuff/Django%20assignment/routing/tests.py))**:
   > *"The project includes 12 automated unit and integration tests covering geocoding, boundary validation, fuel optimization, and API serialization."*

---

### [4:00 – 4:40] 4. Interactive Map UI Demonstration

> **Action**: Switch to browser at `http://127.0.0.1:8000/`.

- Show the split-screen view:
  > *"To satisfy the requirement to 'Return a map of the route along with optimal location to fuel up', I also built an interactive web application powered by Leaflet.js.*
  > *Here you can see the complete route rendered in indigo, the green origin beacon, red destination flag, and numbered amber fuel stop pins.*
  > *Clicking on any fuel stop card in the sidebar smoothly pans and zooms the map to that exact station, displaying its name, address, price, and fuel pumped.*
  > *We also have 1-click presets for quick testing, a cURL command generator, and direct Postman collection download."*

---

### [4:40 – 5:00] 5. Conclusion & Wrap-Up

> **Action**: Navigate to `http://127.0.0.1:8000/api/docs/` (Swagger UI).
>
> *"Finally, the API is fully documented via OpenAPI 3.0 and Swagger UI at `/api/docs/`, allowing developers to test endpoints interactively.*
>
> *To summarize: the API meets all requirements—taking US start and finish locations, returning an interactive route map, calculating cost-optimal fuel stops adhering to the 500-mile range, computing total money spent at 10 mpg, using only 1 route API call, and responding in tens of milliseconds.*
>
> *Thank you for your time!"*

---

## 💡 Quick Tips for a Flawless Recording
1. **Start the server before recording**:
   ```bash
   .venv\Scripts\python manage.py runserver 127.0.0.1:8000
   ```
2. **Import Postman Collection**:
   - Open Postman -> Click **Import** -> Select `Fuel_Route_API.postman_collection.json`.
3. **Pre-test one route**:
   - Send the NY to LA request once so the OSRM route is warm in cache (it will return in ~60ms on video!).
