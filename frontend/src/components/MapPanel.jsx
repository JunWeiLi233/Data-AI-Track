import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import { fmt, getJSON } from '../lib/api.js';
import { COST_NOTE, PER_KM_LABEL, costTier, dollarRange, showCostSources } from '../lib/cost.js';
import { MinusIcon, PlusIcon } from './icons.jsx';

// OpenStreetMap's volunteer tile servers are fine for development; use a tile
// provider or self-hosted tiles before heavy use (operations.osmfoundation.org/policies/tiles).
export const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const NYC_BOUNDS = [[40.49, -74.26], [40.92, -73.70]];
const DENSITY_COLORS = ['#E4E0F7', '#C4BEEC', '#9B91DD', '#6E62C8'];
const WALK_RADIUS_M = 800;
// Planning-model chance that a tract is underserved (long commutes), in five bands.
const RISK_BREAKS = [0.1, 0.25, 0.4, 0.55];
const RISK_COLORS = ['#FCEBDD', '#F7C6A0', '#EE9562', '#D9602F', '#A93A17'];
const pct = (v) => Math.round(v * 100) + '%';
const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function densityColor(breaks, d) {
  const i = breaks.findIndex((b) => d < b);
  return DENSITY_COLORS[i < 0 ? DENSITY_COLORS.length - 1 : i];
}

function addTracts(tracts, group) {
  L.geoJSON(tracts, {
    style: (f) => ({ color: '#FFFFFF', weight: 0.3, fillOpacity: f.properties.density == null ? 0 : 0.6, fillColor: densityColor(tracts.breaks, f.properties.density) }),
    onEachFeature: (f, layer) => layer.bindTooltip(f.properties.density == null
      ? 'Tract ' + f.properties.geoid + ': no population estimate'
      : 'Tract ' + f.properties.geoid + ': ' + fmt(f.properties.population) + ' residents · ' + fmt(f.properties.density) + ' per km²', { sticky: true }),
  }).addTo(group);
}

// Every vector layer shares one canvas: a second canvas on top would swallow
// the mouse events that drive tract tooltips. Tract shading is kept underneath.
function toBack(group) {
  group.eachLayer((layer) => layer.bringToBack());
}

function riskColor(p) {
  const i = RISK_BREAKS.findIndex((b) => p < b);
  return RISK_COLORS[i < 0 ? RISK_COLORS.length - 1 : i];
}

function riskTooltip(geoid, t, reasons) {
  if (!t) return 'Tract ' + esc(geoid) + ': no prediction (no residents, income or job data)';
  const lines = [
    '<strong>Tract ' + esc(geoid) + '</strong>',
    pct(t.probability) + ' chance underserved (90% range ' + pct(t.low) + '–' + pct(t.high) + ')',
    t.reason ? 'Main reason: ' + esc(reasons[t.reason]) : 'No factor raises it above an average tract',
  ];
  if (t.observed_long_commute_share != null) {
    lines.push('Census: ' + pct(t.observed_long_commute_share) + ' of workers commute 60+ min');
  }
  return lines.join('<br>');
}

function addRisk(tracts, planning, group) {
  L.geoJSON(tracts, {
    style: (f) => {
      const t = planning.tracts[f.properties.geoid];
      return { color: '#FFFFFF', weight: 0.3, fillOpacity: t ? 0.75 : 0, fillColor: t ? riskColor(t.probability) : '#FFFFFF' };
    },
    onEachFeature: (f, layer) => layer.bindTooltip(riskTooltip(f.properties.geoid, planning.tracts[f.properties.geoid], planning.reasons), { sticky: true }),
  }).addTo(group);
}

