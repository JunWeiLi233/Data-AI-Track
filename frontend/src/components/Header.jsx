import { useEffect, useMemo, useRef, useState } from 'react';
import { getJSON } from '../lib/api.js';
import { SearchIcon, TrainIcon } from './icons.jsx';
import { OPEN_COPILOT } from './Copilot.jsx';

const SECTIONS = [
  ['explore', 'Explore'],
  ['impact', 'Impact Analysis'],
  ['copilot', 'Copilot'],
  ['data', 'Data & Methodology'],
];

function useActiveSection() {
  const [active, setActive] = useState('explore');
  useEffect(() => {
    const visible = new Map();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((e) => visible.set(e.target.id, e.isIntersecting ? e.intersectionRatio : 0));
      const best = SECTIONS.map(([id]) => id).filter((id) => visible.get(id) > 0)
        .sort((a, b) => visible.get(b) - visible.get(a))[0];
      if (best) setActive(best);
    }, { rootMargin: '-80px 0px -40% 0px', threshold: [0, 0.1, 0.3, 0.6, 1] });
    SECTIONS.forEach(([id]) => { const el = document.getElementById(id); if (el) observer.observe(el); });
    return () => observer.disconnect();
  }, []);
  return [active, setActive];
}

// Searches existing stations (loaded on first use) and the current recommendations.
function Search({ recommended, onFocus }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [existing, setExisting] = useState(null);
  const box = useRef(null);

  useEffect(() => {
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const load = () => {
    if (!existing) getJSON('/api/stations').then((d) => setExisting(d.stations)).catch(() => setExisting([]));
  };

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    const rec = recommended
      .filter((s) => (s.name + ' ' + s.near + ' ' + s.borough).toLowerCase().includes(q))
      .map((s) => ({ key: 'r' + s.rank, label: s.name, sub: 'Recommended station #' + s.rank + ' · ' + s.borough, lat: s.latitude, lon: s.longitude, rec: true }));
    const seen = new Set();
    const old = (existing || []).filter((s) => s.name.toLowerCase().includes(q) && !seen.has(s.name) && seen.add(s.name)).slice(0, 6)
      .map((s, i) => ({ key: 'e' + i + s.name, label: s.name, sub: 'Existing station', lat: s.latitude, lon: s.longitude }));
    return [...rec, ...old].slice(0, 8);
  }, [query, recommended, existing]);

  const pick = (r) => {
    onFocus({ lat: r.lat, lon: r.lon, label: r.label });
    setQuery(r.label);
    setOpen(false);
    document.getElementById('explore')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div ref={box} className="relative min-w-[220px] flex-[0_1_360px]">
      <label className="flex h-11 items-center gap-2.5 rounded-[10px] border border-field bg-white px-3.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25">
        <SearchIcon />
        <input
          type="search"
          aria-label="Search stations"
          placeholder="Search a station or recommended area…"
          value={query}
          onFocus={() => { load(); setOpen(true); }}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && results[0]) pick(results[0]);
            if (e.key === 'Escape') setOpen(false);
          }}
          className="min-w-0 flex-1 border-0 bg-transparent text-ink outline-none"
        />
      </label>
      {open && query.trim().length >= 2 && (
        <ul role="listbox" className="absolute top-12 right-0 left-0 z-[2000] m-0 list-none overflow-hidden rounded-[10px] border border-line bg-white p-1 shadow-lg">
          {results.length === 0 && <li className="px-3 py-2.5 text-muted">{existing ? 'No stations match “' + query + '”.' : 'Searching…'}</li>}
          {results.map((r) => (
            <li key={r.key} role="option" aria-selected="false">
              <button type="button" onClick={() => pick(r)} className="flex w-full cursor-pointer flex-col rounded-lg border-0 bg-transparent px-3 py-2 text-left hover:bg-accent-soft">
                <span className="font-semibold text-ink">{r.label}</span>
                <span className={'text-xs ' + (r.rec ? 'text-rec' : 'text-muted')}>{r.sub}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Header({ recommended, onFocus }) {
  const [active, setActive] = useActiveSection();
  const el = useRef(null);
  // Publish the header height so the sticky sidebar can sit right below it.
  useEffect(() => {
    const observer = new ResizeObserver(() => document.documentElement.style.setProperty('--header-h', el.current.offsetHeight + 'px'));
    observer.observe(el.current);
    return () => observer.disconnect();
  }, []);
  return (
    <header ref={el} className="lg:sticky lg:top-0 z-[3000] flex flex-wrap items-center gap-x-8 gap-y-3 border-b border-line bg-white px-6 py-3">
      <div className="flex items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-[10px] bg-ink"><TrainIcon /></span>
        <span className="text-xl font-bold tracking-tight">NYC Subway Optimization</span>
        <span title="Results come from a planning model, not ridership or cost forecasts" className="rounded-full bg-badge-soft px-2.5 py-1 text-xs font-semibold text-badge">Modeled results</span>
      </div>
      <nav aria-label="Sections" className="flex flex-auto flex-wrap justify-center gap-1">
        {SECTIONS.map(([id, label]) => (
          <a
            key={id}
            href={'#' + id}
            aria-current={active === id ? 'page' : undefined}
            onClick={(e) => {
              // The copilot is a floating panel, not a page section: open it instead of scrolling.
              if (id === 'copilot') { e.preventDefault(); window.dispatchEvent(new Event(OPEN_COPILOT)); return; }
              setActive(id);
            }}
            className={'rounded-lg px-4 py-2.5 no-underline ' + (active === id ? 'bg-accent-soft font-semibold text-accent' : 'font-medium text-ink hover:bg-page hover:text-ink')}
          >
            {label}
          </a>
        ))}
      </nav>
      <Search recommended={recommended} onFocus={onFocus} />
    </header>
  );
}
