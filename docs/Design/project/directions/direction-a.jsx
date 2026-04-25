// Direction A: Terminal Precision
// Monospace DNA, green accent, subtle CRT vibe, but modern (not skeuomorphic)
// Palette: deep near-black bg, green accent, amber/red status

const A = {
  bg: '#0a0c0a',
  surface: '#10130f',
  surface2: '#161a14',
  border: '#1f2620',
  borderHi: '#2a3328',
  text: '#c8d2be',
  textDim: '#7a8275',
  textSub: '#505a4c',
  accent: '#7ee38a',       // terminal green
  accentDim: '#3d6b42',
  warn: '#f3c26b',
  err: '#e87a6a',
  info: '#7ab0e8',
  mono: '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace',
  sans: 'Inter, -apple-system, system-ui, sans-serif',
};

// ─── Landing ───────────────────────────────────────────────────────────────
function A_Landing() {
  const lines = [
    { p: '$', c: 'agentcli --run-now --repo "C:/Dev/BudgetBook" \\' },
    { p: ' ', c: '  --autopilot --continuous --iterations 5' },
    { p: '→', c: 'AgentCLI v0.8.2 · backend=claudecode', t: 'info' },
    { p: '→', c: 'PM stage · emitted 5 tasks · schema v2 OK', t: 'ok' },
    { p: '→', c: 'Dev stage · task=T-017 · rule-engine', t: 'run' },
    { p: '→', c: 'QA stage · pending', t: 'dim' },
  ];
  return (
    <div style={{ ...fillRoot(A), fontFamily: A.sans, color: A.text, position:'relative', overflow:'hidden' }}>
      {/* grid bg */}
      <div style={{ position:'absolute', inset:0, backgroundImage:
        `linear-gradient(${A.border} 1px, transparent 1px), linear-gradient(90deg, ${A.border} 1px, transparent 1px)`,
        backgroundSize:'48px 48px', opacity:0.35, maskImage:'radial-gradient(circle at 60% 30%, #000 30%, transparent 75%)' }}/>
      {/* nav */}
      <div style={{ position:'relative', display:'flex', alignItems:'center', padding:'22px 48px', borderBottom:`1px solid ${A.border}` }}>
        <div style={{ display:'flex', alignItems:'center', gap:10, fontFamily:A.mono, fontSize:15.5, letterSpacing:'-0.01em' }}>
          <span style={{ color:A.accent }}>▍</span>
          <span style={{ fontWeight:600 }}>agentcli</span>
          <span style={{ color:A.textSub, fontSize:13.5 }}>v0.8.2</span>
        </div>
        <div style={{ flex:1 }}/>
        <nav style={{ display:'flex', gap:28, fontSize:14.5, color:A.textDim }}>
          <a style={{ color:A.text }}>Dashboard</a>
          <a>Docs</a>
          <a>Skills</a>
          <a>GitHub ↗</a>
        </nav>
      </div>

      {/* hero */}
      <div style={{ position:'relative', padding:'96px 48px 64px', display:'grid', gridTemplateColumns:'1.1fr 1fr', gap:64, alignItems:'center' }}>
        <div>
          <div style={{ display:'inline-flex', gap:6, alignItems:'center', padding:'4px 10px', border:`1px solid ${A.borderHi}`, borderRadius:2, fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginBottom:28 }}>
            <span style={{ color:A.accent }}>●</span> OPEN SOURCE · MIT
          </div>
          <h1 style={{ fontFamily:A.mono, fontSize:56, fontWeight:500, lineHeight:1.02, letterSpacing:'-0.03em', margin:0, color:A.text }}>
            Leave it running.<br/>
            Wake up to a PR.
          </h1>
          <p style={{ fontSize:18.5, lineHeight:1.55, color:A.textDim, maxWidth:460, marginTop:24 }}>
            A CLI-first multi-agent runner with a <span style={{color:A.text}}>PM → Dev → QA</span> pipeline.
            Now with a web console so you can watch it work from anywhere.
          </p>
          <div style={{ display:'flex', gap:12, marginTop:36 }}>
            <button style={{ background:A.accent, color:'#0a0c0a', border:'none', padding:'12px 20px', fontFamily:A.mono, fontSize:14.5, fontWeight:600, borderRadius:2, cursor:'pointer', letterSpacing:'-0.01em' }}>
              Open Dashboard →
            </button>
            <button style={{ background:'transparent', color:A.text, border:`1px solid ${A.borderHi}`, padding:'12px 20px', fontFamily:A.mono, fontSize:14.5, borderRadius:2, cursor:'pointer' }}>
              git clone
            </button>
          </div>
          <div style={{ display:'flex', gap:32, marginTop:56, fontSize:13.5, fontFamily:A.mono, color:A.textSub }}>
            <div><div style={{ color:A.text, fontSize:26, fontWeight:500 }}>2</div>backends</div>
            <div><div style={{ color:A.text, fontSize:26, fontWeight:500 }}>125+</div>config keys</div>
            <div><div style={{ color:A.text, fontSize:26, fontWeight:500 }}>9</div>stop reasons</div>
          </div>
        </div>

        {/* terminal card */}
        <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:4, overflow:'hidden', boxShadow:'0 40px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(126,227,138,0.04)' }}>
          <div style={{ display:'flex', alignItems:'center', gap:8, padding:'10px 14px', borderBottom:`1px solid ${A.border}`, fontFamily:A.mono, fontSize:12.5, color:A.textDim }}>
            <span style={{ width:10, height:10, borderRadius:'50%', background:'#3a3a3a' }}/>
            <span style={{ width:10, height:10, borderRadius:'50%', background:'#3a3a3a' }}/>
            <span style={{ width:10, height:10, borderRadius:'50%', background:'#3a3a3a' }}/>
            <span style={{ marginLeft:10 }}>~/dev/BudgetBook — agentcli</span>
          </div>
          <div style={{ padding:'20px 18px', fontFamily:A.mono, fontSize:14.5, lineHeight:1.7 }}>
            {lines.map((l, i) => (
              <div key={i} style={{ display:'flex', gap:10, color: l.t==='ok'? A.accent : l.t==='run'? A.warn : l.t==='info'? A.info : l.t==='dim'? A.textSub : A.text }}>
                <span style={{ color:A.textSub, width:12 }}>{l.p}</span>
                <span>{l.c}</span>
                {i === lines.length-1 && <span style={{ color:A.accent, animation:'acli-cursor 1s step-end infinite' }}>▍</span>}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* feature strip */}
      <div style={{ position:'relative', padding:'0 48px 80px', display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:1, background:A.border, border:`1px solid ${A.border}`, margin:'0 48px 48px', borderRadius:4, overflow:'hidden' }}>
        {[
          { k:'01', t:'PM → Dev → QA', d:'Structured output schema forces the planner to emit valid, runnable backlogs. Dev edits code. QA verifies and loops back.' },
          { k:'02', t:'Two backends', d:'Swap between Codex and Claude Code via one config flag. Both use CLI login — no API keys to manage.' },
          { k:'03', t:'Safe by default', d:'Worktree isolation, budget guardrails, quota pre-checks, secret scanning. You leave it running, it stays polite.' },
        ].map(f => (
          <div key={f.k} style={{ background:A.bg, padding:'32px 28px' }}>
            <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.accent, marginBottom:14 }}>{f.k}</div>
            <div style={{ fontSize:18.5, fontWeight:500, marginBottom:10, letterSpacing:'-0.01em' }}>{f.t}</div>
            <div style={{ fontSize:14.5, lineHeight:1.55, color:A.textDim }}>{f.d}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Dashboard content (for embedding in Shell) ───────────────────────────
function A_DashboardContent() {
  const run = MOCK.activeRun;
  return (
    <div style={{ padding:24, fontFamily:A.sans, color:A.text }}>
        {/* header */}
        <div style={{ display:'flex', alignItems:'baseline', gap:16, marginBottom:4 }}>
          <h2 style={{ fontFamily:A.mono, fontSize:26, margin:0, letterSpacing:'-0.02em', fontWeight:500 }}>Overview</h2>
          <span style={{ fontFamily:A.mono, fontSize:13.5, color:A.textSub }}>#{run.id.slice(-6)} · started {fmtTime(run.startedAt)} · {fmtDuration(run.elapsedSec)}</span>
          <div style={{ flex:1 }}/>
          <button style={btnA(A, false)}>{Icon.stop()} stop</button>
          <button style={btnA(A, true)}>{Icon.play()} restart</button>
        </div>
        <div style={{ fontSize:14.5, color:A.textDim, marginBottom:24, fontFamily:A.mono }}>
          repo <span style={{color:A.text}}>{run.repo}</span> · branch <span style={{color:A.text}}>{run.branch}</span> · backend <span style={{color:A.accent}}>{run.backend}</span>
        </div>

        {/* KPI row */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:1, background:A.border, border:`1px solid ${A.border}`, borderRadius:3, marginBottom:20 }}>
          {[
            { l:'STAGE',      v:run.stage, sub:`iter ${run.iteration}/${run.maxIterations}`, accent:true },
            { l:'TASKS',      v:'2/5',     sub:'3 pending · 0 failed' },
            { l:'TOKENS',     v:'227K',    sub:`in ${Math.round(run.tokens.in/1000)}K · out ${Math.round(run.tokens.out/1000)}K` },
            { l:'BUDGET',     v:'$3.27',   sub:'of $8.00 · 41%' },
          ].map((k,i) => (
            <div key={i} style={{ background:A.surface, padding:'14px 16px' }}>
              <div style={{ fontFamily:A.mono, fontSize:11.5, letterSpacing:'0.12em', color:A.textSub }}>{k.l}</div>
              <div style={{ fontFamily:A.mono, fontSize:30, fontWeight:500, marginTop:6, color: k.accent ? A.accent : A.text }}>{k.v}</div>
              <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginTop:4 }}>{k.sub}</div>
            </div>
          ))}
        </div>

        {/* pipeline inline + logs */}
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:20 }}>
          {/* pipeline mini */}
          <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, padding:18 }}>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:18 }}>
              <div style={{ fontFamily:A.mono, fontSize:13.5, color:A.text }}>Pipeline</div>
              <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim }}>iter 3 / 5</div>
            </div>
            <A_Pipeline inline/>
          </div>
          {/* goals */}
          <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, padding:18 }}>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:14 }}>
              <div style={{ fontFamily:A.mono, fontSize:13.5 }}>Goals · P0</div>
              <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim }}>2/4 done</div>
            </div>
            {MOCK.goals.p0.map((g,i) => (
              <div key={i} style={{ display:'flex', gap:10, padding:'6px 0', borderTop: i? `1px dashed ${A.border}`:'none' }}>
                <span style={{ fontFamily:A.mono, fontSize:12.5, color: g.done? A.accent : A.textSub, width:14 }}>
                  {g.done ? '[x]' : '[ ]'}
                </span>
                <span style={{ fontSize:14.5, color: g.done ? A.textDim : A.text, textDecoration: g.done ? 'line-through' : 'none' }}>{g.text}</span>
              </div>
            ))}
          </div>
        </div>

        {/* logs */}
        <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, marginTop:20 }}>
          <div style={{ display:'flex', alignItems:'center', gap:12, padding:'10px 14px', borderBottom:`1px solid ${A.border}`, fontFamily:A.mono, fontSize:12.5 }}>
            <span style={{ color:A.text }}>logs · cycle_summary.log</span>
            <span style={{ color:A.textSub }}>tail -f</span>
            <div style={{ flex:1 }}/>
            <PulseDot color={A.accent} size={6}/>
            <span style={{ color:A.accent }}>live</span>
          </div>
          <div style={{ padding:'10px 14px', fontFamily:A.mono, fontSize:13.5, lineHeight:1.75, maxHeight:220, overflow:'auto' }}>
            {MOCK.logs.slice(-10).map((l, i) => (
              <div key={i} style={{ display:'flex', gap:12, color: l.lvl==='warn'? A.warn : l.lvl==='err'? A.err : l.lvl==='debug'? A.textSub : A.text }}>
                <span style={{ color:A.textSub }}>{l.t}</span>
                <span style={{ width:48, color: l.stage==='PM'? A.info : l.stage==='Dev'? A.accent : l.stage==='QA'? A.warn : A.textDim }}>[{l.stage}]</span>
                <span style={{ color:A.textSub, width:48, textTransform:'uppercase' }}>{l.lvl}</span>
                <span>{l.msg}</span>
              </div>
            ))}
            <div style={{ display:'flex', gap:12, color:A.accent }}>
              <span style={{ color:A.textSub }}>15:10:</span>
              <span style={{ animation:'acli-cursor 1s step-end infinite' }}>▍</span>
            </div>
          </div>
        </div>
    </div>
  );
}

