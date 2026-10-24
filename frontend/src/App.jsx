import { useCallback, useEffect, useRef, useState } from 'react';
import { fmt, getJSON } from './lib/api.js';
import { COST_SOURCES, GLOBAL_AVERAGE_NOTE } from './lib/cost.js';
import Header from './components/Header.jsx';
import Controls from './components/Controls.jsx';
import MapPanel from './components/MapPanel.jsx';
import ImpactCards from './components/ImpactCards.jsx';
import Copilot from './components/Copilot.jsx';

const initialSettings = () => ({
  goal: 'access', show: true, n: 5,
  spacingKm: 1.0, budget: 'Medium',
  c: { along: true, spacing: false, budget: false },
  l: { density: true, risk: false, lines: true, stations: true, walk: true, deserts: false },
});

function DataNote({ summary, error }) {
  const lead = summary
    ? 'Source data: ' + fmt(summary.physical_station_locations) + ' subway station locations, ' + fmt(summary.entrances) + ' entrances, and ' + fmt(summary.tracts) + ' census tracts covering ' + fmt(summary.population) + ' residents (' + fmt(summary.tracts_missing_population) + ' tracts without population estimates).'
    : error ? 'Source data summary unavailable (' + error + ').' : 'Loading source data summary…';
  return (
    <details id="data" className="scroll-mt-24 rounded-[14px] border border-line bg-white p-5">
      <summary className="cursor-pointer text-base font-bold">Data &amp; methodology</summary>
      <ul className="mt-3 mb-0 list-disc pl-5 text-[13px] leading-normal text-muted">
        <li>Recommendations are a planning model, not a ridership or cost forecast.</li>
        <li>Walks are straight-line distance at 80 m per minute; a 10-minute walk is about 800 m.</li>
        <li>The need score (0–100) combines distance to a station, population and income.</li>
        <li>Cost is shown as a relative proxy (how much new track a station needs, none for infill stops) plus an illustrative dollar range: new-track km × published NYC per-km costs below. Those costs are for whole tunneled projects including stations, in different price years, so they bound the order of magnitude only.</li>
      </ul>
      <div id="cost-sources" className="mt-3 scroll-mt-24 text-[13px] leading-normal">
        <strong>Construction cost sources (NYC, per km)</strong>
        <ul className="mt-1 mb-0 list-disc pl-5 text-muted">
          {COST_SOURCES.map((s) => (
            <li key={s.id}>
              {s.project}: <strong className="text-ink">${(s.perKmB * 1000).toLocaleString('en-US', { maximumFractionDigits: 0 })}M per km</strong>. {s.detail}. <a href={s.url} target="_blank" rel="noreferrer">{s.publisher}</a>
            </li>
          ))}
          <li>{GLOBAL_AVERAGE_NOTE.text} <a href={GLOBAL_AVERAGE_NOTE.url} target="_blank" rel="noreferrer">{GLOBAL_AVERAGE_NOTE.publisher}</a></li>
        </ul>
      </div>
      <p className="mt-3 mb-0 text-xs leading-normal text-muted">
      {lead} Stations are placed greedily on candidate sites every 200 m along the existing lines (or, when allowed, at tract centers off the network) using the backend scoring model, then checked with the expansion simulator. Distances are straight-line, walks assume 80 m per minute, and need scores reflect distance, population and income; they are planning proxies, not ridership or cost forecasts. Basemap © OpenStreetMap contributors. Tract boundaries and population: NYC Open Data and the U.S. Census Bureau ACS. Existing lines are rebuilt from the GTFS stations and the named lines in the entrance data, drawn station to station; walk areas are 800 m circles.
      </p>
    </details>
  );
}

