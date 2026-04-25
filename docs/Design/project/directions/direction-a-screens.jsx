// A direction — ADDITIONAL SCREENS with editing
// Goals (modal edit + propose next), Config (field drawer + validation), Run History, Notifications

const AEX = window.__AEXC = (window.__AEXC || {
  bg: '#0a0c0a', surface: '#10130f', surface2: '#161a14',
  border: '#1f2620', borderHi: '#2a3328',
  text: '#c8d2be', textDim: '#7a8275', textSub: '#505a4c',
  accent: '#7ee38a', accentDim: '#3d6b42',
  warn: '#f3c26b', err: '#e87a6a', info: '#7ab0e8',
  mono: '"JetBrains Mono", ui-monospace, Menlo, monospace',
  sans: 'Inter, system-ui, sans-serif',
});

// Fallback schema in case mock-data.js is stale/cached and doesn't include it.
const FALLBACK_SCHEMA = {
  'repo': { kind:'text', restart:true, desc:'AgentCLI가 작업할 저장소의 절대 경로.', hint:'Windows 경로는 슬래시(/) 사용. CLI: --repo PATH' },
  'execution_backend': { kind:'enum', options:['codex','claudecode'], restart:true, desc:'Dev/QA 단계를 실행할 에이전트 런타임.', hint:'codex = OpenAI Codex CLI · claudecode = Anthropic Claude Code CLI' },
  'roles': { kind:'multienum', options:['PM','Dev','QA','Reporter'], restart:false, desc:'파이프라인에서 실행할 단계. 순서가 중요합니다.', hint:'PM이 가장 먼저. Reporter는 QA 후 요약을 추가합니다.' },
  'autopilot': { kind:'bool', restart:false, desc:'대화형 확인 프롬프트를 건너뜁니다.', hint:'false면 단계 사이에 y/n 대기.' },
  'continuous': { kind:'bool', restart:false, desc:'PM → Dev → QA를 중단 없이 자동 연결.', hint:'autopilot=true와 함께 쓰면 무인 실행.' },
  'iterations': { kind:'number', min:1, max:20, restart:false, desc:'한 번의 run에서 최대 반복 횟수.', hint:'1 반복 = PM→Dev→QA 1사이클.' },
  'worktree_isolation': { kind:'bool', restart:true, desc:'변경 격리를 위해 새 git worktree에서 실행.', hint:'공유 머신에 권장. 시작 시간 ~10초 추가.' },
  'run_tests': { kind:'bool', restart:false, desc:'QA 단계에서 테스트 스위트 실행.' },
  'budget.max_usd': { kind:'number', min:0.5, max:100, step:0.5, restart:false, desc:'USD 지출 한도. 도달 시 러너 중단.' },
  'budget.max_iters': { kind:'number', min:1, max:20, restart:false, desc:'안전 캡. 보통 iterations와 같게 설정.' },
  'budget.max_continuations': { kind:'number', min:0, max:10, restart:false, desc:'Dev가 잘린 응답을 이어갈 수 있는 최대 횟수.' },
  'claudecode.dev_model': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'Dev 단계 기본 모델.', hint:'재시도 반복 시 tier1으로 승급.' },
  'claudecode.dev_model_tier1': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'Dev 재시도 시 승급 대상 모델.' },
  'claudecode.qa_model': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'QA 단계 모델. 대부분 Haiku로 충분.' },
  'claudecode.reporter_model': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'run 요약에 사용할 모델.' },
  'telegram.enabled': { kind:'bool', restart:true, desc:'이벤트를 텔레그램 채팅으로 미러링.' },
  'telegram.instance_name': { kind:'text', restart:false, desc:'텔레그램 메시지에 표시되는 러너 이름.', hint:'여러 러너가 같은 채팅을 공유할 때 유용.' },
};

// ─── localStorage helpers ────────────────────────────────────────────────
const LS_GOALS  = 'agentcli.goals.v1';
const LS_CONFIG = 'agentcli.config.v1';
function lsLoad(key, fallback) {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
  catch { return fallback; }
}
// Deep-merge loaded data over a default template so missing nested keys don't crash getAt.
function deepMerge(base, over) {
  if (over == null || typeof over !== 'object' || Array.isArray(over)) return over == null ? base : over;
  const out = Array.isArray(base) ? [...base] : { ...(base || {}) };
  for (const k of Object.keys(over)) {
    const bv = out[k], ov = over[k];
    if (ov && typeof ov === 'object' && !Array.isArray(ov) && bv && typeof bv === 'object' && !Array.isArray(bv)) {
      out[k] = deepMerge(bv, ov);
    } else {
      out[k] = ov;
    }
  }
  return out;
}
function lsSave(key, data) {
  try { localStorage.setItem(key, JSON.stringify(data)); } catch {}
}