// Keep A_Dashboard as the standalone (with its own chrome) version for the canvas/landing use.
// Shell uses A_DashboardContent instead.
function A_Dashboard() { return <A_DashboardContent/>; }

// ─── Pipeline ──────────────────────────────────────────────────────────────
function A_Pipeline({ inline }) {
  const stages = MOCK.stages;
  const node = (s, i) => {
    const color = s.status==='done'? A.accent : s.status==='running'? A.warn : A.textSub;
    return (
      <div key={s.id} style={{ flex:1, minWidth:0 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:8 }}>
          <div style={{ width:28, height:28, borderRadius:2, border:`1px solid ${color}`, background: s.status==='running'? 'rgba(243,194,107,0.08)':'transparent', display:'flex',alignItems:'center',justifyContent:'center', fontFamily:A.mono, fontSize:12.5, color, position:'relative' }}>
            {s.status === 'done' ? Icon.check(12) : s.status === 'running' ? <PulseDot color={color} size={8}/> : s.label[0]}
          </div>
          <div>
            <div style={{ fontFamily:A.mono, fontSize:14.5, color: s.status==='pending'? A.textDim : A.text }}>{s.label}</div>
            <div style={{ fontFamily:A.mono, fontSize:11.5, color:A.textSub, textTransform:'uppercase' }}>{s.status}</div>
          </div>
        </div>
        <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginLeft:36 }}>
          {s.title}<br/>
          <span style={{ color:A.textSub }}>{s.model} · {fmtDuration(s.durationSec)}</span>
        </div>
      </div>
    );
  };

  return (
    <div>
      <div style={{ display:'flex', alignItems:'flex-start', gap:8, position:'relative' }}>
        {stages.map((s, i) => (
          <React.Fragment key={s.id}>
            {node(s, i)}
            {i < stages.length-1 && (
              <div style={{ alignSelf:'flex-start', marginTop:13, width:20, flexShrink:0 }}>
                <svg width="20" height="2">
                  <line x1="0" y1="1" x2="20" y2="1"
                    stroke={stages[i].status==='done' ? A.accent : A.border}
                    strokeWidth="1"
                    strokeDasharray={stages[i+1].status==='running' ? '3 3' : 'none'}
                    style={stages[i+1].status==='running' ? { animation:'acli-flow 0.8s linear infinite' } : {}}
                  />
                </svg>
              </div>
            )}
          </React.Fragment>
        ))}
      </div>
      {!inline && (
        <div style={{ marginTop:24, display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:12 }}>
          {stages.map(s => (
            <div key={s.id} style={{ background:A.bg, border:`1px solid ${A.border}`, borderRadius:3, padding:14, fontFamily:A.mono, fontSize:12.5 }}>
              <div style={{ color:A.textDim, marginBottom:8 }}>[{s.label}] recent output</div>
              {s.id==='PM' && <div>emitted 5 tasks<br/>schema v2 OK<br/><span style={{color:A.textSub}}>(...)</span></div>}
              {s.id==='Dev' && <div><span style={{color:A.accent}}>edit</span> src/category/rules.py<br/><span style={{color:A.accent}}>edit</span> src/db/overrides.sql<br/><span style={{color:A.warn}}>warn</span> 2 type warnings</div>}
              {s.id==='QA' && <div style={{color:A.textSub}}>pending...</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Run Detail with Backlog ───────────────────────────────────────────────
function A_RunDetail() {
  return (
    <div style={{ ...fillRoot(A), fontFamily: A.sans, color: A.text, padding:24, overflow:'auto' }}>
      <div style={{ display:'flex', alignItems:'center', gap:10, fontFamily:A.mono, fontSize:13.5, color:A.textDim, marginBottom:6 }}>
        <span style={{color:A.textSub}}>runs</span><span>/</span>
        <span>{MOCK.activeRun.id}</span>
        <span style={{marginLeft:'auto', color:A.accent, display:'inline-flex', gap:6, alignItems:'center'}}><PulseDot color={A.accent} size={7}/>running</span>
      </div>
      <h2 style={{ fontFamily:A.mono, fontSize:26, margin:'4px 0 20px', fontWeight:500, letterSpacing:'-0.02em' }}>Backlog</h2>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:14 }}>
        {[
          { k:'pending',     label:'Pending',     color: A.textDim },
          { k:'in_progress', label:'In Progress', color: A.warn },
          { k:'done',        label:'Done',        color: A.accent },
        ].map(col => {
          const items = MOCK.backlog.filter(t => t.status === col.k);
          return (
            <div key={col.k} style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, display:'flex', flexDirection:'column' }}>
              <div style={{ padding:'10px 14px', borderBottom:`1px solid ${A.border}`, display:'flex', alignItems:'center', gap:8, fontFamily:A.mono, fontSize:12.5 }}>
                <span style={{ color: col.color }}>● </span>
                <span>{col.label}</span>
                <span style={{ marginLeft:'auto', color:A.textSub }}>{items.length}</span>
              </div>
              <div style={{ padding:10, display:'flex', flexDirection:'column', gap:8, minHeight:320 }}>
                {items.map(t => (
                  <div key={t.id} style={{ background:A.bg, border:`1px solid ${A.border}`, borderRadius:3, padding:'10px 12px' }}>
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
                      <span style={{ fontFamily:A.mono, fontSize:12.5, color:A.textSub }}>{t.id}</span>
                      <span style={{ fontFamily:A.mono, fontSize:11.5, padding:'1px 6px', color: t.priority==='P0'? A.err : A.warn, border:`1px solid ${t.priority==='P0'? A.err : A.warn}`, borderRadius:2 }}>{t.priority}</span>
                    </div>
                    <div style={{ fontSize:14.5, lineHeight:1.4, marginBottom:8 }}>{t.title}</div>
                    <div style={{ display:'flex', gap:6, fontFamily:A.mono, fontSize:11.5 }}>
                      {t.tags.map(tag => <span key={tag} style={{ color:A.textDim, padding:'1px 6px', border:`1px solid ${A.border}`, borderRadius:2 }}>{tag}</span>)}
                      <span style={{ marginLeft:'auto', color:A.textSub }}>· {t.estimate}</span>
                    </div>
                    {col.k==='in_progress' && (
                      <div style={{ marginTop:8, height:2, background:A.border, borderRadius:1, overflow:'hidden' }}>
                        <div style={{ width:'62%', height:'100%', background: `linear-gradient(90deg, ${A.accent}, transparent)`, backgroundSize:'200% 100%', animation:'acli-shimmer 1.8s linear infinite' }}/>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {/* metrics row */}
      <div style={{ marginTop:20, display:'grid', gridTemplateColumns:'1.4fr 1fr 1fr', gap:14 }}>
        <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, padding:14 }}>
          <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginBottom:6 }}>Tokens · last 24h</div>
          <div style={{ fontFamily:A.mono, fontSize:28, color:A.text, marginBottom:2 }}>3.2M <span style={{fontSize:12.5, color:A.accent}}>+12%</span></div>
          <Sparkline data={MOCK.metrics.tokens24h} width={320} height={44} stroke={A.accent} fill="rgba(126,227,138,0.12)"/>
        </div>
        <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, padding:14 }}>
          <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginBottom:6 }}>Budget · this run</div>
          <div style={{ fontFamily:A.mono, fontSize:28, color:A.text }}>$3.27 <span style={{fontSize:12.5, color:A.textSub}}>/ $8.00</span></div>
          <div style={{ marginTop:12, display:'flex', gap:2 }}>
            {Array.from({length:24}).map((_,i) => (
              <div key={i} style={{ flex:1, height:28, background: i<10 ? A.accent : i<12 ? A.warn : A.border, opacity: i<12? 1 : 0.5 }}/>
            ))}
          </div>
        </div>
        <div style={{ background:A.surface, border:`1px solid ${A.border}`, borderRadius:3, padding:14 }}>
          <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginBottom:6 }}>Success · 24 runs</div>
          <div style={{ fontFamily:A.mono, fontSize:28, color:A.text }}>87.5%</div>
          <div style={{ marginTop:12, display:'grid', gridTemplateColumns:'repeat(24,1fr)', gap:2 }}>
            {MOCK.metrics.success24h.map((s,i)=>(
              <div key={i} style={{ height:14, background: s? A.accent : A.err, opacity:0.85 }}/>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Mobile (Telegram-style remote) ────────────────────────────────────────
function A_Mobile() {
  return (
    <div style={{ ...fillRoot(A), fontFamily: A.sans, color: A.text, display:'flex', flexDirection:'column' }}>
      {/* status bar */}
      <div style={{ display:'flex', justifyContent:'space-between', padding:'8px 18px', fontFamily:A.mono, fontSize:12.5, color:A.textDim }}>
        <span>22:47</span>
        <span>• • •</span>
      </div>
      {/* header */}
      <div style={{ padding:'6px 18px 14px', borderBottom:`1px solid ${A.border}` }}>
        <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textSub, letterSpacing:'0.1em' }}>HOME-PC-MAIN</div>
        <div style={{ display:'flex', alignItems:'center', gap:8, marginTop:2 }}>
          <PulseDot color={A.accent} size={8}/>
          <span style={{ fontSize:20, fontFamily:A.mono }}>BudgetBook</span>
          <span style={{ marginLeft:'auto', fontFamily:A.mono, fontSize:12.5, color:A.accent }}>running</span>
        </div>
        <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textDim, marginTop:4 }}>run_…142311 · 47m elapsed</div>
      </div>

      {/* pipeline mini-vertical */}
      <div style={{ padding:'16px 18px', borderBottom:`1px solid ${A.border}` }}>
        {MOCK.stages.map((s, i) => {
          const color = s.status==='done'? A.accent : s.status==='running'? A.warn : A.textSub;
          return (
            <div key={s.id} style={{ display:'flex', gap:12, alignItems:'center', padding:'8px 0' }}>
              <div style={{ width:22, height:22, borderRadius:2, border:`1px solid ${color}`, display:'flex', alignItems:'center', justifyContent:'center', fontFamily:A.mono, fontSize:11.5, color }}>
                {s.status === 'done' ? Icon.check(10) : s.status === 'running' ? <PulseDot color={color} size={6}/> : s.label[0]}
              </div>
              <div style={{ flex:1 }}>
                <div style={{ fontFamily:A.mono, fontSize:14.5 }}>{s.label} · <span style={{color:A.textDim}}>{s.title}</span></div>
                <div style={{ fontFamily:A.mono, fontSize:11.5, color:A.textSub }}>{s.model} · {s.status==='running'?'live':fmtDuration(s.durationSec)}</div>
              </div>
              {s.status === 'running' && <div style={{ fontFamily:A.mono, fontSize:11.5, color:A.warn }}>iter 3/5</div>}
            </div>
          );
        })}
      </div>

      {/* notifications */}
      <div style={{ padding:'14px 18px', flex:1, overflow:'auto' }}>
        <div style={{ fontFamily:A.mono, fontSize:12.5, color:A.textSub, letterSpacing:'0.1em', marginBottom:10 }}>NOTIFICATIONS</div>
        {MOCK.notifications.slice(0,5).map((n,i) => {
          const color = n.kind==='task_done'? A.accent : n.kind==='error' || n.kind==='task_failed'? A.err : n.kind==='quota'? A.warn : A.info;
          return (
            <div key={i} style={{ display:'flex', gap:10, padding:'10px 0', borderTop: i? `1px solid ${A.border}` : 'none' }}>
              <span style={{ color, marginTop:5 }}>{Icon.dot(6)}</span>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:14.5, lineHeight:1.4 }}>{n.text}</div>
                <div style={{ fontFamily:A.mono, fontSize:11.5, color:A.textSub, marginTop:2 }}>{n.kind} · {fmtRelative(n.t)}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* command bar */}
      <div style={{ padding:'10px 14px', borderTop:`1px solid ${A.border}`, display:'flex', gap:8, background:A.surface }}>
        {['/status','/detail','/stop','/tail'].map(c => (
          <span key={c} style={{ padding:'6px 10px', border:`1px solid ${A.borderHi}`, borderRadius:2, fontFamily:A.mono, fontSize:12.5, color: c==='/stop' ? A.err : A.text }}>{c}</span>
        ))}
      </div>
    </div>
  );
}

// helpers
function fillRoot(C) {
  return { width:'100%', height:'100%', background:C.bg, boxSizing:'border-box' };
}
function btnA(C, primary) {
  return {
    display:'inline-flex', alignItems:'center', gap:6,
    padding:'6px 12px', fontFamily:C.mono, fontSize:13.5, fontWeight:500, cursor:'pointer',
    border: primary ? 'none' : `1px solid ${C.borderHi}`,
    background: primary ? C.accent : 'transparent',
    color: primary ? '#0a0c0a' : C.text,
    borderRadius:2,
  };
}

Object.assign(window, { A_Landing, A_Dashboard, A_DashboardContent, A_Pipeline, A_RunDetail, A_Mobile });
