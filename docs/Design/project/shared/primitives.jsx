// Shared tiny primitives used across all three directions.
// Each direction overrides colors via CSS vars on its root.

function fmtDuration(sec) {
  if (sec == null) return '—';
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtRelative(date) {
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

function fmtTime(date) {
  return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

// Inline SVG icon helpers (stroke = currentColor)
const Icon = {
  play: (s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="currentColor"><path d="M3 2l7 4-7 4z"/></svg>,
  stop: (s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="currentColor"><rect x="3" y="3" width="6" height="6" rx="1"/></svg>,
  dot:  (s=8)  => <svg width={s} height={s} viewBox="0 0 8 8" fill="currentColor"><circle cx="4" cy="4" r="3"/></svg>,
  arrow:(s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 6h6M7 3l2 3-2 3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  check:(s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2.5 6.5l2.5 2.5 4.5-5.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  x:    (s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3l6 6M9 3l-6 6" strokeLinecap="round"/></svg>,
  clock:(s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="6" cy="6" r="4.5"/><path d="M6 3.5V6l1.75 1.25" strokeLinecap="round"/></svg>,
  chevron:(s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 3l3 3-3 3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  git:  (s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25"><circle cx="3" cy="3" r="1.3"/><circle cx="9" cy="3" r="1.3"/><circle cx="6" cy="9" r="1.3"/><path d="M3 4.3v2.5c0 .5.4.9.9.9h1.3M9 4.3v1.2c0 .5-.4.9-.9.9H7" strokeLinecap="round"/></svg>,
  term: (s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25"><rect x="1.5" y="2" width="9" height="8" rx="1"/><path d="M3.5 5l1.5 1.25L3.5 7.5M6 7.5h3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  bell: (s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25"><path d="M3 8V6a3 3 0 116 0v2l1 1.5H2L3 8zM5 10.5a1 1 0 002 0"/></svg>,
  spark:(s=12) => <svg width={s} height={s} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25"><path d="M1 9l2-3 2 1 2-4 2 3 2-2"/></svg>,
};

// Sparkline
function Sparkline({ data, width=120, height=32, stroke='currentColor', fill='none', strokeWidth=1.25 }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = (max - min) || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => [i*step, height - ((v-min)/range)*height*0.9 - height*0.05]);
  const d = pts.map((p, i) => (i===0?'M':'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = d + ` L ${width} ${height} L 0 ${height} Z`;
  return (
    <svg width={width} height={height} style={{display:'block'}}>
      {fill !== 'none' && <path d={area} fill={fill}/>}
      <path d={d} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round"/>
    </svg>
  );
}

// Pulsing dot — for "running" status
function PulseDot({ color='currentColor', size=8 }) {
  return (
    <span style={{ position:'relative', display:'inline-block', width:size, height:size }}>
      <span style={{
        position:'absolute', inset:0, borderRadius:'50%',
        background: color, opacity:0.4,
        animation: 'acli-pulse 1.6s ease-out infinite',
      }}/>
      <span style={{
        position:'absolute', inset:0, borderRadius:'50%',
        background: color,
      }}/>
    </span>
  );
}

// Inject global animations once
if (typeof document !== 'undefined' && !document.getElementById('acli-primitives-styles')) {
  const s = document.createElement('style');
  s.id = 'acli-primitives-styles';
  s.textContent = `
    @keyframes acli-pulse {
      0%   { transform: scale(1);   opacity: 0.5; }
      70%  { transform: scale(2.4); opacity: 0; }
      100% { transform: scale(2.4); opacity: 0; }
    }
    @keyframes acli-flow {
      0%   { stroke-dashoffset: 24; }
      100% { stroke-dashoffset: 0; }
    }
    @keyframes acli-shimmer {
      0%   { background-position: -200% 0; }
      100% { background-position: 200% 0; }
    }
    @keyframes acli-cursor {
      0%, 49% { opacity: 1; }
      50%, 100% { opacity: 0; }
    }
    @keyframes acli-logfade {
      from { opacity: 0; transform: translateY(4px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes acli-spin {
      to { transform: rotate(360deg); }
    }
  `;
  document.head.appendChild(s);
}

Object.assign(window, { fmtDuration, fmtRelative, fmtTime, Icon, Sparkline, PulseDot });