// ─── Goals ────────────────────────────────────────────────────────────────
function A_Goals() {
  const C = AEX;
  const [goals, setGoals] = React.useState(() => {
    const loaded = lsLoad(LS_GOALS, null);
    const safe = loaded && loaded.p0 && loaded.p1 ? loaded : MOCK.goals;
    return { p0: safe.p0 || [], p1: safe.p1 || [] };
  });
  const [editing, setEditing] = React.useState(null); // { bucket, idx, text } | { bucket, idx:'new' } | null
  const [proposeOpen, setProposeOpen] = React.useState(false);

  React.useEffect(() => { lsSave(LS_GOALS, goals); }, [goals]);

  const total = goals.p0.length + goals.p1.length;
  const done  = goals.p0.filter(g=>g.done).length + goals.p1.filter(g=>g.done).length;

  const updateGoal = (bucket, idx, patch) => {
    setGoals(g => ({ ...g, [bucket]: g[bucket].map((x,i) => i===idx ? {...x, ...patch} : x) }));
  };
  const addGoal = (bucket, text, note) => {
    setGoals(g => ({ ...g, [bucket]: [...g[bucket], { done:false, text, note }] }));
  };
  const deleteGoal = (bucket, idx) => {
    setGoals(g => ({ ...g, [bucket]: g[bucket].filter((_,i) => i!==idx) }));
  };
  const moveGoal = (bucket, idx) => {
    const other = bucket === 'p0' ? 'p1' : 'p0';
    setGoals(g => {
      const item = g[bucket][idx];
      return { ...g, [bucket]: g[bucket].filter((_,i) => i!==idx), [other]: [...g[other], item] };
    });
  };

  return (
    <div style={{ padding:28, fontFamily:C.sans, color:C.text }}>
      <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:6 }}>
        <h2 style={{ fontFamily:C.mono, fontSize:26, margin:0, fontWeight:500, letterSpacing:'-0.02em' }}>Goals</h2>
        <span style={{ fontFamily:C.mono, fontSize:13.5, color:C.textSub }}>GOALS.md · changes saved locally</span>
        <div style={{ flex:1 }}/>
        <button onClick={() => setProposeOpen(true)} style={{ padding:'6px 12px', background:'transparent', color:C.info, border:`1px solid ${C.info}`, borderRadius:2, fontFamily:C.mono, fontSize:13.5, cursor:'pointer', display:'inline-flex', gap:6, alignItems:'center' }}>
          <span style={{ fontSize:11.5 }}>✦</span> propose next
        </button>
      </div>
      <div style={{ fontFamily:C.mono, fontSize:14.5, color:C.textDim, marginBottom:24 }}>
        {done}/{total} complete · <span style={{color:C.accent}}>{total? Math.round(done/total*100) : 0}%</span>
      </div>

      <div style={{ display:'flex', height:8, gap:2, marginBottom:32, fontFamily:C.mono }}>
        {Array.from({length:total}).map((_,i) => (
          <div key={i} style={{ flex:1, background: i < done ? C.accent : C.border, opacity: i < done ? 1 : 0.5 }}/>
        ))}
      </div>

      {[
        { k:'p0', label:'Must-Have', color: C.err },
        { k:'p1', label:'Should-Have', color: C.warn },
      ].map(bucket => (
        <div key={bucket.k} style={{ marginBottom:28 }}>
          <div style={{ display:'flex', alignItems:'baseline', gap:10, marginBottom:12 }}>
            <span style={{ fontFamily:C.mono, fontSize:12.5, padding:'2px 8px', border:`1px solid ${bucket.color}`, color:bucket.color, borderRadius:2 }}>{bucket.k.toUpperCase()}</span>
            <span style={{ fontSize:15.5, fontWeight:500 }}>{bucket.label}</span>
            <span style={{ fontFamily:C.mono, fontSize:12.5, color:C.textSub }}>{goals[bucket.k].filter(g=>g.done).length}/{goals[bucket.k].length}</span>
            <div style={{ flex:1 }}/>
            <button onClick={() => setEditing({ bucket:bucket.k, idx:'new' })} style={{ padding:'3px 9px', background:'transparent', color:C.accent, border:`1px dashed ${C.accentDim}`, borderRadius:2, fontFamily:C.mono, fontSize:12.5, cursor:'pointer' }}>+ add goal</button>
          </div>
          <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:3 }}>
            {goals[bucket.k].length === 0 && (
              <div style={{ padding:'20px', fontFamily:C.mono, fontSize:13.5, color:C.textSub, textAlign:'center' }}>no goals in this bucket</div>
            )}
            {goals[bucket.k].map((g, i) => (
              <div key={i} className="acli-goalrow" style={{ display:'flex', gap:14, alignItems:'flex-start', padding:'14px 16px', borderTop: i? `1px solid ${C.border}` : 'none', cursor:'pointer' }}
                onClick={() => setEditing({ bucket: bucket.k, idx: i, text: g.text, note: g.note })}>
                <span onClick={(e) => { e.stopPropagation(); updateGoal(bucket.k, i, { done: !g.done }); }}
                  style={{ fontFamily:C.mono, fontSize:14.5, color: g.done ? C.accent : C.textSub, width:24, cursor:'pointer', userSelect:'none' }}>
                  {g.done ? '[x]' : '[ ]'}
                </span>
                <div style={{ flex:1 }}>
                  <div style={{ fontSize:15, lineHeight:1.5, color: g.done ? C.textDim : C.text, textDecoration: g.done ? 'line-through' : 'none' }}>{g.text}</div>
                  {g.note && <div style={{ fontSize:13.5, lineHeight:1.5, color:C.textSub, marginTop:4, fontFamily:C.mono }}>{g.note}</div>}
                </div>
                <span className="acli-rowhover" style={{ opacity:0, display:'flex', gap:6, fontFamily:C.mono, fontSize:11.5 }}>
                  <button onClick={(e) => { e.stopPropagation(); moveGoal(bucket.k, i); }} style={{ padding:'2px 6px', background:'transparent', color:C.textDim, border:`1px solid ${C.border}`, borderRadius:2, cursor:'pointer' }}>→ {bucket.k === 'p0' ? 'P1' : 'P0'}</button>
                  <button onClick={(e) => { e.stopPropagation(); if (confirm('Delete this goal?')) deleteGoal(bucket.k, i); }} style={{ padding:'2px 6px', background:'transparent', color:C.err, border:`1px solid ${C.err}`, borderRadius:2, cursor:'pointer' }}>del</button>
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}

      <style>{`.acli-goalrow:hover .acli-rowhover { opacity: 1 !important; } .acli-goalrow:hover { background: rgba(126,227,138,0.03); }`}</style>

      {editing && (
        <GoalEditModal
          initial={editing}
          onCancel={() => setEditing(null)}
          onSave={(vals) => {
            if (editing.idx === 'new') addGoal(vals.bucket, vals.text, vals.note);
            else {
              if (vals.bucket !== editing.bucket) {
                // move between buckets
                deleteGoal(editing.bucket, editing.idx);
                addGoal(vals.bucket, vals.text, vals.note);
              } else {
                updateGoal(editing.bucket, editing.idx, { text: vals.text, note: vals.note });
              }
            }
            setEditing(null);
          }}
          onDelete={editing.idx !== 'new' ? () => { deleteGoal(editing.bucket, editing.idx); setEditing(null); } : null}
        />
      )}

      {proposeOpen && <ProposeModal onClose={() => setProposeOpen(false)} onAccept={(proposals) => {
        proposals.forEach(p => addGoal(p.bucket, p.text, p.rationale));
        setProposeOpen(false);
      }}/>}
    </div>
  );
}

function GoalEditModal({ initial, onCancel, onSave, onDelete }) {
  const C = AEX;
  const [bucket, setBucket] = React.useState(initial.bucket);
  const [text, setText] = React.useState(initial.text || '');
  const [note, setNote] = React.useState(initial.note || '');
  const isNew = initial.idx === 'new';
  const invalid = text.trim().length === 0;

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.65)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:200 }} onClick={onCancel}>
      <div onClick={e=>e.stopPropagation()} style={{ width:640, maxWidth:'92vw', background:C.surface, border:`1px solid ${C.borderHi}`, borderRadius:4, boxShadow:'0 40px 80px rgba(0,0,0,0.7)', overflow:'hidden' }}>
        <div style={{ display:'flex', alignItems:'center', gap:10, padding:'14px 20px', borderBottom:`1px solid ${C.border}` }}>
          <span style={{ fontFamily:C.mono, fontSize:12.5, color:C.accent, letterSpacing:'0.12em' }}>// {isNew ? 'NEW GOAL' : 'EDIT GOAL'}</span>
          <div style={{ flex:1 }}/>
          <span style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, padding:'2px 6px', border:`1px solid ${C.border}`, borderRadius:2 }}>esc to close</span>
        </div>
        <div style={{ padding:'22px 20px' }}>
          <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginBottom:6 }}>PRIORITY</div>
          <div style={{ display:'flex', gap:8, marginBottom:22 }}>
            {[
              { k:'p0', label:'P0 · Must-Have', color:C.err },
              { k:'p1', label:'P1 · Should-Have', color:C.warn },
            ].map(b => (
              <button key={b.k} onClick={() => setBucket(b.k)} style={{
                padding:'8px 14px', background: bucket===b.k ? 'rgba(126,227,138,0.08)' : 'transparent',
                color: bucket===b.k ? C.text : C.textDim,
                border:`1px solid ${bucket===b.k ? b.color : C.border}`,
                borderRadius:2, fontFamily:C.mono, fontSize:13.5, cursor:'pointer',
              }}>{b.label}</button>
            ))}
          </div>

          <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginBottom:6 }}>
            GOAL {invalid && <span style={{ color:C.err, marginLeft:6 }}>· required</span>}
          </div>
          <textarea autoFocus value={text} onChange={e=>setText(e.target.value)} rows={2}
            placeholder="What should be true when this is done?"
            style={{ width:'100%', background:C.bg, color:C.text, border:`1px solid ${invalid ? C.err : C.borderHi}`, borderRadius:2, padding:'10px 12px', fontFamily:C.sans, fontSize:15.5, outline:'none', resize:'vertical' }}/>

          <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', margin:'18px 0 6px' }}>NOTE <span style={{ color:C.textSub, fontWeight:'normal' }}>· optional context for PM stage</span></div>
          <textarea value={note} onChange={e=>setNote(e.target.value)} rows={3}
            placeholder="Acceptance criteria, constraints, links…"
            style={{ width:'100%', background:C.bg, color:C.text, border:`1px solid ${C.border}`, borderRadius:2, padding:'10px 12px', fontFamily:C.mono, fontSize:14, outline:'none', resize:'vertical' }}/>
        </div>
        <div style={{ display:'flex', gap:10, padding:'14px 20px', borderTop:`1px solid ${C.border}`, background:C.surface2 }}>
          {onDelete && <button onClick={onDelete} style={{ padding:'7px 14px', background:'transparent', color:C.err, border:`1px solid ${C.err}`, borderRadius:2, fontFamily:C.mono, fontSize:13.5, cursor:'pointer' }}>delete</button>}
          <div style={{ flex:1 }}/>
          <button onClick={onCancel} style={{ padding:'7px 14px', background:'transparent', color:C.text, border:`1px solid ${C.borderHi}`, borderRadius:2, fontFamily:C.mono, fontSize:13.5, cursor:'pointer' }}>cancel</button>
          <button disabled={invalid} onClick={() => onSave({ bucket, text: text.trim(), note: note.trim() })} style={{ padding:'7px 14px', background: invalid ? C.border : C.accent, color: invalid ? C.textSub : C.bg, border:'none', borderRadius:2, fontFamily:C.mono, fontSize:13.5, fontWeight:600, cursor: invalid ? 'not-allowed' : 'pointer' }}>save</button>
        </div>
      </div>
    </div>
  );
}

