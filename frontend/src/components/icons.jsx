const stroke = { fill: 'none', strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };

export function TrainIcon({ size = 24, color = '#FFFFFF', wheels = true }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" stroke={color} strokeWidth="1.8" {...stroke}>
      <rect x="5" y="3" width="14" height="14" rx="3" />
      <path d="M5 10 H19" />
      {wheels && <><circle cx="9" cy="13.5" r="0.8" fill={color} /><circle cx="15" cy="13.5" r="0.8" fill={color} /></>}
      <path d="M8 17 L6 21 M16 17 L18 21" />
    </svg>
  );
}

export function PlayIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="#FFFFFF"><path d="M6 4 L20 12 L6 20 Z" /></svg>;
}

export function PlusIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" stroke="#12161C" strokeWidth="2" {...stroke}><path d="M12 5 V19 M5 12 H19" /></svg>;
}

export function MinusIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" stroke="#12161C" strokeWidth="2" {...stroke}><path d="M5 12 H19" /></svg>;
}

export function PeopleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" stroke="#2457D6" strokeWidth="2" {...stroke}>
      <circle cx="9" cy="8" r="3.5" /><path d="M3 20 C3 15.5 15 15.5 15 20" /><path d="M16 4.5 A3.5 3.5 0 0 1 16 11.5 M18 15 C20 16 21 17.5 21 20" />
    </svg>
  );
}

export function ClockIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" stroke="#2457D6" strokeWidth="2" {...stroke}>
      <circle cx="12" cy="12" r="8.5" /><path d="M12 7 V12 L15.5 14" />
    </svg>
  );
}

export function BarsIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" stroke="#2457D6" strokeWidth="2" {...stroke}><path d="M5 20 V13 M10 20 V9 M15 20 V12 M20 20 V5" /></svg>;
}
