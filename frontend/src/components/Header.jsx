import { SearchIcon, TrainIcon } from './icons.jsx';

const SECTIONS = [
  ['#explore', 'Explore'],
  ['#recommendations', 'Recommendations'],
  ['#impact', 'Impact Analysis'],
  ['#copilot', 'Copilot'],
  ['#data', 'Data & Methodology'],
];

export default function Header() {
  return (
    <header className="flex flex-wrap items-center gap-x-8 gap-y-4 border-b border-line bg-white px-6 py-3">
      <div className="flex items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-[10px] bg-ink"><TrainIcon /></span>
        <span className="text-xl font-bold tracking-tight">NYC Subway Optimization</span>
        <span className="rounded-full bg-badge-soft px-2.5 py-1 text-xs font-semibold text-badge">Modeled results</span>
      </div>
      <nav aria-label="Sections" className="flex flex-auto flex-wrap justify-center gap-1">
        {SECTIONS.map(([href, label], i) => (
          <a
            key={href}
            href={href}
            aria-current={i === 0 ? 'page' : undefined}
            className={'rounded-lg px-4 py-2.5 no-underline ' + (i === 0 ? 'bg-accent-soft font-semibold text-accent' : 'font-medium text-ink hover:text-ink')}
          >
            {label}
          </a>
        ))}
      </nav>
      <label className="flex h-11 min-w-[220px] flex-[0_1_360px] items-center gap-2.5 rounded-[10px] border border-field bg-white px-3.5">
        <SearchIcon />
        <input
          type="search"
          aria-label="Search neighborhood, station, or address"
          placeholder="Search neighborhood, station, or address…"
          className="min-w-0 flex-1 border-0 bg-transparent text-ink outline-none"
        />
      </label>
    </header>
  );
}
