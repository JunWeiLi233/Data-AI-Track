import { compact, fmt } from '../lib/api.js';
import { BarsIcon, ClockIcon, PeopleIcon, TrainIcon } from './icons.jsx';

function Card({ title, children, className = 'gap-3.5' }) {
  return (
    <section aria-label={title} className={'flex flex-col rounded-[14px] border border-line bg-white p-5 ' + className}>
      <h2 className="m-0 text-base font-bold">{title}</h2>
      {children}
    </section>
  );
}

function Improvement({ icon, value, label }) {
  return (
    <div className="flex items-center gap-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent-soft">{icon}</span>
      <div className="flex flex-col">
        <strong>{value}</strong>
        <span className="text-[13px] text-muted">{label}</span>
      </div>
    </div>
  );
}

export default function ImpactCards({ impact: im, count }) {
  const pending = '…';
  const added = im ? fmt(im.residents_added) : pending;
  const coveragePct = im ? im.coverage_after_pct : 0;
  const walkBefore = im ? im.walk_minutes_before : pending;
  const walkAfter = im ? im.walk_minutes_after : pending;
  const ratio = im ? im.walk_minutes_after / im.walk_minutes_before : 1;

  return (
    <div id="impact" className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
      <Card title="Population coverage">
        <div className="flex items-center gap-5">
          <svg width="120" height="120" viewBox="0 0 120 120" role="img" aria-label={coveragePct + ' percent of residents within a 10-minute walk'}>
            <circle cx="60" cy="60" r="50" fill="none" stroke="#E8EFFD" strokeWidth="16" />
            <circle cx="60" cy="60" r="50" fill="none" stroke="#2457D6" strokeWidth="16" strokeDasharray={(314 * coveragePct / 100).toFixed(0) + ' 314'} transform="rotate(-90 60 60)" />
            <text x="60" y="67" textAnchor="middle" fontSize="22" fontWeight="700" fill="#12161C">{coveragePct}%</text>
          </svg>
          <div className="flex flex-col gap-1">
            <strong className="text-2xl">{im ? compact(im.covered_after) : pending}</strong>
            <span className="text-muted">residents within a 10-min walk</span>
            <strong className="mt-1.5 text-xl text-good">+{added}</strong>
            <span className="text-muted">additional residents vs. today</span>
          </div>
        </div>
      </Card>

      <Card title="Average walk to a station">
        <div className="flex items-end justify-between gap-6">
          <div className="flex flex-col gap-1">
            <strong className="text-3xl">{walkAfter} min</strong>
            <strong className="text-lg text-good">↓ {im ? (100 * (1 - ratio)).toFixed(1) : pending}%</strong>
            <span className="text-muted">vs. current network</span>
          </div>
          <div className="flex h-[130px] items-end gap-4">
            <div className="flex flex-col items-center gap-1.5">
              <span className="font-semibold">{walkBefore} min</span>
              <span className="h-[90px] w-14 rounded-t bg-neutral" />
              <span className="text-xs text-muted">Current</span>
            </div>
            <div className="flex flex-col items-center gap-1.5">
              <span className="font-semibold">{walkAfter} min</span>
              <span className="w-14 rounded-t bg-accent" style={{ height: Math.round(90 * ratio) }} />
              <span className="text-xs font-semibold text-ink">Optimized</span>
            </div>
          </div>
        </div>
      </Card>

      <Card title="New stations">
        <div className="flex items-center gap-5">
          <span className="flex size-[72px] items-center justify-center rounded-full bg-[#EEF1F5]"><TrainIcon size={34} color="#12161C" wheels={false} /></span>
          <div className="flex flex-col gap-1">
            <strong className="text-[34px]">{count}</strong>
            <a href="#recommendations" className="no-underline">recommended locations →</a>
          </div>
        </div>
      </Card>

      <Card title="Key improvements" className="gap-3">
        <Improvement icon={<PeopleIcon />} value={'+' + added} label="additional residents served" />
        <Improvement icon={<ClockIcon />} value={'−' + (im ? (im.walk_minutes_before - im.walk_minutes_after).toFixed(1) : pending) + ' min'} label="average walk to a station" />
        <Improvement
          icon={<BarsIcon />}
          value={im ? im.on_existing_lines + ' of ' + count + ' on existing track' : pending}
          label={im && im.boroughs.length ? 'in ' + im.boroughs.join(', ') : ''}
        />
      </Card>
    </div>
  );
}
