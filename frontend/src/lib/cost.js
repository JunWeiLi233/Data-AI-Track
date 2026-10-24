// Relative cost proxy. The data has no dollar construction costs; the only cost
// signal is how much new track a site needs (offline_km). Tiers mirror
// BUDGET_OFFLINE_KM in backend/optimizer.py: max km of new track per budget level.
export const BUDGET_NEW_TRACK_KM = { Low: 0, Medium: 1.5, High: 3 };

export function costTier(st) {
  const km = st.on_existing_line ? 0 : st.offline_km;
  if (km <= BUDGET_NEW_TRACK_KM.Low) return { level: 'Low', km, text: 'Infill stop on existing track, no new track needed' };
  if (km <= BUDGET_NEW_TRACK_KM.Medium) return { level: 'Medium', km, text: 'Needs ' + km + ' km of new track (within the Medium budget limit of 1.5 km)' };
  if (km <= BUDGET_NEW_TRACK_KM.High) return { level: 'High', km, text: 'Needs ' + km + ' km of new track (within the High budget limit of 3 km)' };
  return { level: 'Above High', km, text: 'Needs ' + km + ' km of new track (more than the High budget limit of 3 km)' };
}

export const totalNewTrackKm = (stations) =>
  Math.round(100 * stations.reduce((sum, st) => sum + (st.on_existing_line ? 0 : st.offline_km), 0)) / 100;

export const COST_NOTE = 'Relative cost is based only on the new track a station needs. Dollar figures multiply that length by published NYC per-km costs; they are illustrative ranges, not estimates for this site.';

// Open the methodology section and jump to the cost sources.
export const showCostSources = () => {
  const data = document.getElementById('data');
  if (data) data.open = true;
};

// Published NYC subway construction costs per km, checked against each source (Oct 2026).
// Whole-project figures for fully tunneled extensions, stations and systems included.
export const COST_SOURCES = [
  {
    id: 'fta-sas2',
    project: 'Second Avenue Subway Phase 2',
    perKmB: 7699.03 / (1.76 * 1.609344) / 1000, // $7,699.03M over 1.76 mi (2.83 km)
    detail: '$7,699.03M total project cost (FFGA, year-of-expenditure dollars) for a 1.76-mile (2.83 km) extension with 3 new stations',
    publisher: 'Federal Transit Administration, project profile (February 2024)',
    url: 'https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-03/NY-New-York-Second-Avenue-Subway-Phase-2-Profile-AR25.pdf',
  },
  {
    id: 'tcp-sas2',
    project: 'Second Avenue Subway Phase 2',
    perKmB: 2.4231,
    detail: '$6,300M for 2.6 km with 3 stations, $2,423.1M per km (2024 USD)',
    publisher: 'Transit Costs Project, NYU Marron Institute, project database',
    url: 'https://transitcosts.com/?p=7410',
  },
  {
    id: 'tcp-sas1',
    project: 'Second Avenue Subway Phase 1',
    perKmB: 1.7041,
    detail: '$4,601M for 2.7 km with 3 stations, $1,704.1M per km (2012 USD)',
    publisher: 'Transit Costs Project, NYU Marron Institute, project database',
    url: 'https://transitcosts.com/newyork-second-avenue-phase-1/',
  },
];
export const GLOBAL_AVERAGE_NOTE = {
  text: 'For comparison, the Transit Costs Project’s database of 900+ rapid-rail projects averages almost $350 million per mile (about $0.22 billion per km).',
  publisher: 'Eric Goldwyn (NYU Marron Institute), Vital City (April 7, 2023)',
  url: 'https://www.vitalcitynyc.org/articles/why-it-costs-4-billion-per-mile-of-subway-track',
};

const perKm = COST_SOURCES.map((s) => s.perKmB);
export const PER_KM_RANGE_B = [Math.min(...perKm), Math.max(...perKm)];

const billions = (v) => '$' + (v < 10 ? v.toFixed(1) : Math.round(v)) + 'B';

// Illustrative dollar range: km of new track × the published NYC per-km range.
// Null for infill stops, where per-km tunnel costs do not apply.
export function dollarRange(km) {
  if (!km) return null;
  return billions(km * PER_KM_RANGE_B[0]) + '–' + billions(km * PER_KM_RANGE_B[1]);
}

export const PER_KM_LABEL = billions(PER_KM_RANGE_B[0]) + '–' + billions(PER_KM_RANGE_B[1]) + ' per km';