function addNetwork(lines, existing, deserts, layers) {
  L.geoJSON(lines, {
    style: (f) => ({ color: f.properties.color, weight: 3, opacity: 0.9, lineCap: 'round', lineJoin: 'round' }),
    onEachFeature: (f, layer) => layer.bindTooltip(f.properties.line + (f.properties.routes.length ? ' (' + f.properties.routes.join(', ') + ')' : ''), { sticky: true }),
  }).addTo(layers.lines);
  existing.stations.forEach((st) => L.circleMarker([st.latitude, st.longitude], { radius: 2.5, color: '#3A414B', weight: 1, fillColor: '#FFFFFF', fillOpacity: 1 })
    .bindTooltip(st.name).addTo(layers.lines));
  L.geoJSON(deserts, {
    style: { color: '#C4363B', weight: 1.5, dashArray: '5 5', fillColor: '#E5484D', fillOpacity: 0.08 },
  }).bindTooltip(deserts.tracts + ' residential tracts, ' + fmt(deserts.population) + ' residents, beyond ' + deserts.threshold_km + ' km of a station', { sticky: true })
    .addTo(layers.deserts);
}

function Detail({ value, label, note }) {
  return (
    <div className="flex flex-col gap-0.5">
      <strong className="text-[15px]">{value}</strong>
      <span className="text-[11px] leading-tight text-muted">{label}</span>
      {note && <span className="text-[11px] leading-tight text-muted/80">{note}</span>}
    </div>
  );
}

// Station details from the optimizer response (backend/optimizer.py). Straight-line
// distances from tract centers, 80 m per minute, 10-min walk = 800 m.
const COST_STYLE = { Low: 'bg-good/10 text-good', Medium: 'bg-badge-soft text-badge', High: 'bg-rec/10 text-error', 'Above High': 'bg-rec/15 text-error' };

function StationDetails({ st, onClose }) {
  const cost = costTier(st);
  const track = st.on_existing_line
    ? 'Infill stop on existing track (' + st.line + (st.routes.length ? ': ' + st.routes.join(', ') : '') + ')'
    : null; // off-network: `near` already gives the new-track distance and line
  return (
    <div role="dialog" aria-label={'Details for recommended station ' + st.rank} className="absolute bottom-3.5 left-3.5 z-[1002] flex max-h-[calc(100%-28px)] w-[300px] max-w-[calc(100%-90px)] flex-col gap-2.5 overflow-y-auto rounded-xl bg-white px-4 py-3.5 shadow-[0_4px_16px_rgba(18,22,28,0.18)]">
      <div className="flex items-start gap-2.5">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-rec text-xs font-bold text-white">{st.rank}</span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <strong className="leading-snug">{st.name}</strong>
          <span className="text-xs text-muted">{st.borough} · Recommended station #{st.rank}</span>
        </div>
        <button type="button" aria-label="Close details" onClick={onClose} className="-mt-1 -mr-1 cursor-pointer rounded-md border-0 bg-transparent px-1.5 text-lg leading-none text-muted hover:bg-page hover:text-ink">×</button>
      </div>
      <div className="flex flex-col gap-1 text-xs leading-snug">
        <span>{st.near}</span>
        {track && <span className="text-muted">{track}</span>}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2.5 border-t border-divider pt-2.5">
        <Detail value={'+' + fmt(st.residents)} label="Residents newly within a 10-min walk" />
        <Detail value={fmt(st.residents_benefiting)} label="Residents with a shorter walk" />
        <Detail value={'−' + st.minutes_saved + ' min'} label="Avg. walk saved" note="for residents with a shorter walk" />
        <Detail value={st.score + '/100'} label="Need score" note="avg. tract priority of those residents" />
      </div>
      <div className="flex flex-col gap-1.5 border-t border-divider pt-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold">Relative cost</span>
          <span className={'rounded-full px-2 py-0.5 text-[11px] font-semibold ' + COST_STYLE[cost.level]}>{cost.level}</span>
        </div>
        <span className="text-xs leading-snug">{cost.text}</span>
        {dollarRange(cost.km)
          ? <span className="text-xs leading-snug">Illustrative construction cost: <strong>{dollarRange(cost.km)}</strong> ({cost.km} km × {PER_KM_LABEL}, from Second Avenue Subway costs)</span>
          : <span className="text-xs leading-snug">Published per-km tunnel costs don’t apply to an infill stop, and the cited sources give no cost for adding a station to existing track.</span>}
        <span className="text-[11px] leading-snug text-muted">{COST_NOTE} <a href="#cost-sources" onClick={showCostSources}>Sources</a></span>
      </div>
      <span className="text-[11px] leading-snug text-muted">Need score weights distance to a station 50%, population 30%, lower income 20%. Modeled estimates, not ridership forecasts.</span>
    </div>
  );
}

