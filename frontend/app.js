
class DCLogic {
  constructor(props) { this.props = props || {}; this.state = {}; }
  setState(p) { Object.assign(this.state, typeof p === 'function' ? p(this.state) : p); schedule(); }
}

// OpenStreetMap's volunteer tile servers are fine for development; use a tile
// provider or self-hosted tiles before heavy use (operations.osmfoundation.org/policies/tiles).
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const NYC_BOUNDS = [[40.49, -74.26], [40.92, -73.70]];
const DENSITY_COLORS = ['#E4E0F7', '#C4BEEC', '#9B91DD', '#6E62C8'];
const WALK_RADIUS_M = 800;

async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// Leaflet owns its DOM, so the map element is created once and moved into
// #map-slot after every template render instead of being re-rendered.
class MapView {
  constructor() {
    this.el = document.createElement('div');
    this.el.style.cssText = 'width: 100%; height: 100%';
    this.map = null;
    this.layers = { density: L.layerGroup(), lines: L.layerGroup(), deserts: L.layerGroup(), walk: L.layerGroup(), stations: L.layerGroup() };
    this.breaks = null;
    this.shownStations = null;
  }
  mount(slot) {
    slot.appendChild(this.el);
    if (this.map) return;
    this.map = L.map(this.el, { zoomControl: false, preferCanvas: true, minZoom: 9, maxBounds: [[40.2, -74.7], [41.2, -73.3]] }).fitBounds(NYC_BOUNDS);
    L.tileLayer(TILE_URL, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(this.map);
    this.map.createPane('overlays').style.zIndex = 450;
    // The panel's size settles after fonts and data load; keep NYC framed until the user moves the map.
    this.touched = false;
    this.map.on('dragstart', () => { this.touched = true; });
    this.el.addEventListener('wheel', () => { this.touched = true; }, { passive: true });
    new ResizeObserver(() => {
      this.map.invalidateSize();
      if (!this.touched) this.map.fitBounds(NYC_BOUNDS, { animate: false });
    }).observe(this.el);
  }
  zoom(delta) {
    if (!this.map) return;
    this.touched = true;
    this.map.setZoom(this.map.getZoom() + delta);
  }
  async loadTracts() {
    const tracts = await getJSON('/api/tracts');
    this.breaks = tracts.breaks;
    const fmt = (v) => v.toLocaleString('en-US');
    L.geoJSON(tracts, {
      style: (f) => ({ color: '#FFFFFF', weight: 0.3, fillOpacity: f.properties.density == null ? 0 : 0.6, fillColor: this.densityColor(f.properties.density) }),
      onEachFeature: (f, layer) => layer.bindTooltip(f.properties.density == null
        ? 'Tract ' + f.properties.geoid + ': no population estimate'
        : 'Tract ' + f.properties.geoid + ': ' + fmt(f.properties.population) + ' residents · ' + fmt(f.properties.density) + ' per km²', { sticky: true })
    }).addTo(this.layers.density);
    return tracts.breaks;
  }
  densityColor(d) {
    const i = this.breaks.findIndex((b) => d < b);
    return DENSITY_COLORS[i < 0 ? DENSITY_COLORS.length - 1 : i];
  }
  async loadNetwork() {
    const [overlays, existing] = await Promise.all([getJSON('assets/overlays.geojson'), getJSON('/api/stations')]);
    L.geoJSON(overlays, {
      pane: 'overlays',
      filter: (f) => f.properties.kind === 'line',
      style: (f) => ({ color: f.properties.color, weight: 3, opacity: 0.9, lineCap: 'round', lineJoin: 'round' })
    }).addTo(this.layers.lines);
    existing.stations.forEach((st) => L.circleMarker([st.latitude, st.longitude], { pane: 'overlays', radius: 2.5, color: '#3A414B', weight: 1, fillColor: '#FFFFFF', fillOpacity: 1 })
      .bindTooltip(st.name).addTo(this.layers.lines));
    L.geoJSON(overlays, {
      pane: 'overlays',
      filter: (f) => f.properties.kind === 'desert',
      style: { color: '#C4363B', weight: 1.5, dashArray: '5 5', fillColor: '#E5484D', fillOpacity: 0.08 }
    }).addTo(this.layers.deserts);
  }
  sync(s) {
    if (!this.map) return;
    if (this.shownStations !== s.stations) {
      this.shownStations = s.stations;
      this.layers.walk.clearLayers();
      this.layers.stations.clearLayers();
      s.stations.forEach((st) => {
        const at = [st.latitude, st.longitude];
        L.circle(at, { pane: 'overlays', radius: WALK_RADIUS_M, color: '#E5484D', opacity: 0.5, weight: 1, fillColor: '#E5484D', fillOpacity: 0.22 }).addTo(this.layers.walk);
        L.marker(at, { zIndexOffset: 1000, title: st.name, icon: L.divIcon({ className: 'rec-marker', iconSize: [24, 24],
          html: '<span style="width: 24px; height: 24px; border-radius: 50%; background: #E5484D; border: 2.5px solid #FFFFFF; box-sizing: border-box; color: #FFFFFF; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 1px 4px rgba(18,22,28,0.3)">' + st.rank + '</span>' }) })
          .bindTooltip(st.name).addTo(this.layers.stations);
      });
    }
    const on = { density: s.l.density, lines: s.l.lines, deserts: s.l.deserts, walk: s.show && s.l.walk, stations: s.show && s.l.stations };
    Object.keys(on).forEach((k) => {
      if (on[k] && !this.map.hasLayer(this.layers[k])) this.layers[k].addTo(this.map);
      if (!on[k] && this.map.hasLayer(this.layers[k])) this.map.removeLayer(this.layers[k]);
    });
  }
}

// Static thumbnail: 3x3 basemap tiles positioned so the station sits at (52, 46).
function thumbStyle(lat, lon) {
  const z = 15, size = 256, world = size * Math.pow(2, z);
  const px = (lon + 180) / 360 * world;
  const rad = lat * Math.PI / 180;
  const py = (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2 * world;
  const tx = Math.floor(px / size), ty = Math.floor(py / size);
  const images = [], positions = [];
  for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
    images.push('url(https://tile.openstreetmap.org/' + z + '/' + (tx + dx) + '/' + (ty + dy) + '.png)');
    positions.push(Math.round(52 - px + (tx + dx) * size) + 'px ' + Math.round(46 - py + (ty + dy) * size) + 'px');
  }
  return 'position: absolute; inset: 0; filter: grayscale(0.85) brightness(1.04); background-color: #D6E4EE; background-repeat: no-repeat; background-size: ' + size + 'px ' + size + 'px; background-image: ' + images.join(', ') + '; background-position: ' + positions.join(', ');
}

class Component extends DCLogic {
constructor(props) {
super(props);
this.state = Object.assign(this.initial(), { stations: [], sample: true, summary: null, summaryError: '', breaks: null });
this.request = 0;
}
initial() {
return { goal: 'access', show: true, n: 5, status: '', statusError: false,
spacingKm: 1.0, budget: 'Medium',
c: { along: true, spacing: false, budget: false },
l: { density: true, lines: true, stations: true, walk: true, deserts: false } };
}
async loadSummary() {
try { this.setState({ summary: await getJSON('/api/summary') }); }
catch (e) { this.setState({ summaryError: e.message }); }
}
async loadMapData() {
try {
const [breaks] = await Promise.all([mapView.loadTracts(), mapView.loadNetwork()]);
this.setState({ breaks: breaks });
} catch (e) {
this.setState({ status: 'Could not load map layers: ' + e.message, statusError: true });
}
}
// Responses can arrive out of order when settings change quickly; keep only the latest.
async loadRecommendations(status) {
const id = ++this.request;
const { goal, n } = this.state;
try {
const body = await getJSON('/api/recommendations?goal=' + goal + '&n=' + n);
if (id === this.request) this.setState({ stations: body.stations, sample: body.sample, status: status || '', statusError: false });
} catch (e) {
if (id === this.request) this.setState({ status: 'Could not load recommendations: ' + e.message, statusError: true });
}
}
update(patch) { this.setState(patch); this.loadRecommendations(); }
renderVals() {
const s = this.state;
const shown = s.stations;
const fmt = (v) => v.toLocaleString('en-US');
const list = shown.map((st) => ({
rank: st.rank, name: st.name, near: st.near, residents: '+' + fmt(st.residents), mins: '−' + st.minutes_saved + ' min', score: st.score + '/100',
thumbStyle: thumbStyle(st.latitude, st.longitude)
}));
const added = shown.reduce((t, st) => t + st.residents, 0);
const seg = (on) => 'height: 40px; border: 0; border-radius: 8px; font: inherit; font-weight: 600; cursor: pointer; ' + (on ? 'background: #2457D6; color: #FFFFFF' : 'background: #FFFFFF; color: #12161C');
const flip = (group, key) => () => this.setState({ [group]: Object.assign({}, this.state[group], { [key]: !this.state[group][key] }) });
const sum = s.summary;
return {
n: s.n, count: shown.length,
badge: s.sample ? 'Sample data' : 'Modeled results',
accessPressed: String(s.goal === 'access'), effPressed: String(s.goal === 'eff'),
accessStyle: seg(s.goal === 'access'), effStyle: seg(s.goal === 'eff'),
goalText: s.goal === 'access' ? 'Prioritize serving more residents within a 10-minute walk of a subway station.' : 'Prioritize the biggest cuts in door-to-door travel time across the network.',
setAccess: () => this.update({ goal: 'access' }), setEff: () => this.update({ goal: 'eff' }),
showStr: String(s.show), toggleShow: () => this.setState({ show: !this.state.show }),
switchStyle: 'width: 44px; height: 26px; border-radius: 999px; border: 0; padding: 3px; cursor: pointer; display: flex; ' + (s.show ? 'background: #2457D6; justify-content: flex-end' : 'background: #C5CBD3; justify-content: flex-start'),
knobStyle: 'width: 20px; height: 20px; border-radius: 50%; background: #FFFFFF; display: block',
setN: (e) => this.update({ n: Number(e.target.value) }),
c: s.c, l: s.l,
spacingKm: s.spacingKm, setSpacing: (e) => this.setState({ spacingKm: Number(e.target.value) }),
budgetIs: { [s.budget]: true }, setBudget: (e) => this.setState({ budget: e.target.value }),
toggleAlong: flip('c', 'along'), toggleSpacing: flip('c', 'spacing'), toggleBudget: flip('c', 'budget'),
tDensity: flip('l', 'density'), tLines: flip('l', 'lines'), tStations: flip('l', 'stations'), tWalk: flip('l', 'walk'), tDeserts: flip('l', 'deserts'),
run: () => this.loadRecommendations(s.sample ? 'Sample results shown; constraints are not applied until the scoring model is implemented.' : 'Results updated for the current goal and settings.'),
reset: () => this.update(this.initial()),
status: s.status, statusColor: s.statusError ? '#C4363B' : '#127A3E',
densityLow: s.breaks ? '< ' + fmt(s.breaks[0]) : 'Lower', densityHigh: s.breaks ? '≥ ' + fmt(s.breaks[s.breaks.length - 1]) : 'Higher',
zoomIn: () => mapView.zoom(1),
zoomOut: () => mapView.zoom(-1),
list: list, showList: s.show, hideList: !s.show,
countLabel: s.show ? shown.length + (shown.length === 1 ? ' location' : ' locations') : 'Hidden',
added: fmt(added),
dataSummary: sum
? 'Source data: ' + fmt(sum.physical_station_locations) + ' subway station locations, ' + fmt(sum.entrances) + ' entrances, and ' + fmt(sum.tracts) + ' census tracts covering ' + fmt(sum.population) + ' residents (' + fmt(sum.tracts_missing_population) + ' tracts without population estimates).'
: s.summaryError ? 'Source data summary unavailable (' + s.summaryError + ').' : 'Loading source data summary…'
};
}
}

const TPL = document.getElementById('tpl').textContent;
const esc = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const look = (path, scope) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), scope);
function holes(str, scope) {
  return str
    .replace(/\{\{\?([\w.]+)\}\}/g, (_, p) => (look(p, scope) ? 'checked' : ''))
    .replace(/\{\{!([\w.]+)\}\}/g, (_, p) => (look(p, scope) ? 'selected' : ''))
    .replace(/\{\{\s*([\w.$]+)\s*\}\}/g, (_, p) => { const v = look(p, scope); return v == null ? '' : esc(v); });
}
function render(vals) {
  let s = TPL.replace(/<sc-if value="\{\{\s*([\w.]+)\s*\}\}"[^>]*>([\s\S]*?)<\/sc-if>/g, (_, p, inner) => (look(p, vals) ? inner : ''));
  s = s.replace(/<sc-for list="\{\{\s*([\w.]+)\s*\}\}" as="(\w+)"[^>]*>([\s\S]*?)<\/sc-for>/g, (_, p, as, inner) =>
    (look(p, vals) || []).map((item, i) => holes(inner, Object.assign({}, vals, { [as]: item, $index: i }))).join(''));
  return holes(s, vals);
}
const mapView = new MapView();
const comp = new Component({});
const root = document.getElementById('root');
let pending = false;
function schedule() { if (!pending) { pending = true; requestAnimationFrame(draw); } }
function draw() {
  pending = false;
  const vals = comp.renderVals();
  root.innerHTML = render(vals);
  root.querySelectorAll('[data-ev-click]').forEach((el) => el.addEventListener('click', (e) => { e.preventDefault(); look(el.dataset.evClick, vals)(e); }));
  root.querySelectorAll('[data-ev-change]').forEach((el) => el.addEventListener('change', look(el.dataset.evChange, vals)));
  mapView.mount(document.getElementById('map-slot'));
  mapView.sync(comp.state);
}
draw();
comp.loadRecommendations();
comp.loadSummary();
comp.loadMapData();
