export async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

export const fmt = (v) => v.toLocaleString('en-US');
export const compact = (v) => (v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : fmt(v));