// Hover summary: the headline numbers, with the full breakdown one click away.
function stationSummary(st) {
  return '<div class="flex w-[230px] flex-col gap-1.5 whitespace-normal font-sans">'
    + '<strong class="text-sm leading-snug text-ink">' + esc(st.rank) + '. ' + esc(st.name) + '</strong>'
    + '<span class="text-xs leading-snug text-muted">' + esc(st.near) + '</span>'
    + '<span class="text-xs text-ink"><strong>+' + esc(fmt(st.residents)) + '</strong> residents newly within a 10-min walk</span>'
    + '<span class="text-xs text-ink"><strong>−' + esc(st.minutes_saved) + ' min</strong> avg. walk saved · need score <strong>' + esc(st.score) + '/100</strong></span>'
    + '<span class="text-xs text-ink">Relative cost: <strong>' + esc(costTier(st).level) + '</strong> · ' + esc(costTier(st).km ? costTier(st).km + ' km of new track (≈ ' + dollarRange(costTier(st).km) + ' illustrative)' : 'no new track') + '</span>'
    + '<span class="text-[11px] font-semibold text-accent">Click the marker for full details</span>'
    + '</div>';
}

function RiskScale({ planning }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="font-semibold">Underserved risk</span>
      <span className="text-xs text-muted">Chance a tract&apos;s workers have long (60+ min) commutes, from the planning model</span>
      <div className="grid h-2.5 grid-cols-5 overflow-hidden rounded-[3px]">
        {RISK_COLORS.map((c) => <span key={c} style={{ background: c }} />)}
      </div>
      <div className="flex justify-between text-xs text-muted">
        <span>{'< ' + pct(RISK_BREAKS[0])}</span>
        <span>{'≥ ' + pct(RISK_BREAKS[RISK_BREAKS.length - 1])}</span>
      </div>
      <span className="text-xs text-muted">
        {planning.status === 'ready'
          ? 'Ranks tracts with ROC AUC ' + planning.model.roc_auc + ' on held-out districts. Hover a tract for its 90% range and main reason.'
          : planning.status === 'error' ? 'Could not load the model.' : 'Loading model…'}
      </span>
    </div>
  );
}

function Legend({ breaks, risk, planning }) {
  return (
    <div className="absolute top-3.5 left-3.5 z-[1000] flex w-[220px] flex-col gap-2.5 rounded-xl bg-white px-4 py-3.5 shadow-[0_2px_10px_rgba(18,22,28,0.10)]">
      {risk ? <RiskScale planning={planning} /> : (
        <div className="flex flex-col gap-1.5">
          <span className="font-semibold">Population density</span>
          <span className="text-xs text-muted">Residents per km² by census tract (ACS)</span>
          <div className="grid h-2.5 grid-cols-4 overflow-hidden rounded-[3px]">
            {DENSITY_COLORS.map((c) => <span key={c} style={{ background: c }} />)}
          </div>
          <div className="flex justify-between text-xs text-muted">
            <span>{breaks ? '< ' + fmt(breaks[0]) : 'Lower'}</span>
            <span>{breaks ? '≥ ' + fmt(breaks[breaks.length - 1]) : 'Higher'}</span>
          </div>
        </div>
      )}
      <div className="flex flex-col gap-2 border-t border-divider pt-2.5 text-[13px]">
        <span className="flex items-center gap-2.5"><span className="h-[3px] w-[22px] rounded-sm bg-[#0039A6]" />Existing line (station to station)</span>
        <span className="flex items-center gap-2.5"><span className="mx-1.5 size-2.5 rounded-full border-2 border-[#3A414B] bg-white" />Existing station</span>
        <span className="flex items-center gap-2.5"><span className="mx-1 size-3.5 rounded-full border-2 border-white bg-rec shadow-[0_0_0_1px_#E5484D]" />Recommended station</span>
        <span className="flex items-center gap-2.5"><span className="mx-0.5 size-[18px] rounded-full border border-rec/50 bg-rec/20" />10-min walk area (≈800 m)</span>
        <span className="flex items-center gap-2.5"><span className="h-3.5 w-[22px] rounded-full border-[1.5px] border-dashed border-error" />Transit desert (&gt; 15-min walk)</span>
      </div>
    </div>
  );
}