function ProposeModal({ onClose, onAccept }) {
  const C = AEX;
  const [phase, setPhase] = React.useState('loading'); // loading | ready
  const [selected, setSelected] = React.useState({});
  const proposals = [
    { bucket:'p0', text:'Recurring-transaction detection flags monthly bills automatically', rationale:'Current category rules miss subscriptions; high-value groundwork for budget predictions.' },
    { bucket:'p0', text:'Budget view warns when projected spend > 95% of monthly cap', rationale:'Natural follow-up to the over/under card; fits the existing Budget screen.' },
    { bucket:'p1', text:'CSV import preserves source bank name as a metadata tag', rationale:'Useful for per-account analytics later; small schema change only.' },
    { bucket:'p1', text:'Settings screen dark mode matches rest of app', rationale:'Listed in P1 already but re-framed with scope (Settings only, not full theme system).' },
  ];

  React.useEffect(() => {
    const t = setTimeout(() => setPhase('ready'), 1400);
    return () => clearTimeout(t);
  }, []);

  const toggle = (i) => setSelected(s => ({ ...s, [i]: !s[i] }));
  const chosen = proposals.filter((_, i) => selected[i]);

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.65)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:200 }} onClick={onClose}>
      <div onClick={e=>e.stopPropagation()} style={{ width:720, maxWidth:'92vw', background:C.surface, border:`1px solid ${C.borderHi}`, borderRadius:4, boxShadow:'0 40px 80px rgba(0,0,0,0.7)', overflow:'hidden' }}>
        <div style={{ display:'flex', alignItems:'center', gap:10, padding:'14px 20px', borderBottom:`1px solid ${C.border}` }}>
          <span style={{ fontFamily:C.mono, fontSize:12.5, color:C.info, letterSpacing:'0.12em' }}>✦ PROPOSE NEXT GOALS</span>
          <span style={{ fontFamily:C.mono, fontSize:12, color:C.textSub }}>model=opus-4.5 · reading GOALS.md + last 3 runs</span>
        </div>

        {phase === 'loading' ? (
          <div style={{ padding:'40px 24px', fontFamily:C.mono, fontSize:14.5, color:C.textDim }}>
            <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
              <PulseDot color={C.info} size={7}/>
              <span style={{ color:C.text }}>thinking…</span>
            </div>
            <div style={{ fontSize:13.5 }}>
              <div style={{ marginBottom:5 }}>→ reading GOALS.md (8 items)</div>
              <div style={{ marginBottom:5 }}>→ reading last 3 run summaries</div>
              <div style={{ marginBottom:5 }}>→ cross-referencing BACKLOG completion rate</div>
              <div style={{ color:C.accent, animation:'acli-cursor 1s step-end infinite' }}>→ drafting proposals ▍</div>
            </div>
          </div>
        ) : (
          <div style={{ padding:'16px 20px', maxHeight:'60vh', overflow:'auto' }}>
            <div style={{ fontSize:14, color:C.textDim, marginBottom:14, lineHeight:1.5 }}>
              Based on completed goals and recent backlog velocity, here are <span style={{color:C.text}}>{proposals.length}</span> next goals to consider. Select which to add.
            </div>
            {proposals.map((p, i) => (
              <div key={i} onClick={() => toggle(i)} style={{
                display:'flex', gap:12, alignItems:'flex-start', padding:'14px', marginBottom:8,
                background: selected[i] ? 'rgba(126,227,138,0.06)' : C.surface2,
                border:`1px solid ${selected[i] ? C.accent : C.border}`, borderRadius:3, cursor:'pointer',
              }}>
                <span style={{ fontFamily:C.mono, fontSize:14.5, color: selected[i] ? C.accent : C.textSub, width:20, paddingTop:1 }}>{selected[i] ? '[x]' : '[ ]'}</span>
                <div style={{ flex:1 }}>
                  <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
                    <span style={{ fontFamily:C.mono, fontSize:11.5, padding:'1px 6px', border:`1px solid ${p.bucket==='p0' ? C.err : C.warn}`, color: p.bucket==='p0' ? C.err : C.warn, borderRadius:2 }}>{p.bucket.toUpperCase()}</span>
                    <span style={{ fontSize:15 }}>{p.text}</span>
                  </div>
                  <div style={{ fontSize:13, fontFamily:C.mono, color:C.textSub, lineHeight:1.5, marginLeft:0 }}>↳ {p.rationale}</div>
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display:'flex', gap:10, padding:'14px 20px', borderTop:`1px solid ${C.border}`, background:C.surface2 }}>
          <span style={{ fontFamily:C.mono, fontSize:12.5, color:C.textDim, alignSelf:'center' }}>{chosen.length} selected</span>
          <div style={{ flex:1 }}/>
          <button onClick={onClose} style={{ padding:'7px 14px', background:'transparent', color:C.text, border:`1px solid ${C.borderHi}`, borderRadius:2, fontFamily:C.mono, fontSize:13.5, cursor:'pointer' }}>cancel</button>
          <button disabled={phase !== 'ready' || chosen.length === 0} onClick={() => onAccept(chosen)} style={{ padding:'7px 14px', background: (phase==='ready' && chosen.length) ? C.accent : C.border, color: (phase==='ready' && chosen.length) ? C.bg : C.textSub, border:'none', borderRadius:2, fontFamily:C.mono, fontSize:13.5, fontWeight:600, cursor: (phase==='ready' && chosen.length) ? 'pointer' : 'not-allowed' }}>add {chosen.length} goal{chosen.length===1?'':'s'}</button>
        </div>
      </div>
    </div>
  );
}

// ─── Config ────────────────────────────────────────────────────────────────
function getAt(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setAt(obj, path, value) {
  const parts = path.split('.');
  const next = obj && typeof obj === 'object' ? JSON.parse(JSON.stringify(obj)) : {};
  let cur = next;
  for (let i=0; i<parts.length-1; i++) {
    if (cur[parts[i]] == null || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length-1]] = value;
  return next;
}

function validateField(path, value, schema) {
  const s = schema[path];
  if (!s) return null;
  if (s.kind === 'number') {
    if (typeof value !== 'number' || isNaN(value)) return 'must be a number';
    if (s.min != null && value < s.min) return `must be ≥ ${s.min}`;
    if (s.max != null && value > s.max) return `must be ≤ ${s.max}`;
  }
  if (s.kind === 'text' && (value == null || String(value).trim() === '')) return 'cannot be empty';
  if (s.kind === 'enum' && !s.options.includes(value)) return `must be one of: ${s.options.join(', ')}`;
  return null;
}

function A_Config() {
  const C = AEX;
  const [config, setConfig] = React.useState(() => deepMerge(JSON.parse(JSON.stringify(MOCK.config)), lsLoad(LS_CONFIG, null)));
  const [baseline] = React.useState(() => JSON.parse(JSON.stringify(MOCK.config)));
  const [drawer, setDrawer] = React.useState(null); // { path }
  const schema = (MOCK.configSchema && Object.keys(MOCK.configSchema).length) ? MOCK.configSchema : FALLBACK_SCHEMA;

  React.useEffect(() => { lsSave(LS_CONFIG, config); }, [config]);

  // compute diff against baseline & whether any changed fields require restart
  const diffs = [];
  Object.keys(schema).forEach(path => {
    const a = getAt(baseline, path), b = getAt(config, path);
    if (JSON.stringify(a) !== JSON.stringify(b)) diffs.push({ path, from:a, to:b, restart: schema[path].restart });
  });
  const restartNeeded = diffs.some(d => d.restart);

  const update = (path, value) => setConfig(c => setAt(c, path, value));
  const errorFor = (path) => validateField(path, getAt(config, path), schema);
  const hasErrors = Object.keys(schema).some(p => errorFor(p));

  const sections = [
    { title:'PROJECT', paths:['repo','execution_backend','roles'] },
    { title:'RUNTIME', paths:['autopilot','continuous','iterations','worktree_isolation','run_tests'] },
    { title:'BUDGET',  paths:['budget.max_usd','budget.max_iters','budget.max_continuations'] },
    { title:'CLAUDECODE MODELS', paths:['claudecode.dev_model','claudecode.dev_model_tier1','claudecode.qa_model','claudecode.reporter_model'] },
    { title:'TELEGRAM', paths:['telegram.enabled','telegram.instance_name'] },
  ];

  return (
    <div style={{ padding:28, fontFamily:C.sans, color:C.text, display:'flex', gap:24, position:'relative' }}>
      <div style={{ flex:1, minWidth:0 }}>
        <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:6 }}>
          <h2 style={{ fontFamily:C.mono, fontSize:26, margin:0, fontWeight:500, letterSpacing:'-0.02em' }}>Config</h2>
          <span style={{ fontFamily:C.mono, fontSize:13.5, color:C.textSub }}>~/.agentcli/configs/budgetbook.json</span>
          <div style={{ flex:1 }}/>
          <button onClick={() => { if (confirm('Discard all changes?')) setConfig(JSON.parse(JSON.stringify(baseline))); }}
            style={{ background:'transparent', color:C.textDim, border:`1px solid ${C.borderHi}`, padding:'6px 12px', fontFamily:C.mono, fontSize:13.5, borderRadius:2, cursor:'pointer' }}>reset</button>
          <button disabled={hasErrors || diffs.length===0}
            style={{ background: (hasErrors || diffs.length===0) ? C.border : C.accent, color: (hasErrors || diffs.length===0) ? C.textSub : C.bg, border:'none', padding:'6px 12px', fontFamily:C.mono, fontSize:13.5, fontWeight:600, borderRadius:2, cursor: (hasErrors || diffs.length===0) ? 'not-allowed' : 'pointer' }}>
            {hasErrors ? '⚠ fix errors' : diffs.length === 0 ? 'no changes' : `save ${diffs.length} change${diffs.length===1?'':'s'}`}
          </button>
        </div>
        <div style={{ fontFamily:C.mono, fontSize:13.5, color:C.textDim, marginBottom:18 }}>
          tap any field to open details · changes saved locally · CLI flags &gt; JSON &gt; DEFAULTS
        </div>

        {restartNeeded && (
          <div style={{ display:'flex', gap:10, padding:'10px 14px', background:'rgba(243,194,107,0.08)', border:`1px solid ${C.warn}`, borderRadius:2, marginBottom:18, fontFamily:C.mono, fontSize:13.5 }}>
            <span style={{ color:C.warn }}>⚠</span>
            <span style={{ color:C.text }}>Some pending changes require restarting the runner to take effect.</span>
            <span style={{ color:C.textDim }}>({diffs.filter(d=>d.restart).map(d=>d.path).join(', ')})</span>
          </div>
        )}

        {sections.map(sec => (
          <div key={sec.title} style={{ marginBottom:22 }}>
            <div style={{ fontFamily:C.mono, fontSize:12, color:C.accent, letterSpacing:'0.12em', marginBottom:2 }}>// {sec.title}</div>
            <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:3, padding:'2px 18px' }}>
              {sec.paths.map((path, i) => (
                <ConfigRow key={path}
                  path={path}
                  schema={schema[path]}
                  value={getAt(config, path)}
                  error={errorFor(path)}
                  changed={diffs.some(d => d.path === path)}
                  active={drawer && drawer.path === path}
                  onChange={(v) => update(path, v)}
                  onOpen={() => setDrawer({ path })}
                  first={i === 0}
                />
              ))}
            </div>
          </div>
        ))}

        {diffs.length > 0 && (
          <div style={{ marginTop:28, background:C.surface, border:`1px solid ${C.border}`, borderRadius:3, padding:'14px 18px' }}>
            <div style={{ fontFamily:C.mono, fontSize:12, color:C.accent, letterSpacing:'0.12em', marginBottom:10 }}>// PENDING DIFF</div>
            {diffs.map(d => (
              <div key={d.path} style={{ display:'flex', gap:14, alignItems:'center', fontFamily:C.mono, fontSize:13.5, padding:'6px 0' }}>
                <span style={{ color:C.textDim, width:220, flexShrink:0 }}>{d.path}</span>
                <span style={{ color:C.err, textDecoration:'line-through' }}>{JSON.stringify(d.from)}</span>
                <span style={{ color:C.textSub }}>→</span>
                <span style={{ color:C.accent }}>{JSON.stringify(d.to)}</span>
                {d.restart && <span style={{ marginLeft:'auto', fontSize:11.5, color:C.warn, border:`1px solid ${C.warn}`, padding:'1px 6px', borderRadius:2 }}>requires restart</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      {drawer && (
        <ConfigDrawer
          path={drawer.path}
          schema={schema[drawer.path]}
          value={getAt(config, drawer.path)}
          baseline={getAt(baseline, drawer.path)}
          error={errorFor(drawer.path)}
          onChange={(v) => update(drawer.path, v)}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

function ConfigRow({ path, schema, value, error, changed, active, onChange, onOpen, first }) {
  const C = AEX;
  return (
    <div onClick={onOpen} style={{
      display:'grid', gridTemplateColumns:'240px 1fr auto', gap:18, padding:'12px 0',
      borderTop: first ? 'none' : `1px solid ${C.border}`, alignItems:'center',
      cursor:'pointer',
      background: active ? 'rgba(126,227,138,0.04)' : 'transparent',
      marginLeft: -18, marginRight: -18, paddingLeft: 18, paddingRight: 18,
      borderLeft: active ? `2px solid ${AEX.accent}` : '2px solid transparent',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
        <span style={{ fontFamily:C.mono, fontSize:13.5, color: active ? C.accent : C.text }}>{path}</span>
        {changed && <span style={{ width:5, height:5, background:C.warn, borderRadius:'50%' }} title="unsaved change"/>}
      </div>
      <div onClick={(e) => e.stopPropagation()} style={{ fontFamily:C.mono, fontSize:14.5 }}>
        <InlineControl schema={schema} value={value} error={error} onChange={onChange}/>
      </div>
      <span style={{ fontFamily:C.mono, fontSize:11.5, color: active ? C.accent : C.textSub }}>details →</span>
    </div>
  );
}

function InlineControl({ schema, value, error, onChange }) {
  const C = AEX;
  const errStyle = error ? { border:`1px solid ${C.err}`, background:'rgba(232,122,106,0.06)' } : {};

  if (schema.kind === 'bool') {
    return (
      <button onClick={() => onChange(!value)} style={{
        display:'inline-flex', alignItems:'center', gap:8, padding:'3px 10px', borderRadius:2,
        border:`1px solid ${value ? C.accent : C.borderHi}`,
        color: value ? C.accent : C.textDim,
        background:'transparent', cursor:'pointer', fontFamily:C.mono, fontSize:12.5,
      }}>
        <span style={{ width:6, height:6, borderRadius:'50%', background: value ? C.accent : C.textSub }}/>
        {value ? 'enabled' : 'disabled'}
      </button>
    );
  }
  if (schema.kind === 'enum') {
    return (
      <select value={value} onChange={e => onChange(e.target.value)} style={{
        background:C.bg, color:C.text, border:`1px solid ${C.borderHi}`, borderRadius:2,
        padding:'4px 8px', fontFamily:C.mono, fontSize:13.5, ...errStyle,
      }}>
        {schema.options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  if (schema.kind === 'multienum') {
    return (
      <div style={{ display:'flex', gap:6, flexWrap:'wrap' }}>
        {schema.options.map(o => {
          const on = (value || []).includes(o);
          return (
            <span key={o} onClick={() => {
              const set = new Set(value || []);
              on ? set.delete(o) : set.add(o);
              onChange([...set]);
            }} style={{
              padding:'3px 9px', fontFamily:C.mono, fontSize:12.5, cursor:'pointer',
              border:`1px solid ${on ? C.accent : C.border}`,
              color: on ? C.accent : C.textDim, borderRadius:2,
            }}>{o}</span>
          );
        })}
      </div>
    );
  }
  if (schema.kind === 'number') {
    return (
      <input type="number" value={value} onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        min={schema.min} max={schema.max} step={schema.step || 1}
        style={{ background:C.bg, color:C.text, border:`1px solid ${C.borderHi}`, borderRadius:2, padding:'4px 8px', fontFamily:C.mono, fontSize:13.5, width:110, ...errStyle }}/>
    );
  }
  // text
  return (
    <input type="text" value={value || ''} onChange={e => onChange(e.target.value)}
      style={{ background:C.bg, color:C.text, border:`1px solid ${C.borderHi}`, borderRadius:2, padding:'4px 8px', fontFamily:C.mono, fontSize:13.5, width:'100%', maxWidth:360, ...errStyle }}/>
  );
}

function ConfigDrawer({ path, schema, value, baseline, error, onChange, onClose }) {
  const C = AEX;
  const changed = JSON.stringify(value) !== JSON.stringify(baseline);
  return (
    <div style={{
      width:360, flexShrink:0, position:'sticky', top:0, alignSelf:'flex-start',
      background:C.surface2, border:`1px solid ${C.borderHi}`, borderRadius:3,
      maxHeight:'calc(100vh - 100px)', overflow:'auto',
    }}>
      <div style={{ padding:'14px 18px', borderBottom:`1px solid ${C.border}`, display:'flex', alignItems:'center' }}>
        <span style={{ fontFamily:C.mono, fontSize:12.5, color:C.accent, letterSpacing:'0.12em' }}>// FIELD DETAILS</span>
        <div style={{ flex:1 }}/>
        <button onClick={onClose} style={{ background:'transparent', color:C.textDim, border:'none', fontFamily:C.mono, fontSize:15.5, cursor:'pointer' }}>✕</button>
      </div>

      <div style={{ padding:'18px' }}>
        <div style={{ fontFamily:C.mono, fontSize:14.5, color:C.text, marginBottom:4, wordBreak:'break-all' }}>{path}</div>
        <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:16 }}>
          <span style={{ fontFamily:C.mono, fontSize:11.5, padding:'2px 6px', border:`1px solid ${C.border}`, color:C.info, borderRadius:2 }}>{schema.kind}</span>
          {schema.restart && <span style={{ fontFamily:C.mono, fontSize:11.5, padding:'2px 6px', border:`1px solid ${C.warn}`, color:C.warn, borderRadius:2 }}>restart required</span>}
          {changed && <span style={{ fontFamily:C.mono, fontSize:11.5, padding:'2px 6px', border:`1px solid ${C.accent}`, color:C.accent, borderRadius:2 }}>unsaved</span>}
        </div>

        <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginBottom:6 }}>DESCRIPTION</div>
        <div style={{ fontSize:14.5, color:C.text, lineHeight:1.5, marginBottom:16 }}>{schema.desc}</div>

        {schema.hint && (
          <>
            <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginBottom:6 }}>HINT</div>
            <div style={{ fontSize:13.5, color:C.textDim, fontFamily:C.mono, lineHeight:1.5, marginBottom:16 }}>{schema.hint}</div>
          </>
        )}

        {(schema.min != null || schema.max != null || schema.options) && (
          <>
            <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginBottom:6 }}>CONSTRAINTS</div>
            <div style={{ fontSize:13.5, color:C.textDim, fontFamily:C.mono, marginBottom:16 }}>
              {schema.options && <div>one of: {schema.options.map(o => <span key={o} style={{color:C.text, marginRight:8}}>{o}</span>)}</div>}
              {schema.min != null && <div>min: <span style={{color:C.text}}>{schema.min}</span></div>}
              {schema.max != null && <div>max: <span style={{color:C.text}}>{schema.max}</span></div>}
            </div>
          </>
        )}

        <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginBottom:6 }}>VALUE</div>
        <div style={{ marginBottom:8 }}>
          <InlineControl schema={schema} value={value} error={error} onChange={onChange}/>
        </div>
        {error && <div style={{ fontFamily:C.mono, fontSize:12.5, color:C.err, marginBottom:12 }}>✕ {error}</div>}
        {changed && !error && (
          <div style={{ padding:'8px 12px', background:C.bg, border:`1px dashed ${C.borderHi}`, borderRadius:2, fontFamily:C.mono, fontSize:12.5, color:C.textDim, marginBottom:12 }}>
            <div style={{color:C.err, textDecoration:'line-through'}}>{JSON.stringify(baseline)}</div>
            <div style={{color:C.accent}}>→ {JSON.stringify(value)}</div>
          </div>
        )}

        <div style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, letterSpacing:'0.12em', marginTop:16, marginBottom:6 }}>DEFAULT</div>
        <div style={{ fontFamily:C.mono, fontSize:13.5, color:C.textDim, display:'flex', alignItems:'center', gap:10 }}>
          <span>{JSON.stringify(baseline)}</span>
          {changed && <button onClick={() => onChange(baseline)} style={{ padding:'2px 8px', background:'transparent', color:C.textDim, border:`1px solid ${C.border}`, borderRadius:2, fontFamily:C.mono, fontSize:11.5, cursor:'pointer' }}>revert</button>}
        </div>
      </div>
    </div>
  );
}

// ─── Run History ──────────────────────────────────────────────────────────
function A_History() {
  const C = AEX;
  const totalTasks = MOCK.runs.reduce((s,r)=>s+r.tasksTotal, 0);
  const doneTasks  = MOCK.runs.reduce((s,r)=>s+r.tasksDone,  0);
  const ok = MOCK.runs.filter(r=>r.status==='success').length;
  return (
    <div style={{ padding:28, fontFamily:C.sans, color:C.text, overflow:'auto' }}>
      <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:6 }}>
        <h2 style={{ fontFamily:C.mono, fontSize:26, margin:0, fontWeight:500, letterSpacing:'-0.02em' }}>Run History</h2>
        <span style={{ fontFamily:C.mono, fontSize:13.5, color:C.textSub }}>{MOCK.runs.length} runs · last 3 days</span>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:1, background:C.border, border:`1px solid ${C.border}`, borderRadius:3, marginTop:18, marginBottom:24 }}>
        {[
          { l:'SUCCESS', v:`${ok}/${MOCK.runs.length}`, sub:`${Math.round(ok/MOCK.runs.length*100)}%`, color:C.accent },
          { l:'TASKS',   v:`${doneTasks}/${totalTasks}`, sub:`${Math.round(doneTasks/totalTasks*100)}% completed` },
          { l:'AVG TIME',v:'29m', sub:'σ 18m' },
          { l:'SPEND',   v:'$24.80', sub:'avg $3.54/run' },
        ].map((k,i)=>(
          <div key={i} style={{ background:C.surface, padding:'14px 16px' }}>
            <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em' }}>{k.l}</div>
            <div style={{ fontFamily:C.mono, fontSize:28, fontWeight:500, color: k.color || C.text, marginTop:4 }}>{k.v}</div>
            <div style={{ fontFamily:C.mono, fontSize:12.5, color:C.textDim, marginTop:2 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:3, overflow:'hidden' }}>
        <div style={{ display:'grid', gridTemplateColumns:'110px 1fr 100px 110px 130px 110px', padding:'10px 16px', fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.1em', borderBottom:`1px solid ${C.border}` }}>
          <span>STATUS</span>
          <span>BRANCH / ID</span>
          <span>TASKS</span>
          <span>DURATION</span>
          <span>STARTED</span>
          <span style={{textAlign:'right'}}>ACTION</span>
        </div>
        {MOCK.runs.map((r, i) => {
          const color = r.status==='success'? C.accent : r.status==='failed'? C.err : r.status==='stopped'? C.warn : C.info;
          return (
            <div key={r.id} style={{ display:'grid', gridTemplateColumns:'110px 1fr 100px 110px 130px 110px', padding:'12px 16px', borderTop:`1px solid ${C.border}`, alignItems:'center', fontFamily:C.mono, fontSize:14 }}>
              <span style={{ display:'inline-flex', alignItems:'center', gap:8, color, fontSize:12.5, letterSpacing:'0.08em' }}>
                {r.status==='running' ? <PulseDot color={color} size={5}/> : <span style={{ width:6, height:6, background:color, borderRadius:'50%' }}/>}
                {r.status.toUpperCase()}
              </span>
              <span>
                <span style={{ color:C.text }}>{r.branch}</span>
                <div style={{ fontSize:12, color:C.textSub, marginTop:2 }}>{r.id}</div>
              </span>
              <span style={{ display:'flex', alignItems:'center', gap:6 }}>
                <span style={{ display:'flex', gap:2 }}>
                  {Array.from({length:r.tasksTotal}).map((_,j)=>(
                    <div key={j} style={{ width:8, height:4, background: j<r.tasksDone ? C.accent : r.status==='failed' && j===r.tasksDone ? C.err : C.border }}/>
                  ))}
                </span>
                <span style={{ color:C.textDim, fontSize:12.5 }}>{r.tasksDone}/{r.tasksTotal}</span>
              </span>
              <span style={{ color:C.textDim, fontSize:12.5 }}>{fmtDuration(r.durationSec)}</span>
              <span style={{ color:C.textDim, fontSize:12.5 }}>{fmtRelative(r.startedAt)}</span>
              <span style={{ textAlign:'right' }}>
                <a style={{ color:C.accent, textDecoration:'none', fontSize:12.5, cursor:'pointer' }}>open →</a>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Notifications ────────────────────────────────────────────────────────
function A_Notifications() {
  const C = AEX;
  return (
    <div style={{ padding:28, fontFamily:C.sans, color:C.text }}>
      <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:6 }}>
        <h2 style={{ fontFamily:C.mono, fontSize:26, margin:0, fontWeight:500, letterSpacing:'-0.02em' }}>Notifications</h2>
        <span style={{ fontFamily:C.mono, fontSize:13.5, color:C.textSub }}>cross-run event feed · telegram mirrored</span>
      </div>
      <div style={{ display:'flex', gap:8, margin:'16px 0 20px' }}>
        {['ALL','task_done','task_failed','quota','error','stalled'].map((f,i) => (
          <span key={f} style={{
            padding:'4px 10px', fontFamily:C.mono, fontSize:12.5,
            border:`1px solid ${i===0 ? C.accent : C.borderHi}`,
            color: i===0 ? C.accent : C.textDim,
            borderRadius:2, cursor:'pointer',
          }}>{f}</span>
        ))}
      </div>

      <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:3 }}>
        {MOCK.notifications.map((n, i) => {
          const color = n.kind==='task_done'? C.accent
            : n.kind==='error' || n.kind==='task_failed'? C.err
            : n.kind==='quota'? C.warn
            : n.kind==='stalled'? C.warn
            : C.info;
          return (
            <div key={i} style={{ display:'grid', gridTemplateColumns:'110px 90px 1fr 140px', gap:14, alignItems:'center', padding:'14px 18px', borderTop: i? `1px solid ${C.border}` : 'none' }}>
              <span style={{ fontFamily:C.mono, fontSize:12.5, color, letterSpacing:'0.08em', display:'inline-flex', gap:6, alignItems:'center' }}>
                <span style={{ width:5, height:5, borderRadius:'50%', background:color }}/>
                {n.kind.replace('_','.').toUpperCase()}
              </span>
              <span style={{ fontFamily:C.mono, fontSize:12.5, color:C.textSub }}>{fmtRelative(n.t)}</span>
              <span style={{ fontSize:15, color:C.text, lineHeight:1.4 }}>{n.text}</span>
              <span style={{ fontFamily:C.mono, fontSize:12, color:C.textSub, textAlign:'right' }}>{n.run.slice(-10)}</span>
            </div>
          );
        })}
      </div>

      <div style={{ marginTop:22, display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
        <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:3, padding:16 }}>
          <div style={{ fontFamily:C.mono, fontSize:12, color:C.accent, letterSpacing:'0.12em', marginBottom:10 }}>// TELEGRAM · home-pc-main</div>
          <div style={{ display:'flex', alignItems:'center', gap:10, fontFamily:C.mono, fontSize:13.5, color:C.text }}>
            <PulseDot color={C.accent} size={6}/>
            connected · allowlist 1 chat
          </div>
          <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginTop:12 }}>
            {['run_start','run_stop','task_done','task_failed','quota','error','stalled'].map(e => (
              <span key={e} style={{ fontFamily:C.mono, fontSize:11.5, padding:'2px 8px', border:`1px solid ${C.border}`, color:C.accent, borderRadius:2 }}>{e}</span>
            ))}
          </div>
        </div>
        <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:3, padding:16 }}>
          <div style={{ fontFamily:C.mono, fontSize:12, color:C.accent, letterSpacing:'0.12em', marginBottom:10 }}>// STALLED DETECTION</div>
          <div style={{ fontFamily:C.mono, fontSize:13.5, color:C.text }}>stalled_seconds = 600</div>
          <div style={{ fontFamily:C.mono, fontSize:12.5, color:C.textDim, marginTop:6 }}>
            fires when metrics.jsonl hasn't updated for 10m. sent as push + surfaced here.
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { A_Goals, A_Config, A_History, A_Notifications });
