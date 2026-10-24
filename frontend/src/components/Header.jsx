import { useEffect, useRef, useState } from 'react';
import { TrainIcon } from './icons.jsx';
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

export default function Header() {
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
    </header>
  );
}