export default function MapPanel({ stations, show, layers: visible, onError }) {
  const el = useRef(null);
  const ref = useRef(null); // { map, layers, touched }
  const [breaks, setBreaks] = useState(null);
  const [legendOpen, setLegendOpen] = useState(true);
  const [selected, setSelected] = useState(null);
  const [planning, setPlanning] = useState({ status: 'idle', model: null });
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    const map = L.map(el.current, { zoomControl: false, preferCanvas: true, minZoom: 9, maxBounds: [[40.2, -74.7], [41.2, -73.3]] }).fitBounds(NYC_BOUNDS);
    L.tileLayer(TILE_URL, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(map);
    const layers = { density: L.layerGroup(), risk: L.layerGroup(), lines: L.layerGroup(), deserts: L.layerGroup(), walk: L.layerGroup(), stations: L.layerGroup() };
    const state = { map, layers, touched: false, tracts: null, planningRequested: false };
    ref.current = state;

    // The panel's size settles after fonts and data load; keep NYC framed until the user moves the map.
    map.on('dragstart', () => { state.touched = true; });
    const onWheel = () => { state.touched = true; };
    el.current.addEventListener('wheel', onWheel, { passive: true });
    const observer = new ResizeObserver(() => {
      map.invalidateSize();
      if (!state.touched) map.fitBounds(NYC_BOUNDS, { animate: false });
    });
    observer.observe(el.current);

    let cancelled = false;
    const tracts = getJSON('/api/tracts');
    state.tracts = tracts;
    Promise.all([
      tracts,
      getJSON('/api/lines'),
      getJSON('/api/stations'),
      getJSON('/api/deserts'),
    ]).then(([tracts, lines, existing, deserts]) => {
      if (cancelled) return;
      addTracts(tracts, layers.density);
      toBack(layers.density);
      addNetwork(lines, existing, deserts, layers);
      setBreaks(tracts.breaks);
    }).catch((e) => {
      if (!cancelled) onErrorRef.current('Could not load map layers: ' + e.message);
    });

    const node = el.current;
    return () => {
      cancelled = true;
      observer.disconnect();
      node.removeEventListener('wheel', onWheel);
      map.remove();
      ref.current = null;
    };
  }, []);

  useEffect(() => {
    const { layers } = ref.current;
    setSelected(null);
    layers.walk.clearLayers();
    layers.stations.clearLayers();
    stations.forEach((st) => {
      const at = [st.latitude, st.longitude];
      L.circle(at, { radius: WALK_RADIUS_M, color: '#E5484D', opacity: 0.5, weight: 1, fillColor: '#E5484D', fillOpacity: 0.22 }).addTo(layers.walk);
      const marker = L.marker(at, {
        zIndexOffset: 1000,
        title: st.name,
        icon: L.divIcon({
          className: 'rec-marker',
          iconSize: [24, 24],
          html: '<span style="width: 24px; height: 24px; border-radius: 50%; background: #E5484D; border: 2.5px solid #FFFFFF; box-sizing: border-box; color: #FFFFFF; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 1px 4px rgba(18,22,28,0.3)">' + st.rank + '</span>',
        }),
      });
      // Hover shows a summary (below the marker when it sits near the top edge);
      // a click opens the full details card in the corner of the map.
      marker.bindTooltip(stationSummary(st), { direction: 'top', offset: [0, -14], opacity: 1, className: 'station-tip' })
        .on('mouseover', () => {
          const below = ref.current.map.latLngToContainerPoint(at).y < 190;
          Object.assign(marker.getTooltip().options, below ? { direction: 'bottom', offset: [0, 14] } : { direction: 'top', offset: [0, -14] });
          marker.getTooltip().update();
        })
        .on('click', () => { marker.closeTooltip(); setSelected(st); })
        .addTo(layers.stations);
    });
  }, [stations]);

  // The planning model is fit on first use (a few seconds), so load it only when its layer is turned on.
  useEffect(() => {
    const state = ref.current;
    if (!visible.risk || state.planningRequested) return;
    state.planningRequested = true;
    setPlanning({ status: 'loading', model: null });
    Promise.all([state.tracts, getJSON('/api/planning-model')]).then(([tracts, body]) => {
      if (ref.current !== state) return;
      addRisk(tracts, body, state.layers.risk);
      toBack(state.layers.risk);
      setPlanning({ status: 'ready', model: body.model });
    }).catch((e) => {
      if (ref.current !== state) return;
      state.planningRequested = false;
      setPlanning({ status: 'error', model: null });
      onErrorRef.current('Could not load the planning model: ' + e.message);
    });
  }, [visible.risk]);

  useEffect(() => {
    const { map, layers } = ref.current;
    // Risk and density both shade tracts; risk wins while it is on.
    const on = { density: visible.density && !visible.risk, risk: visible.risk, lines: visible.lines, deserts: visible.deserts, walk: show && visible.walk, stations: show && visible.stations };
    Object.keys(on).forEach((k) => {
      if (on[k] && !map.hasLayer(layers[k])) {
        layers[k].addTo(map);
        if (k === 'density' || k === 'risk') toBack(layers[k]);
      }
      if (!on[k] && map.hasLayer(layers[k])) map.removeLayer(layers[k]);
    });
  }, [show, visible]);

  const zoom = (delta) => {
    const state = ref.current;
    state.touched = true;
    state.map.setZoom(state.map.getZoom() + delta);
  };

  const zoomButton = 'flex size-11 cursor-pointer items-center justify-center bg-white hover:bg-page';
  return (
    <section aria-label="Map" className="relative h-[calc(100vh-var(--header-h,73px)-32px)] min-h-[480px] min-w-0 flex-1 overflow-hidden rounded-[14px] border border-line bg-water">
      <div ref={el} className="absolute inset-0 z-0" />
      {legendOpen && <Legend breaks={breaks} risk={visible.risk} planning={planning} />}
      {selected && show && <StationDetails st={selected} onClose={() => setSelected(null)} />}
      <button
        type="button"
        aria-expanded={legendOpen}
        onClick={() => setLegendOpen((o) => !o)}
        className={'absolute z-[1001] cursor-pointer rounded-lg border-0 bg-white px-2.5 py-1.5 text-xs font-semibold text-ink shadow-[0_2px_10px_rgba(18,22,28,0.10)] hover:bg-page ' + (legendOpen ? 'top-3.5 left-[246px]' : 'top-3.5 left-3.5')}
      >
        {legendOpen ? 'Hide legend' : 'Show legend'}
      </button>
      <div className="absolute top-3.5 right-3.5 z-[1000] flex flex-col overflow-hidden rounded-[10px] bg-white shadow-[0_2px_10px_rgba(18,22,28,0.10)]">
        <button type="button" aria-label="Zoom in" onClick={() => zoom(1)} className={zoomButton}><PlusIcon /></button>
        <button type="button" aria-label="Zoom out" onClick={() => zoom(-1)} className={zoomButton + ' border-t border-divider'}><MinusIcon /></button>
      </div>
    </section>
  );
}
