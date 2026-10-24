import { PlayIcon } from './icons.jsx';

const GOAL_TEXT = {
  access: 'Prioritize serving more residents within a 10-minute walk of a subway station.',
  eff: 'Prioritize the biggest cuts in average walking time to the nearest station.',
};

const LAYERS = [
  ['density', 'Population density'],
  ['risk', 'Underserved risk (model)'],
  ['lines', 'Existing subway lines'],
  ['stations', 'Recommended stations'],
  ['walk', '10-minute walk area'],
  ['deserts', 'Transit deserts (far from any station)'],
];

// Matches MAX_STATIONS in backend/optimizer.py.
const MAX_STATIONS = 100;

const fieldClass = 'h-[34px] rounded-lg border border-field bg-white px-2';

function Group({ title, children, first }) {
  return (
    <div className={'flex flex-col gap-3 ' + (first ? '' : 'border-t border-divider pt-[18px]')}>
      <h2 className="m-0 text-base font-bold">{title}</h2>
      {children}
    </div>
  );
}

function Checkbox({ checked, onChange, children }) {
  return (
    <label className="flex min-h-7 items-center gap-2.5">
      <input type="checkbox" checked={checked} onChange={onChange} />
      <span>{children}</span>
    </label>
  );
}

export default function Controls({ state, status, onUpdate, onSet, onRun, onReset }) {
  const { goal, show, n, c, l, spacingKm, budget } = state;
  const flip = (group, key, refresh) => () => (refresh ? onUpdate : onSet)({ [group]: { ...state[group], [key]: !state[group][key] } });

  return (
    <section aria-label="Controls" className="flex min-h-full flex-col gap-5 bg-white p-5">
      <p className="m-0 rounded-[10px] bg-accent-soft px-3 py-2.5 text-[13px] leading-normal text-ink">
        <strong>How it works:</strong> pick a goal and how many stations to add. The map, list and impact numbers update automatically.
      </p>

      <Group title="Optimization goal" first>
        <div className="grid grid-cols-2 gap-[3px] rounded-[10px] border border-field p-[3px]">
          {[['access', 'Accessibility'], ['eff', 'Efficiency']].map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={goal === key}
              onClick={() => onUpdate({ goal: key })}
              className={'h-10 cursor-pointer rounded-lg font-semibold ' + (goal === key ? 'bg-accent text-white' : 'bg-white text-ink')}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="m-0 leading-normal text-muted">{GOAL_TEXT[goal]}</p>
      </Group>

      <Group title="New stations">
        <div className="flex items-center gap-3">
          <button
            type="button"
            role="switch"
            aria-checked={show}
            aria-label="Show recommended locations"
            onClick={() => onSet({ show: !show })}
            className={'flex h-[26px] w-11 cursor-pointer rounded-full p-[3px] ' + (show ? 'justify-end bg-accent' : 'justify-start bg-neutral')}
          >
            <span className="block size-5 rounded-full bg-white" />
          </button>
          <span>Show recommended locations</span>
        </div>
        <label className="flex flex-col gap-2">
          <span className="flex items-center justify-between gap-2">
            <span>Number of stations</span>
            <input
              type="number" min="1" max={MAX_STATIONS} step="1" value={n} aria-label="Number of stations"
              onChange={(e) => { const v = Math.round(Number(e.target.value)); if (v >= 1 && v <= MAX_STATIONS) onUpdate({ n: v }, true); }}
              className={fieldClass + ' w-16 text-right font-semibold'}
            />
          </span>
          <input type="range" min="1" max={MAX_STATIONS} step="1" value={n} aria-label="Number of stations slider" onChange={(e) => onUpdate({ n: Number(e.target.value) }, true)} className="w-full" />
          <span className="flex justify-between text-xs text-muted"><span>1</span><span>{MAX_STATIONS}</span></span>
        </label>
      </Group>

      <Group title={<>Constraints <span className="font-normal text-muted">(optional)</span></>}>
        <Checkbox checked={c.along} onChange={flip('c', 'along', true)}>
          <span title="Only suggest infill stops on current track. Turn off to allow sites that would need new track.">Along existing lines only</span>
        </Checkbox>
        <div className="flex min-h-7 items-center gap-2.5">
          <input id="c-spacing" type="checkbox" checked={c.spacing} onChange={flip('c', 'spacing', true)} />
          <label htmlFor="c-spacing" className="flex-1">Minimum station spacing (km)</label>
          <input
            type="number"
            aria-label="Minimum spacing in kilometers"
            value={spacingKm}
            onChange={(e) => (c.spacing ? onUpdate : onSet)({ spacingKm: e.target.value })}
            step="0.1"
            min="0.2"
            className={fieldClass + ' w-16'}
          />
        </div>
        <div className="flex min-h-7 items-center gap-2.5">
          <input id="c-budget" type="checkbox" checked={c.budget} onChange={flip('c', 'budget', true)} />
          <label htmlFor="c-budget" className="flex-1" title="Low: existing track only · Medium: up to 1.5 km of new track · High: up to 3 km">Maximum budget (relative)</label>
          <select aria-label="Budget level" value={budget} onChange={(e) => (c.budget ? onUpdate : onSet)({ budget: e.target.value })} className={fieldClass}>
            <option>Low</option><option>Medium</option><option>High</option>
          </select>
        </div>
        <p className="m-0 -mt-1 text-xs leading-normal text-muted">
          Budget limits how much new track a station may need: Low = none, Medium = up to 1.5 km, High = up to 3 km. It applies when “Along existing lines only” is off.
        </p>
      </Group>

      <Group title="Map layers">
        <p className="m-0 -mt-1 text-xs text-muted">A transit desert is an area more than a 15-minute walk from any station.</p>
        {LAYERS.map(([key, label]) => (
          <Checkbox key={key} checked={l[key]} onChange={flip('l', key)}>{label}</Checkbox>
        ))}
      </Group>

      <div className="sticky bottom-0 -mx-5 -mb-5 mt-auto flex flex-col gap-2.5 border-t border-divider bg-white px-5 pt-3 pb-5">
        <button type="button" onClick={onRun} className="flex h-[46px] cursor-pointer items-center justify-center gap-2 rounded-[10px] bg-accent font-semibold text-white hover:bg-accent-dark focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          <PlayIcon />Re-run optimization
        </button>
        <button type="button" onClick={onReset} className="h-[46px] cursor-pointer rounded-[10px] border border-field bg-white font-semibold text-ink hover:bg-page">
          Reset
        </button>
        <p role="status" className={'m-0 min-h-[18px] text-[13px] ' + (status.error ? 'text-error' : 'text-good')}>{status.text}</p>
      </div>
    </section>
  );
}
