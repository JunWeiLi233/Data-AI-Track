import { useEffect, useRef, useState } from 'react';

const SUGGESTIONS = [
  'Which Queens neighborhoods have the most residents in transit deserts?',
  'Why is the first recommended station ranked first?',
  'Which Bronx tracts have the highest need scores, and how far are they from a station?',
  'How is the need score calculated?',
];
const HISTORY_TURNS = 6;

async function postJSON(url, payload) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// The model is asked for plain sentences, "- " bullets and **bold**; render only those, as elements.
function Inline({ text }) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part);
}

function RichText({ text }) {
  const blocks = [];
  for (const raw of text.split('\n')) {
    const line = raw.trim().replace(/^#+\s*/, '');
    if (!line) continue;
    const bullet = line.match(/^[-*•]\s+(.*)/);
    if (bullet) {
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'list') last.items.push(bullet[1]);
      else blocks.push({ type: 'list', items: [bullet[1]] });
    } else {
      blocks.push({ type: 'p', text: line });
    }
  }
  return blocks.map((b, i) => (b.type === 'list'
    ? <ul key={i} className="my-1 list-disc pl-5">{b.items.map((item, j) => <li key={j}><Inline text={item} /></li>)}</ul>
    : <p key={i} className="mb-1.5"><Inline text={b.text} /></p>));
}

function Message({ m }) {
  const user = m.role === 'user';
  return (
    <article className={'flex max-w-[85%] flex-col gap-1.5 rounded-xl px-3.5 py-3 ' + (user ? 'self-end bg-accent-soft' : 'self-start bg-page') + (m.error ? ' text-error' : '')}>
      <span className="text-xs font-semibold text-muted">{user ? 'You' : 'Copilot'}</span>
      <div className="leading-normal">{user ? m.text : <RichText text={m.text} />}</div>
      {m.sources && m.sources.length > 0 && (
        <span className="text-xs text-muted">
          Sources: {m.sources.map((s) => s.title).join(' · ')}
          {m.sql && !m.sqlError ? ' · SQL result (' + m.rowCount + (m.rowCount === 1 ? ' row)' : ' rows)') : ''}
        </span>
      )}
      {m.sql && (
        <details className="text-[13px]">
          <summary className="cursor-pointer text-accent">SQL run in Snowflake (read-only){m.sqlError ? ' — failed' : ''}</summary>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-ink p-2.5 text-xs whitespace-pre-wrap text-line">{m.sql}</pre>
        </details>
      )}
    </article>
  );
}

// A generic snowflake mark in Snowflake's brand blue; swap in the official logo asset if needed.
function SnowflakeMark({ size = 28 }) {
  const arm = 'M0 -10 V10 M-3.2 -7 L0 -4.2 L3.2 -7 M-3.2 7 L0 4.2 L3.2 7';
  return (
    <svg width={size} height={size} viewBox="-12 -12 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {[0, 60, 120].map((r) => <path key={r} d={arm} transform={'rotate(' + r + ')'} />)}
    </svg>
  );
}

export const OPEN_COPILOT = 'open-copilot';

export default function Copilot({ recommendations }) {
  const [open, setOpen] = useState(false);
  const input = useRef(null);

  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_COPILOT, show);
    return () => window.removeEventListener(OPEN_COPILOT, show);
  }, []);
  useEffect(() => { if (open && input.current) input.current.focus(); }, [open]);

  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [model, setModel] = useState('llama3.3-70b');
  const log = useRef(null);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [messages, pending]);

  const ask = async (question) => {
    question = question.trim();
    if (!question || pending) return;
    const history = messages.filter((m) => !m.error).slice(-HISTORY_TURNS).map((m) => ({ role: m.role, text: m.text }));
    setMessages((ms) => [...ms, { role: 'user', text: question }]);
    setDraft('');
    setPending(true);
    try {
      const body = await postJSON('/api/chat', { question, history, recommendations });
      setModel(body.model);
      setMessages((ms) => [...ms, {
        role: 'assistant', text: body.answer, sources: body.sources,
        sql: body.sql, sqlError: body.sql_error, rowCount: body.rows.length,
      }]);
    } catch (e) {
      setMessages((ms) => [...ms, { role: 'assistant', text: e.message, error: true }]);
    } finally {
      setPending(false);
    }
  };

  // Collapsed to a floating Snowflake button; the chat opens as a panel when clicked.
  // The panel stays mounted while hidden so the conversation survives closing it.
  return (
    <>
      <button
        type="button"
        aria-label={open ? 'Close the planning copilot' : 'Ask the planning copilot (Snowflake)'}
        aria-expanded={open}
        title="Ask the planning copilot"
        onClick={() => setOpen((o) => !o)}
        className="fixed right-4 bottom-4 z-[3300] flex size-14 cursor-pointer items-center justify-center rounded-full border-0 bg-white text-[#29B5E8] shadow-[0_4px_16px_rgba(18,22,28,0.22)] hover:bg-page"
      >
        {open ? <span className="text-2xl leading-none text-muted">×</span> : <SnowflakeMark />}
      </button>
    <section id="copilot" aria-label="Planning copilot" hidden={!open} className="fixed right-4 bottom-[88px] z-[3300] flex max-h-[calc(100vh-120px)] w-[440px] max-w-[calc(100vw-32px)] flex-col gap-3.5 rounded-[14px] border border-line bg-white p-5 shadow-[0_8px_30px_rgba(18,22,28,0.22)]">
      <div className="flex items-start gap-3 border-b border-divider pb-2.5">
        <span className="text-[#29B5E8]"><SnowflakeMark size={22} /></span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 className="m-0 text-lg font-bold">Ask the planning copilot</h2>
          <span className="text-xs text-muted">Snowflake analytics + AI_COMPLETE ({model}) over the planning tables</span>
        </div>
      </div>
      <div ref={log} role="log" aria-live="polite" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
        {messages.length === 0 && (
          <p className="m-0 leading-normal text-muted">
            Ask about transit deserts, neighborhoods, stations or the recommendations on the map. Answers are grounded in Snowflake tables and list what they used.
          </p>
        )}
        {messages.map((m, i) => <Message key={i} m={m} />)}
        {pending && <p className="m-0 text-muted">Searching profiles and querying Snowflake…</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((q) => (
          <button key={q} type="button" disabled={pending} onClick={() => ask(q)}
            className="cursor-pointer rounded-full border border-field bg-white px-3 py-2 text-left text-[13px] text-ink disabled:cursor-default disabled:opacity-60">
            {q}
          </button>
        ))}
      </div>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
        <input
          ref={input} type="text" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={1000} autoComplete="off"
          aria-label="Ask the planning copilot" placeholder="e.g. Which Bronx neighborhoods have the highest need scores?"
          className="h-11 min-w-0 flex-1 rounded-[10px] border border-field px-3.5"
        />
        <button type="submit" disabled={pending || !draft.trim()}
          className="h-11 cursor-pointer rounded-[10px] border-0 bg-accent px-5 font-semibold text-white disabled:cursor-default disabled:opacity-60">
          {pending ? 'Thinking…' : 'Ask'}
        </button>
      </form>
    </section>
    </>
  );
}
