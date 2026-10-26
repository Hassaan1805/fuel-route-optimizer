/**
 * FuelPath — frontend for the /api/route/ endpoint.
 * Draws the route and fuel stops on a Leaflet map and renders the refueling plan.
 */

document.addEventListener('DOMContentLoaded', () => {
    const $ = (id) => document.getElementById(id);

    // ---------- Map ----------
    const map = L.map('map', { zoomControl: false, zoomSnap: 0.25 }).setView([39.8283, -98.5795], 4);
    L.control.zoom({ position: 'bottomright' }).addTo(map);

    // OSM's tile usage policy rejects requests without a Referer, and Django's
    // default Referrer-Policy (same-origin) strips it from cross-site requests.
    const tileOptions = { referrerPolicy: 'strict-origin-when-cross-origin' };
    const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        ...tileOptions,
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors'
    });
    const esriTopo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
        ...tileOptions,
        maxZoom: 19,
        attribution: 'Tiles &copy; Esri'
    });
    const esriStreets = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
        ...tileOptions,
        maxZoom: 19,
        attribution: 'Tiles &copy; Esri'
    });
    osm.addTo(map);
    L.control.layers({ 'Road atlas': osm, 'Topographic': esriTopo, 'Streets': esriStreets }, null, { position: 'topright' }).addTo(map);

    // Leaflet measures its container once; re-measure when the layout changes
    // (panel stacking on small screens) so tiles and fitBounds stay correct.
    new ResizeObserver(() => map.invalidateSize()).observe($('map'));

    const routeLayer = L.layerGroup().addTo(map);
    const markerLayer = L.layerGroup().addTo(map);
    let routeBounds = null;
    let stopMarkers = {};

    // ---------- DOM ----------
    const form = $('routeForm');
    const startInput = $('startInput');
    const finishInput = $('finishInput');
    const rangeInput = $('rangeInput');
    const mpgInput = $('mpgInput');
    const submitBtn = $('submitBtn');
    let activeRequest = null;

    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
    const money = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const miles = (n) => `${Math.round(n).toLocaleString('en-US')} mi`;
    const hours = (h) => `${Math.floor(h)}h ${String(Math.round((h % 1) * 60)).padStart(2, '0')}m`;
    const ghost = (text) => text.replace(/\d/g, '8');
    // Gas-station style price: 2.79⁹
    const pylonPrice = (p) => {
        const s = p.toFixed(3);
        return `${s.slice(0, -1)}<span class="cents">${s.slice(-1)}</span>`;
    };

    function setLcd(el, text) {
        el.textContent = text;
        el.dataset.ghost = ghost(text);
    }

    function pinIcon(kind, label = '') {
        const sizes = { start: [24, 24], finish: [26, 26], fuel: [30, 26], 'origin-fuel': [30, 26] };
        const [w, h] = sizes[kind];
        const isFuel = kind.includes('fuel');
        return L.divIcon({
            className: 'pin-icon',
            html: `<div class="pin pin-${kind}${isFuel ? ' pin-fuel' : ''}">${label}</div>`,
            iconSize: [w, h],
            iconAnchor: isFuel ? [w / 2, h + 5] : [w / 2, h / 2],
            popupAnchor: [0, isFuel ? -h - 4 : -h / 2]
        });
    }

    // ---------- Request ----------
    function readParams() {
        return {
            start: startInput.value.trim(),
            finish: finishInput.value.trim(),
            max_range: parseFloat(rangeInput.value) || 500,
            mpg: parseFloat(mpgInput.value) || 10
        };
    }

    async function calculateRoute() {
        const params = readParams();
        if (!params.start || !params.finish) {
            showError('Enter both an origin and a destination.');
            return;
        }

        const query = new URLSearchParams(params).toString();
        window.history.replaceState({}, '', `${window.location.pathname}?${query}`);

        activeRequest?.abort();
        const controller = new AbortController();
        activeRequest = controller;
        setLoading(true);
        hideError();

        try {
            const response = await fetch(`/api/route/?${query}`, { signal: controller.signal });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                showError(data.error || formatFieldErrors(data.errors) || `Request failed (HTTP ${response.status}).`);
                return;
            }
            renderMap(data);
            renderResults(data, query);
        } catch (err) {
            if (err.name !== 'AbortError') showError(`Could not reach the server: ${err.message}`);
        } finally {
            if (activeRequest === controller) setLoading(false);
        }
    }

    function formatFieldErrors(errors) {
        if (!errors) return '';
        return Object.entries(errors).map(([field, msgs]) => `${field}: ${[].concat(msgs).join(' ')}`).join(' · ');
    }

    function setLoading(isLoading) {
        submitBtn.disabled = isLoading;
        submitBtn.querySelector('.btn-label').classList.toggle('hidden', isLoading);
        submitBtn.querySelector('.btn-busy').classList.toggle('hidden', !isLoading);
        $('mapLoading').classList.toggle('hidden', !isLoading);
    }

    function showError(message) {
        $('errorText').textContent = message;
        $('errorBox').classList.remove('hidden');
        $('results').classList.add('hidden');
        routeLayer.clearLayers();
        markerLayer.clearLayers();
        $('fitRouteBtn').classList.add('hidden');
    }

    function hideError() {
        $('errorBox').classList.add('hidden');
    }

    // ---------- Map rendering ----------
    function renderMap(data) {
        routeLayer.clearLayers();
        markerLayer.clearLayers();
        stopMarkers = {};

        // Drawn as a road: dark asphalt with a dashed amber center line
        L.geoJSON(data.route_geometry, { style: { color: '#17150f', weight: 9, opacity: 0.9, lineCap: 'round', lineJoin: 'round' } }).addTo(routeLayer);
        L.geoJSON(data.route_geometry, { style: { color: '#3a3832', weight: 6, opacity: 1, lineCap: 'round', lineJoin: 'round' } }).addTo(routeLayer);
        const center = L.geoJSON(data.route_geometry, { style: { color: '#f2a900', weight: 2, opacity: 1, dashArray: '6 8' } }).addTo(routeLayer);
        routeBounds = center.getBounds();

        L.marker([data.start.latitude, data.start.longitude], { icon: pinIcon('start', '▶'), zIndexOffset: 500 })
            .bindPopup(`<div class="popup-title">Origin</div><div class="popup-sub">${escapeHtml(data.start.display_name)}</div>`)
            .addTo(markerLayer);
        L.marker([data.finish.latitude, data.finish.longitude], { icon: pinIcon('finish'), zIndexOffset: 500 })
            .bindPopup(`<div class="popup-title">Destination</div><div class="popup-sub">${escapeHtml(data.finish.display_name)}</div>`)
            .addTo(markerLayer);

        data.fuel_stops.forEach((stop) => {
            const kind = stop.stop_type === 'origin' ? 'origin-fuel' : 'fuel';
            stopMarkers[stop.stop_number] = L.marker([stop.latitude, stop.longitude], { icon: pinIcon(kind, stop.stop_number), zIndexOffset: 1000 })
                .bindPopup(stopPopup(stop))
                .addTo(markerLayer);
        });

        $('fitRouteBtn').classList.remove('hidden');
        fitRoute();
    }

    function stopPopup(stop) {
        return `
            <div class="popup-title">${stop.stop_number}. ${escapeHtml(stop.name)}</div>
            <div class="popup-sub">${escapeHtml([stop.address, stop.city, stop.state].filter(Boolean).join(', '))}</div>
            <div class="popup-price">$${stop.retail_price.toFixed(3)}</div>
            <div class="popup-grid">
                <span>Buy <b>${stop.gallons_pumped.toFixed(1)} gal</b></span>
                <span>Pay <b>$${money(stop.cost_at_stop)}</b></span>
                <span>Mile <b>${Math.round(stop.route_mile)}</b></span>
                <span>Off route <b>${stop.miles_off_route.toFixed(1)} mi</b></span>
            </div>`;
    }

    function fitRoute() {
        if (routeBounds) map.fitBounds(routeBounds, { padding: [56, 56] });
    }

    // ---------- Panel rendering ----------
    function renderResults(data, query) {
        const specs = data.vehicle_specs;
        const stops = data.fuel_stops;

        setLcd($('kpiCost'), money(data.total_fuel_cost));
        setLcd($('kpiGallons'), data.total_fuel_gallons.toFixed(1));
        setLcd($('kpiAvgPrice'), data.average_price_per_gallon.toFixed(3));
        $('kpiDistance').textContent = miles(data.total_distance_miles);
        $('kpiDuration').textContent = hours(data.total_duration_hours);
        $('kpiStops').textContent = stops.length;
        $('kpiRange').textContent =
            `${specs.max_vehicle_range_miles} mi range · ${specs.tank_capacity_gallons} gal tank · ${specs.fuel_efficiency_mpg} mpg`;

        const calls = data.meta.routing_api_calls;
        $('metaTime').textContent = Math.round(data.api_execution_time_ms);
        $('metaCalls').textContent = calls === 0 ? '0 routing calls (cached)' : `${calls} routing call`;
        $('metaStations').textContent = data.meta.stations_considered;

        $('stopsBadge').textContent = `${stops.length} stop${stops.length === 1 ? '' : 's'}`;
        renderRoad(data);
        renderItinerary(data);

        $('curlCode').textContent = `curl "${window.location.origin}/api/route/?${query}"`;
        $('results').classList.remove('hidden');
    }

    function renderRoad(data) {
        const total = data.total_distance_miles;
        const pct = (mile) => Math.min(100, Math.max(0, (mile / total) * 100));
        const road = $('timeline');
        road.innerHTML = '<div class="road-dash"></div>';

        // Shade how far each fill-up carries the truck
        const mpg = data.vehicle_specs.fuel_efficiency_mpg;
        data.fuel_stops.forEach((stop) => {
            const reach = Math.min(total, stop.route_mile + (stop.fuel_on_arrival_gallons + stop.gallons_pumped) * mpg);
            const band = document.createElement('div');
            band.className = 'road-fuel';
            band.style.left = `${pct(stop.route_mile)}%`;
            band.style.width = `${pct(reach) - pct(stop.route_mile)}%`;
            road.appendChild(band);
        });

        const addPin = (mile, cls, label, title, onClick) => {
            const pin = document.createElement(onClick ? 'button' : 'span');
            pin.className = `road-pin ${cls}`;
            pin.style.left = `${pct(mile)}%`;
            pin.textContent = label;
            pin.title = title;
            if (onClick) {
                pin.type = 'button';
                pin.addEventListener('click', onClick);
            }
            road.appendChild(pin);
        };
        addPin(total, 'pin-finish', '■', 'Destination');
        data.fuel_stops.forEach((stop) => addPin(
            stop.route_mile, stop.stop_type === 'origin' ? 'pin-start' : '', stop.stop_number,
            `${stop.stop_number}. ${stop.city}, ${stop.state} — mile ${Math.round(stop.route_mile)}, $${stop.retail_price.toFixed(3)}/gal`,
            () => focusStop(stop.stop_number)
        ));

        $('timelineEnd').textContent = `MILE ${Math.round(total).toLocaleString('en-US')}`;
        const longestLeg = Math.max(...data.fuel_stops.map((s) => s.leg_distance_miles), data.final_leg_miles);
        $('timelineCaption').textContent = `longest leg ${miles(longestLeg)}`;
    }

    function renderItinerary(data) {
        const items = [];
        let i = 0;
        const place = (geo) => escapeHtml(geo.display_name.replace(/, USA$|, United States$/, ''));

        items.push(`
            <li style="--i:${i++}">
                <div class="guide">
                    <div><span class="label">Origin · tank empty</span><span class="place">${place(data.start)}</span></div>
                    <span class="dist">MILE 0</span>
                </div>
            </li>`);

        data.fuel_stops.forEach((stop) => {
            const isOrigin = stop.stop_type === 'origin';
            const tab = isOrigin ? 'Fill-up near origin' : `Exit · mile ${Math.round(stop.route_mile).toLocaleString('en-US')}`;
            const arrive = isOrigin
                ? `${stop.miles_off_route.toFixed(1)} mi from start`
                : `arrive <b>${stop.fuel_on_arrival_gallons.toFixed(1)}</b> gal`;
            if (!isOrigin) {
                items.push(`<li class="connector" style="--i:${i++}">${miles(stop.leg_distance_miles)}</li>`);
            }
            items.push(`
                <li class="stop is-clickable ${isOrigin ? 'stop-origin' : ''}" data-stop="${stop.stop_number}" style="--i:${i++}">
                    <div class="sign" tabindex="0" role="button" aria-label="Show stop ${stop.stop_number} on the map">
                        <span class="exit-tab">${stop.stop_number} · ${tab}</span>
                        <div class="sign-inner">
                            <div class="sign-title" title="${escapeHtml(stop.name)}">${escapeHtml(stop.name)}</div>
                            <div class="sign-sub">${escapeHtml([stop.address, `${stop.city}, ${stop.state}`].filter(Boolean).join(' · '))}</div>
                            <div class="sign-stats">
                                <span>${arrive}</span>
                                <span>buy <b>${stop.gallons_pumped.toFixed(1)}</b> gal</span>
                                <span>pay <b>$${money(stop.cost_at_stop)}</b></span>
                            </div>
                        </div>
                        <div class="pylon">
                            <small>Diesel</small>
                            <span class="lcd">${pylonPrice(stop.retail_price)}</span>
                        </div>
                    </div>
                </li>`);
        });

        items.push(`<li class="connector" style="--i:${i++}">${miles(data.final_leg_miles)}</li>`);
        items.push(`
            <li style="--i:${i++}">
                <div class="guide guide-finish">
                    <div><span class="label">Destination</span><span class="place">${place(data.finish)}</span></div>
                    <span class="dist">$${money(data.total_fuel_cost)}</span>
                </div>
            </li>`);

        const list = $('itinerary');
        list.innerHTML = items.join('');
        list.querySelectorAll('.stop .sign').forEach((sign) => {
            const number = Number(sign.closest('li').dataset.stop);
            sign.addEventListener('click', () => focusStop(number));
            sign.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); focusStop(number); }
            });
        });
    }

    function focusStop(number) {
        const marker = stopMarkers[number];
        if (!marker) return;
        map.setView(marker.getLatLng(), 11, { animate: true });
        marker.openPopup();
        if (window.matchMedia('(max-width: 900px)').matches) {
            $('map').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    // ---------- Events ----------
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        calculateRoute();
    });

    rangeInput.addEventListener('input', () => {
        const value = Math.round(parseFloat(rangeInput.value));
        $('shieldNum').textContent = Number.isFinite(value) ? value : '—';
    });

    $('swapBtn').addEventListener('click', () => {
        [startInput.value, finishInput.value] = [finishInput.value, startInput.value];
    });

    document.querySelectorAll('.preset').forEach((btn) => {
        btn.addEventListener('click', () => {
            startInput.value = btn.dataset.start;
            finishInput.value = btn.dataset.finish;
            calculateRoute();
        });
    });

    $('fitRouteBtn').addEventListener('click', fitRoute);

    $('copyCurlBtn').addEventListener('click', async () => {
        const btn = $('copyCurlBtn');
        try {
            await navigator.clipboard.writeText($('curlCode').textContent);
            btn.textContent = 'Copied ✓';
        } catch {
            btn.textContent = 'Copy failed';
        }
        setTimeout(() => { btn.textContent = 'Copy'; }, 1800);
    });

    if (startInput.value.trim() && finishInput.value.trim()) {
        calculateRoute();
    }
});