export default function App() {
  const [settings, setSettings] = useState(initialSettings);
  const [result, setResult] = useState({ stations: [], impact: null });
  const [status, setStatus] = useState({ text: '', error: false });
  const [summary, setSummary] = useState({ data: null, error: '' });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const request = useRef(0);
  const debounce = useRef(0);

  // Responses can arrive out of order when settings change quickly; keep only the latest.
  const loadRecommendations = useCallback(async (s, announce) => {
    const id = ++request.current;
    const params = new URLSearchParams({ goal: s.goal, n: s.n, along: s.c.along ? '1' : '0' });
    if (s.c.spacing && Number(s.spacingKm) > 0) params.set('spacing', s.spacingKm);
    if (s.c.budget) params.set('budget', s.budget);
    setStatus({ text: 'Optimizing…', error: false });
    try {
      const body = await getJSON('/api/recommendations?' + params);
      if (id !== request.current) return;
      const placed = body.stations.length;
      const text = placed < s.n ? 'Only ' + placed + ' of ' + s.n + ' stations fit the current constraints.'
        : announce ? 'Placed ' + placed + (placed === 1 ? ' station' : ' stations') + ' for the current goal and constraints.' : '';
      setResult({ stations: body.stations, impact: body.impact });
      setStatus({ text, error: false });
    } catch (e) {
      if (id === request.current) setStatus({ text: 'Could not load recommendations: ' + e.message, error: true });
    }
  }, []);

  useEffect(() => {
    loadRecommendations(initialSettings());
    getJSON('/api/summary')
      .then((data) => setSummary({ data, error: '' }))
      .catch((e) => setSummary({ data: null, error: e.message }));
  }, [loadRecommendations]);

  // `set` only changes settings; `update` also re-runs the optimizer, matching
  // which controls apply immediately and which wait for "Run optimization".
  const set = (patch) => setSettings((s) => ({ ...s, ...patch }));
  // Sliders and typed numbers fire on every step; wait for a pause before optimizing.
  const update = (patch, debounced) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    clearTimeout(debounce.current);
    if (debounced) debounce.current = setTimeout(() => loadRecommendations(next), 350);
    else loadRecommendations(next);
  };
  const reset = () => {
    const next = initialSettings();
    setSettings(next);
    loadRecommendations(next);
  };
  const mapError = useCallback((text) => setStatus({ text, error: true }), []);

  return (
    <div className="flex min-h-screen w-full flex-col">
      <a href="#explore" className="sr-only focus:not-sr-only focus:p-3">Skip to the map</a>
      <Header />
      <div className="flex flex-1 items-start">
      {/* Controls live only in the sidebar: docked on large screens, a slide-out drawer on small ones. */}
      {sidebarOpen && <div aria-hidden="true" onClick={() => setSidebarOpen(false)} className="fixed inset-0 z-[3400] bg-ink/30 lg:hidden" />}
      <aside
        aria-label="Controls sidebar"
        className={'fixed inset-y-0 left-0 z-[3500] w-[320px] max-w-[85vw] shrink-0 overflow-y-auto border-r border-line bg-white shadow-xl transition-transform lg:sticky lg:top-[var(--header-h,73px)] lg:z-10 lg:h-[calc(100vh-var(--header-h,73px))] lg:translate-x-0 lg:shadow-none ' + (sidebarOpen ? 'translate-x-0' : '-translate-x-full')}
      >
        <div className="flex items-center justify-between border-b border-divider px-5 py-3 lg:hidden">
          <strong>Controls</strong>
          <button type="button" aria-label="Close controls" onClick={() => setSidebarOpen(false)} className="cursor-pointer rounded-md border-0 bg-transparent px-2 text-xl leading-none text-muted hover:bg-page">×</button>
        </div>
        <Controls
          state={settings}
          status={status}
          onSet={set}
          onUpdate={update}
          onRun={() => loadRecommendations(settings, true)}
          onReset={reset}
        />
      </aside>
      <button type="button" onClick={() => setSidebarOpen(true)} className="fixed bottom-4 left-4 z-[3300] flex h-11 cursor-pointer items-center gap-2 rounded-full border-0 bg-accent px-5 font-semibold text-white shadow-lg lg:hidden">
        Controls
      </button>
      <main className="flex min-w-0 flex-1 flex-col gap-4 p-4">
        <div id="explore" className="flex scroll-mt-24">
          <MapPanel stations={result.stations} show={settings.show} layers={settings.l} onError={mapError} />
        </div>
        <ImpactCards impact={result.impact} stations={result.stations} />
        <Copilot recommendations={{ goal: settings.goal, constraints: { alongExistingLines: settings.c.along, minSpacingKm: settings.c.spacing ? settings.spacingKm : null, budget: settings.c.budget ? settings.budget : null }, stations: result.stations, impact: result.impact }} />
        <DataNote summary={summary.data} error={summary.error} />
      </main>
      </div>
    </div>
  );
}
