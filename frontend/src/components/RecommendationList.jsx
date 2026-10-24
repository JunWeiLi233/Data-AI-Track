import { fmt } from '../lib/api.js';

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
  return { backgroundImage: images.join(', '), backgroundPosition: positions.join(', '), backgroundSize: size + 'px ' + size + 'px' };
}

function Stat({ value, label }) {
  return (
    <div className="flex flex-col gap-0.5">
      <strong className="text-[17px]">{value}</strong>
      <span className="text-xs text-muted">{label}</span>
    </div>
  );
}

function StationCard({ st }) {
  return (
    <article className="flex items-center gap-4 border-b border-divider py-4">
      <div className="relative h-[92px] w-[104px] shrink-0 overflow-hidden rounded-[10px] bg-water">
        <div role="img" aria-label={'Map of ' + st.name} className="absolute inset-0 bg-water bg-no-repeat grayscale-[0.85] brightness-[1.04]" style={thumbStyle(st.latitude, st.longitude)} />
        <span className="absolute top-2 left-2 flex size-[22px] items-center justify-center rounded-full bg-rec text-xs font-bold text-white">{st.rank}</span>
        <span className="absolute top-[42px] left-12 size-2 rounded-full bg-rec shadow-[0_0_0_3px_rgba(229,72,77,0.3)]" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        <div className="flex flex-col gap-0.5">
          <h3 className="m-0 text-base font-bold">{st.name}</h3>
          <span className="text-[13px] text-muted">{st.near}</span>
          <span className="text-xs text-muted">
            {st.borough + (st.on_existing_line ? ' · infill on existing track' : ' · needs ' + st.offline_km + ' km of new track')}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Stat value={'+' + fmt(st.residents)} label="New within 10 min" />
          <Stat value={'−' + st.minutes_saved + ' min'} label="Avg. walk saved" />
          <Stat value={st.score + '/100'} label="Need score" />
        </div>
      </div>
    </article>
  );
}

export default function RecommendationList({ stations, show }) {
  const countLabel = show ? stations.length + (stations.length === 1 ? ' location' : ' locations') : 'Hidden';
  return (
    <section id="recommendations" aria-label="Recommended stations" className="flex min-w-[300px] max-w-[480px] flex-[1_1_400px] flex-col gap-1 rounded-[14px] border border-line bg-white p-5">
      <div className="flex items-baseline justify-between border-b border-divider pb-2.5">
        <h2 className="m-0 text-lg font-bold">Recommended stations</h2>
        <span className="text-muted">{countLabel}</span>
      </div>
      {show
        ? stations.map((st) => <StationCard key={st.rank + st.name} st={st} />)
        : <p className="m-0 py-6 text-muted">Recommended locations are hidden. Turn on “Show recommended locations” to see them.</p>}
    </section>
  );
}
