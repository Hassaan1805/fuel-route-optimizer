/**
 * FuelPath AI - Frontend Application Logic
 * Integrates with Django REST API, renders Leaflet.js interactive maps,
 * manages refueling itinerary, and supports 1-click presets and Postman exports.
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Initialize Leaflet Map
    const map = L.map('map', {
        zoomControl: false,
        attributionControl: true
    }).setView([39.8283, -98.5795], 4); // Center of Continental USA

    // Add zoom control at bottom right
    L.control.zoom({ position: 'bottomright' }).addTo(map);

    // Add clean, high-performance map tiles (CartoDB Voyager)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
    }).addTo(map);

    // Layer groups for dynamic map elements
    let routeLayer = null;
    let markersLayer = L.layerGroup().addTo(map);

    // DOM Elements
    const routeForm = document.getElementById('routeForm');
    const startInput = document.getElementById('startInput');
    const finishInput = document.getElementById('finishInput');
    const rangeInput = document.getElementById('rangeInput');
    const mpgInput = document.getElementById('mpgInput');
    const submitBtn = document.getElementById('submitBtn');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnSpinner = submitBtn.querySelector('.btn-spinner');

    const resultsSection = document.getElementById('resultsSection');
    const kpiCost = document.getElementById('kpiCost');
    const kpiAvgPrice = document.getElementById('kpiAvgPrice');
    const kpiDistance = document.getElementById('kpiDistance');
    const kpiDuration = document.getElementById('kpiDuration');
    const kpiStops = document.getElementById('kpiStops');
    const kpiGallons = document.getElementById('kpiGallons');
    const kpiTime = document.getElementById('kpiTime');
    const itineraryList = document.getElementById('itineraryList');
    const itineraryCountBadge = document.getElementById('itineraryCountBadge');
    const curlCode = document.getElementById('curlCode');
    const copyCurlBtn = document.getElementById('copyCurlBtn');
    const fitRouteBtn = document.getElementById('fitRouteBtn');
    const downloadPostmanBtn = document.getElementById('downloadPostmanBtn');

    // Store current route stops for click-to-zoom
    let currentFuelStops = [];
    let stopMarkers = {};

    // Custom Icon Creators
    function createCustomPin(type, content = '') {
        const className = `custom-pin pin-${type}`;
        return L.divIcon({
            className: 'leaflet-div-icon',
            html: `<div class="${className}" style="width: 30px; height: 30px; font-size: 13px;">${content}</div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15],
            popupAnchor: [0, -15]
        });
    }

    // 2. Fetch and render route
    async function calculateRoute(start, finish, maxRange, mpg) {
        setLoading(true);

        const params = new URLSearchParams({
            start: start,
            finish: finish,
            max_range: maxRange,
            mpg: mpg
        });

        // Update URL query parameters without reloading
        const newUrl = `${window.location.pathname}?${params.toString()}`;
        window.history.replaceState({}, '', newUrl);

        // Update cURL snippet
        const curlUrl = `${window.location.origin}/api/route/?${params.toString()}`;
        curlCode.innerText = `curl "${curlUrl}"`;

        try {
            const response = await fetch(`/api/route/?${params.toString()}`);
            const data = await response.json();

            if (!response.ok || !data.success) {
                const errMsg = data.error || (data.errors ? JSON.stringify(data.errors) : 'Route calculation failed.');
                alert(`Error: ${errMsg}`);
                setLoading(false);
                return;
            }

            renderRouteOnMap(data);
            populateDashboard(data);
            resultsSection.classList.remove('hidden');
        } catch (err) {
            console.error('API Error:', err);
            alert(`Network or Server error: ${err.message}`);
        } finally {
            setLoading(false);
        }
    }

    function setLoading(isLoading) {
        if (isLoading) {
            btnText.classList.add('hidden');
            btnSpinner.classList.remove('hidden');
            submitBtn.disabled = true;
        } else {
            btnText.classList.remove('hidden');
            btnSpinner.classList.add('hidden');
            submitBtn.disabled = false;
        }
    }

    // 3. Render Leaflet Map Elements
    function renderRouteOnMap(data) {
        // Clear previous layers
        if (routeLayer) {
            map.removeLayer(routeLayer);
        }
        markersLayer.clearLayers();
        stopMarkers = {};

        // Draw Route Polyline
        routeLayer = L.geoJSON(data.route_geometry, {
            style: {
                color: '#6366f1',
                weight: 5,
                opacity: 0.85,
                lineCap: 'round',
                lineJoin: 'round'
            }
        }).addTo(map);

        // Add Origin Marker (Green Pin)
        const startCoord = [data.start.latitude, data.start.longitude];
        const startMarker = L.marker(startCoord, {
            icon: createCustomPin('start', '<i class="fa-solid fa-play"></i>')
        }).bindPopup(`
            <div class="popup-station-title"><i class="fa-solid fa-location-dot" style="color: #10b981;"></i> Origin</div>
            <div class="popup-station-addr">${data.start.display_name}</div>
        `).addTo(markersLayer);

        // Add Destination Marker (Red Pin)
        const finishCoord = [data.finish.latitude, data.finish.longitude];
        const finishMarker = L.marker(finishCoord, {
            icon: createCustomPin('finish', '<i class="fa-solid fa-flag-checkered"></i>')
        }).bindPopup(`
            <div class="popup-station-title"><i class="fa-solid fa-flag-checkered" style="color: #f43f5e;"></i> Destination</div>
            <div class="popup-station-addr">${data.finish.display_name}</div>
        `).addTo(markersLayer);

        // Add Fuel Stop Markers (Amber Pins)
        currentFuelStops = data.fuel_stops || [];
        currentFuelStops.forEach((stop) => {
            const stopCoord = [stop.latitude, stop.longitude];
            const stopMarker = L.marker(stopCoord, {
                icon: createCustomPin('fuel', stop.stop_number)
            }).bindPopup(`
                <div class="popup-station-title">
                    <span style="color: #f59e0b;">Stop #${stop.stop_number}:</span> ${stop.name}
                </div>
                <div class="popup-station-addr">${stop.address}, ${stop.city}, ${stop.state}</div>
                <div class="popup-station-price">$${stop.retail_price.toFixed(3)} <span style="font-size: 11px; font-weight: normal; color: #94a3b8;">/ gal</span></div>
                <div class="popup-station-metrics">
                    <div><strong>Pumped:</strong> ${stop.gallons_pumped.toFixed(1)} gal</div>
                    <div><strong>Cost:</strong> $${stop.cost_at_stop.toFixed(2)}</div>
                    <div><strong>Mile Marker:</strong> ${stop.route_mile.toFixed(1)} mi</div>
                    <div><strong>Leg Distance:</strong> ${stop.leg_distance_miles.toFixed(1)} mi</div>
                </div>
            `).addTo(markersLayer);

            stopMarkers[stop.stop_number] = stopMarker;
        });

        // Fit Map to bounds
        fitRouteBounds();
    }

    function fitRouteBounds() {
        if (routeLayer) {
            map.fitBounds(routeLayer.getBounds(), { padding: [60, 60] });
        }
    }

    // 4. Populate UI Cards and Itinerary
    function populateDashboard(data) {
        kpiCost.innerText = `$${data.total_fuel_cost.toFixed(2)}`;
        kpiAvgPrice.innerText = `Avg: $${data.average_price_per_gallon.toFixed(3)} / gal`;
        kpiDistance.innerText = `${data.total_distance_miles.toFixed(1)} mi`;
        kpiDuration.innerText = `${data.total_duration_hours.toFixed(1)} hrs driving`;
        kpiStops.innerText = data.fuel_stops_count;
        kpiGallons.innerText = `${data.total_fuel_gallons.toFixed(1)} gal`;
        kpiTime.innerText = data.api_execution_time_ms;

        itineraryCountBadge.innerText = `${data.fuel_stops_count} stop${data.fuel_stops_count === 1 ? '' : 's'}`;

        // Populate Itinerary List
        itineraryList.innerHTML = '';
        if (data.fuel_stops_count === 0) {
            itineraryList.innerHTML = `
                <div class="stop-card" style="border-left-color: #10b981;">
                    <div class="stop-card-header">
                        <div class="stop-title">
                            <span class="stop-badge" style="background: #10b981; color: #fff;"><i class="fa-solid fa-check"></i></span>
                            <span class="stop-name">Direct Trip - No Fuel Stops Required</span>
                        </div>
                    </div>
                    <div class="stop-meta">
                        Total distance of ${data.total_distance_miles.toFixed(1)} miles is within your vehicle's ${data.vehicle_specs.max_vehicle_range_miles} mile range.
                    </div>
                </div>
            `;
            return;
        }

        data.fuel_stops.forEach((stop) => {
            const card = document.createElement('div');
            card.className = 'stop-card';
            card.innerHTML = `
                <div class="stop-card-header">
                    <div class="stop-title">
                        <span class="stop-badge">${stop.stop_number}</span>
                        <span class="stop-name" title="${stop.name}">${stop.name}</span>
                    </div>
                    <div class="stop-price-tag">$${stop.retail_price.toFixed(3)}/gal</div>
                </div>
                <div class="stop-meta">
                    <i class="fa-solid fa-location-dot"></i> ${stop.address ? stop.address + ', ' : ''}${stop.city}, ${stop.state}
                </div>
                <div class="stop-stats">
                    <span>Leg: <strong>${stop.leg_distance_miles.toFixed(1)} mi</strong></span>
                    <span>Fuel: <strong>${stop.gallons_pumped.toFixed(1)} gal</strong></span>
                    <span>Cost: <strong style="color: #86efac;">$${stop.cost_at_stop.toFixed(2)}</strong></span>
                    <span>At Mile: <strong>${stop.route_mile.toFixed(0)}</strong></span>
                </div>
            `;

            // Click card to zoom map into marker
            card.addEventListener('click', () => {
                const marker = stopMarkers[stop.stop_number];
                if (marker) {
                    map.setView(marker.getLatLng(), 11, { animate: true });
                    marker.openPopup();
                }
            });

            itineraryList.appendChild(card);
        });
    }

    // 5. Event Listeners
    routeForm.addEventListener('submit', (e) => {
        e.preventDefault();
        calculateRoute(
            startInput.value.trim(),
            finishInput.value.trim(),
            parseFloat(rangeInput.value) || 500.0,
            parseFloat(mpgInput.value) || 10.0
        );
    });

    fitRouteBtn.addEventListener('click', fitRouteBounds);

    // Preset buttons
    document.querySelectorAll('.preset-pill').forEach(btn => {
        btn.addEventListener('click', () => {
            startInput.value = btn.dataset.start;
            finishInput.value = btn.dataset.finish;
            calculateRoute(
                btn.dataset.start,
                btn.dataset.finish,
                parseFloat(rangeInput.value) || 500.0,
                parseFloat(mpgInput.value) || 10.0
            );
        });
    });

    // Copy cURL button
    copyCurlBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(curlCode.innerText).then(() => {
            copyCurlBtn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
            setTimeout(() => {
                copyCurlBtn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy';
            }, 2000);
        });
    });

    // Download Postman Collection
    downloadPostmanBtn.addEventListener('click', () => {
        window.open('/static/Fuel_Route_API.postman_collection.json', '_blank');
    });

    // Auto-calculate on initial load if inputs present
    if (startInput.value.trim() && finishInput.value.trim()) {
        calculateRoute(
            startInput.value.trim(),
            finishInput.value.trim(),
            parseFloat(rangeInput.value) || 500.0,
            parseFloat(mpgInput.value) || 10.0
        );
    }
});
