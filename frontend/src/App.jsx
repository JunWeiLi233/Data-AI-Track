import { useCallback, useEffect, useRef, useState } from 'react';
import { fmt, getJSON } from './lib/api.js';
import Header from './components/Header.jsx';
import Controls from './components/Controls.jsx';
import MapPanel from './components/MapPanel.jsx';
import RecommendationList from './components/RecommendationList.jsx';
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
    <p id="data" className="m-0 text-xs leading-normal text-muted">
      {lead} Stations are placed greedily on candidate sites every 200 m along the existing lines (or, when allowed, at tract centers off the network) using the backend scoring model, then checked with the expansion simulator. Distances are straight-line, walks assume 80 m per minute, and need scores reflect distance, population and income; they are planning proxies, not ridership or cost forecasts. Basemap © OpenStreetMap contributors. Tract boundaries and population: NYC Open Data and the U.S. Census Bureau ACS. Existing lines are rebuilt from the GTFS stations and the named lines in the entrance data, drawn station to station; walk areas are 800 m circles.
    </p>
  );
}

export default function App() {
  const [settings, setSettings] = useState(initialSettings);
  const [result, setResult] = useState({ stations: [], impact: null });
  const [status, setStatus] = useState({ text: '', error: false });
  const [summary, setSummary] = useState({ data: null, error: '' });
  const request = useRef(0);

  // Responses can arrive out of order when settings change quickly; keep only the latest.
  const loadRecommendations = useCallback(async (s, announce) => {
    const id = ++request.current;
    const params = new URLSearchParams({ goal: s.goal, n: s.n, along: s.c.along ? '1' : '0' });
    if (s.c.spacing) params.set('spacing', s.spacingKm);
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
  const update = (patch) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    loadRecommendations(next);
  };
  const reset = () => {
    const next = initialSettings();
    setSettings(next);
    loadRecommendations(next);
  };
  const mapError = useCallback((text) => setStatus({ text, error: true }), []);

  return (
    <div className="flex min-h-screen w-full flex-col">
      <Header />
      <main id="explore" className="flex flex-1 flex-col gap-4 p-4">
        <div className="flex flex-wrap items-stretch gap-4">
          <Controls
            state={settings}
            status={status}
            onSet={set}
            onUpdate={update}
            onRun={() => loadRecommendations(settings, true)}
            onReset={reset}
          />
          <MapPanel stations={result.stations} show={settings.show} layers={settings.l} onError={mapError} />
          <RecommendationList stations={result.stations} show={settings.show} />
        </div>
        <ImpactCards impact={result.impact} count={result.stations.length} />
        <Copilot recommendations={{ goal: settings.goal, constraints: { alongExistingLines: settings.c.along, minSpacingKm: settings.c.spacing ? settings.spacingKm : null, budget: settings.c.budget ? settings.budget : null }, stations: result.stations, impact: result.impact }} />
        <DataNote summary={summary.data} error={summary.error} />
      </main>
    </div>
  );
}
