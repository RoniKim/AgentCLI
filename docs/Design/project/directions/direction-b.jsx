// Direction B: Linear Calm
// Linear/Vercel-inspired: airier spacing, rounded corners, violet accent,
// subtle gradients, "calm software" — lots of whitespace, sans-serif primary.

const B = {
  bg: '#0b0b10',
  surface: '#121219',
  surface2: '#181822',
  border: '#1f1f2b',
  borderHi: '#2a2a38',
  text: '#e5e6ed',
  textDim: '#9195a6',
  textSub: '#5d6175',
  accent: '#a68cf7',         // violet
  accent2: '#7a6bd9',
  ok: '#6cd9a8',
  warn: '#f1b872',
  err: '#f47a80',
  info: '#87b5f6',
  sans: 'Inter, -apple-system, system-ui, sans-serif',
  mono: '"JetBrains Mono", ui-monospace, Menlo, monospace',
};

function B_Landing() {
  return (
    <div style={{ width:'100%', height:'100%', background:B.bg, fontFamily:B.sans, color:B.text, overflow:'hidden', position:'relative' }}>
      {/* soft gradient glow */}
      <div style={{ position:'absolute', top:-200, left:'40%', width:900, height:900, background:'radial-gradient(circle, rgba(166,140,247,0.18), transparent 60%)', filter:'blur(40px)', pointerEvents:'none' }}/>
      <div style={{ position:'absolute', top:100, left:-100, width:500, height:500, background:'radial-gradient(circle, rgba(108,217,168,0.08), transparent 60%)', filter:'blur(40px)' }}/>

      {/* nav */}
      <div style={{ position:'relative', display:'flex', alignItems:'center', padding:'20px 56px' }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <div style={{ width:26, height:26, borderRadius:7, background:`linear-gradient(135deg, ${B.accent}, ${B.accent2})`, display:'flex', alignItems:'center', justifyContent:'center', fontWeight:700, fontSize:14.5, color:'#fff' }}>a</div>
          <span style={{ fontWeight:600, fontSize:16.5, letterSpacing:'-0.01em' }}>AgentCLI</span>
        </div>
        <div style={{ flex:1 }}/>
        <nav style={{ display:'flex', gap:28, fontSize:15, color:B.textDim }}>
          <a>Product</a><a>Docs</a><a>Changelog</a><a>GitHub</a>
        </nav>
        <button style={{ marginLeft:24, background:B.text, color:B.bg, border:'none', padding:'7px 14px', borderRadius:8, fontSize:14.5, fontWeight:500, cursor:'pointer' }}>Get started</button>
      </div>

      {/* hero */}
      <div style={{ position:'relative', padding:'80px 56px 40px', textAlign:'center' }}>
        <div style={{ display:'inline-flex', gap:8, alignItems:'center', padding:'5px 12px', background:'rgba(166,140,247,0.08)', border:`1px solid ${B.borderHi}`, borderRadius:999, fontSize:13.5, color:B.textDim, marginBottom:32 }}>
          <span style={{ width:6, height:6, borderRadius:'50%', background:B.accent }}/>
          <span>v0.8.2 · now with web console</span>
        </div>
        <h1 style={{ fontSize:68, fontWeight:500, lineHeight:1.02, letterSpacing:'-0.035em', margin:'0 auto', maxWidth:840, background:`linear-gradient(180deg, ${B.text} 0%, ${B.textDim} 100%)`, WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent' }}>
          The autonomous<br/>pipeline, visualized.
        </h1>
        <p style={{ fontSize:20, lineHeight:1.55, color:B.textDim, maxWidth:580, margin:'24px auto 0' }}>
          AgentCLI runs PM → Dev → QA overnight and hands you a PR.
          The web console lets you watch it think, anywhere.
        </p>
        <div style={{ display:'flex', gap:10, justifyContent:'center', marginTop:36 }}>
          <button style={{ background:B.text, color:B.bg, border:'none', padding:'11px 20px', borderRadius:10, fontSize:15.5, fontWeight:500, cursor:'pointer' }}>Open dashboard →</button>
          <button style={{ background:'transparent', color:B.text, border:`1px solid ${B.borderHi}`, padding:'11px 20px', borderRadius:10, fontSize:15.5, fontWeight:500, cursor:'pointer', fontFamily:B.mono }}>$ git clone</button>
        </div>
      </div>

      {/* product shot */}
      <div style={{ position:'relative', padding:'40px 56px 80px' }}>
        <div style={{ background:`linear-gradient(180deg, ${B.surface} 0%, ${B.bg} 100%)`, border:`1px solid ${B.border}`, borderRadius:16, padding:24, boxShadow:'0 60px 120px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.04)' }}>
          {/* mini pipeline */}
          <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:22 }}>
            <div style={{ width:10, height:10, borderRadius:'50%', background:B.ok }}/>
            <span style={{ fontFamily:B.mono, fontSize:13.5, color:B.textDim }}>run_20260424_142311 · BudgetBook</span>
            <div style={{ flex:1 }}/>
            <span style={{ fontFamily:B.mono, fontSize:12.5, color:B.ok, display:'inline-flex', gap:6, alignItems:'center' }}>
              <PulseDot color={B.ok} size={6}/> running
            </span>
          </div>

          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:14, marginBottom:20 }}>
            {MOCK.stages.map((s, i) => {
              const active = s.status==='running', done = s.status==='done';
              return (
                <div key={s.id} style={{
                  padding:14, borderRadius:10,
                  background: active ? 'rgba(166,140,247,0.08)' : B.surface2,
                  border:`1px solid ${active ? B.accent : B.border}`,
                  position:'relative', overflow:'hidden',
                }}>
                  <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6 }}>
                    <div style={{ width:18, height:18, borderRadius:'50%', background: done? B.ok : active? B.accent : B.border, display:'flex', alignItems:'center', justifyContent:'center', color: done || active ? '#0b0b10' : B.textSub, fontSize:11.5 }}>
                      {done ? '✓' : active ? '•' : i+1}
                    </div>
                    <span style={{ fontSize:14.5, fontWeight:500 }}>{s.label}</span>
                    {active && <PulseDot color={B.accent} size={5}/>}
                  </div>
                  <div style={{ fontSize:13.5, color:B.textDim }}>{s.title}</div>
                  <div style={{ fontFamily:B.mono, fontSize:11.5, color:B.textSub, marginTop:4 }}>{s.model}</div>
                  {active && (
                    <div style={{ position:'absolute', bottom:0, left:0, right:0, height:2, background:`linear-gradient(90deg, transparent, ${B.accent}, transparent)`, backgroundSize:'200% 100%', animation:'acli-shimmer 2s linear infinite' }}/>
                  )}
                </div>
              );
            })}
          </div>

          {/* log */}
          <div style={{ background:B.bg, border:`1px solid ${B.border}`, borderRadius:8, padding:14, fontFamily:B.mono, fontSize:13.5, lineHeight:1.7 }}>
            {MOCK.logs.slice(-5).map((l, i) => (
              <div key={i} style={{ display:'flex', gap:12, color: l.lvl==='warn'? B.warn : B.textDim }}>
                <span style={{ color:B.textSub, width:52 }}>{l.t}</span>
                <span style={{ color: l.stage==='PM'? B.info : l.stage==='Dev'? B.accent : B.warn, width:40 }}>{l.stage}</span>
                <span style={{ flex:1, color: l.lvl==='warn'? B.warn : B.text }}>{l.msg}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Trust row */}
      <div style={{ padding:'0 56px 80px', display:'flex', gap:28, justifyContent:'center', color:B.textSub, fontSize:13.5, flexWrap:'wrap' }}>
        <span>Codex · ChatGPT subscription</span>
        <span>·</span>
        <span>Claude Code · Anthropic login</span>
        <span>·</span>
        <span>No API keys</span>
        <span>·</span>
        <span>MIT licensed</span>
      </div>
    </div>
  );
}

function B_Dashboard() {
  const r = MOCK.activeRun;
  return (
    <div style={{ width:'100%', height:'100%', background:B.bg, fontFamily:B.sans, color:B.text, display:'grid', gridTemplateColumns:'220px 1fr' }}>
      {/* sidebar */}
      <div style={{ borderRight:`1px solid ${B.border}`, background:B.surface, padding:'18px 14px', display:'flex', flexDirection:'column', gap:4 }}>
        <div style={{ display:'flex', alignItems:'center', gap:10, padding:'4px 8px 18px' }}>
          <div style={{ width:24, height:24, borderRadius:6, background:`linear-gradient(135deg, ${B.accent}, ${B.accent2})`, color:'#fff', display:'flex', alignItems:'center', justifyContent:'center', fontSize:13.5, fontWeight:700 }}>a</div>
          <span style={{ fontWeight:600, fontSize:15.5 }}>AgentCLI</span>
        </div>
        {[
          { i:Icon.term(14),  l:'Overview',   active:true },
          { i:Icon.spark(14), l:'Pipeline' },
          { i:Icon.check(14), l:'Backlog' },
          { i:Icon.clock(14), l:'Runs' },
          { i:Icon.bell(14),  l:'Notifications', badge:3 },
        ].map((it,i) => (
          <div key={i} style={{
            display:'flex', alignItems:'center', gap:10, padding:'7px 10px', borderRadius:7, fontSize:14.5,
            background: it.active ? 'rgba(166,140,247,0.1)' : 'transparent',
            color: it.active ? B.text : B.textDim, cursor:'pointer',
          }}>
            <span style={{ color: it.active ? B.accent : B.textSub }}>{it.i}</span>
            <span style={{ flex:1 }}>{it.l}</span>
            {it.badge && <span style={{ fontSize:11.5, padding:'1px 6px', background:B.accent, color:B.bg, borderRadius:10, fontWeight:600 }}>{it.badge}</span>}
          </div>
        ))}
        <div style={{ marginTop:'auto', padding:10, background:B.surface2, borderRadius:10, fontSize:12.5, color:B.textDim }}>
          <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:6 }}>
            <PulseDot color={B.ok} size={6}/>
            <span style={{ color:B.text, fontSize:13.5, fontWeight:500 }}>home-pc-main</span>
          </div>
          <div>quota 5h · 34%</div>
          <div style={{ height:3, background:B.border, borderRadius:2, marginTop:6, overflow:'hidden' }}>
            <div style={{ width:'34%', height:'100%', background:B.ok }}/>
          </div>
        </div>
      </div>

      {/* main */}
      <div style={{ overflow:'auto' }}>
        {/* top bar */}
        <div style={{ display:'flex', alignItems:'center', padding:'14px 28px', borderBottom:`1px solid ${B.border}`, gap:14 }}>
          <div style={{ display:'flex', alignItems:'center', gap:8, fontSize:14.5, color:B.textDim }}>
            <span>BudgetBook</span>
            <span style={{ color:B.textSub }}>/</span>
            <span style={{ color:B.text }}>Overview</span>
          </div>
          <div style={{ flex:1 }}/>
          <div style={{ display:'flex', gap:6, fontSize:13.5, fontFamily:B.mono, color:B.textDim, padding:'4px 10px', background:B.surface, borderRadius:6, border:`1px solid ${B.border}` }}>
            <span style={{ color:B.ok }}>●</span>
            <span>{r.backend}</span>
          </div>
          <button style={{ padding:'6px 12px', fontSize:13.5, border:`1px solid ${B.borderHi}`, borderRadius:7, background:'transparent', color:B.text, cursor:'pointer', display:'inline-flex', alignItems:'center', gap:6 }}>{Icon.stop()} Stop</button>
          <button style={{ padding:'6px 12px', fontSize:13.5, border:'none', borderRadius:7, background:B.accent, color:B.bg, fontWeight:500, cursor:'pointer', display:'inline-flex', alignItems:'center', gap:6 }}>{Icon.play()} New run</button>
        </div>

        <div style={{ padding:28 }}>
          {/* title */}
          <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:4 }}>
            <h2 style={{ fontSize:28, fontWeight:500, margin:0, letterSpacing:'-0.02em' }}>Run overview</h2>
            <span style={{ fontSize:14.5, color:B.textDim, fontFamily:B.mono }}>#{r.id.slice(-6)}</span>
            <span style={{ fontSize:13.5, padding:'2px 8px', background:'rgba(108,217,168,0.12)', color:B.ok, borderRadius:4, display:'inline-flex', gap:5, alignItems:'center' }}>
              <PulseDot color={B.ok} size={5}/> running · {r.stage}
            </span>
          </div>
          <div style={{ fontSize:14.5, color:B.textDim, marginBottom:22 }}>
            Started {fmtRelative(r.startedAt)} · branch <span style={{color:B.text, fontFamily:B.mono}}>{r.branch}</span> · iteration {r.iteration} of {r.maxIterations}
          </div>

          {/* KPI */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:14, marginBottom:22 }}>
            {[
              { l:'Tasks',    v:'2/5',    sub:'3 pending', data:[1,2,2,3,3,4,5], pos:true },
              { l:'Tokens',   v:'227K',   sub:'↗ 12% vs last', data:MOCK.metrics.tokens24h.slice(-12) },
              { l:'Budget',   v:'$3.27',  sub:'of $8.00', data:MOCK.metrics.budget, accent:B.warn },
              { l:'Duration', v:fmtDuration(r.elapsedSec), sub:'avg 32m' },
            ].map((k,i)=>(
              <div key={i} style={{ background:B.surface, border:`1px solid ${B.border}`, borderRadius:12, padding:16 }}>
                <div style={{ fontSize:13.5, color:B.textDim }}>{k.l}</div>
                <div style={{ fontSize:32, fontWeight:500, marginTop:4, letterSpacing:'-0.015em' }}>{k.v}</div>
                <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginTop:8, fontSize:12.5, color:B.textDim }}>
                  <span>{k.sub}</span>
                  {k.data && <Sparkline data={k.data} width={56} height={20} stroke={k.accent || B.accent} strokeWidth={1.5}/>}
                </div>
              </div>
            ))}
          </div>

          {/* pipeline + goals */}
          <div style={{ display:'grid', gridTemplateColumns:'1.5fr 1fr', gap:14, marginBottom:22 }}>
            <div style={{ background:B.surface, border:`1px solid ${B.border}`, borderRadius:12, padding:18 }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:18 }}>
                <div style={{ fontSize:15.5, fontWeight:500 }}>Pipeline</div>
                <div style={{ fontSize:13.5, color:B.textDim }}>iter 3 of 5</div>
              </div>
              {/* horizontal pipeline with bezier */}
              <svg viewBox="0 0 560 100" style={{ width:'100%', height:100 }}>
                <defs>
                  <linearGradient id="b-flow" x1="0" x2="1">
                    <stop offset="0" stopColor={B.ok}/>
                    <stop offset="0.5" stopColor={B.accent}/>
                    <stop offset="1" stopColor={B.border}/>
                  </linearGradient>
                </defs>
                <path d="M 60 50 C 160 50 160 50 280 50 C 400 50 400 50 500 50" stroke="url(#b-flow)" strokeWidth="2" fill="none"/>
                <circle cx="280" cy="50" r="14" fill="none" stroke={B.accent} strokeWidth="2" strokeDasharray="4 3" style={{ animation:'acli-flow 1s linear infinite' }}/>
                {[
                  { x:60, label:'PM',  color:B.ok,     sub:'5 tasks · 5m' },
                  { x:280, label:'Dev', color:B.accent, sub:'T-018 · 30m' },
                  { x:500, label:'QA',  color:B.textSub, sub:'pending' },
                ].map((n,i) => (
                  <g key={i}>
                    <circle cx={n.x} cy={50} r={i===1?10:8} fill={n.color}/>
                    <text x={n.x} y={20} fill={B.text} fontSize="13" textAnchor="middle" fontWeight="500">{n.label}</text>
                    <text x={n.x} y={82} fill={B.textDim} fontSize="10.5" textAnchor="middle" fontFamily={B.mono}>{n.sub}</text>
                  </g>
                ))}
              </svg>
              {/* current task */}
              <div style={{ marginTop:8, padding:12, background:B.surface2, border:`1px solid ${B.border}`, borderRadius:8 }}>
                <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:6 }}>
                  <PulseDot color={B.accent} size={6}/>
                  <span style={{ fontFamily:B.mono, fontSize:12.5, color:B.textDim }}>T-018</span>
                  <span style={{ fontSize:14.5 }}>Persist manual category overrides to SQLite</span>
                </div>
                <div style={{ height:4, background:B.border, borderRadius:2, overflow:'hidden' }}>
                  <div style={{ width:'70%', height:'100%', background:`linear-gradient(90deg, ${B.accent}, ${B.accent2})`, backgroundSize:'200% 100%', animation:'acli-shimmer 1.8s linear infinite' }}/>
                </div>
              </div>
            </div>

            <div style={{ background:B.surface, border:`1px solid ${B.border}`, borderRadius:12, padding:18 }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14 }}>
                <div style={{ fontSize:15.5, fontWeight:500 }}>Goals</div>
                <div style={{ fontSize:12.5, padding:'2px 8px', background:B.surface2, borderRadius:999, color:B.textDim }}>2/4 P0</div>
              </div>
              {MOCK.goals.p0.map((g,i) => (
                <div key={i} style={{ display:'flex', gap:10, alignItems:'flex-start', padding:'8px 0', borderTop: i ? `1px solid ${B.border}` : 'none' }}>
                  <div style={{ width:16, height:16, borderRadius:4, border:`1.5px solid ${g.done ? B.ok : B.borderHi}`, background: g.done ? B.ok : 'transparent', display:'flex', alignItems:'center', justifyContent:'center', color:'#0b0b10', fontSize:11.5, marginTop:1 }}>
                    {g.done && '✓'}
                  </div>
                  <div style={{ flex:1, fontSize:14.5, lineHeight:1.4, color: g.done ? B.textDim : B.text, textDecoration: g.done ? 'line-through' : 'none' }}>{g.text}</div>
                </div>
              ))}
            </div>
          </div>

          {/* logs */}
          <div style={{ background:B.surface, border:`1px solid ${B.border}`, borderRadius:12, overflow:'hidden' }}>
            <div style={{ display:'flex', alignItems:'center', padding:'10px 16px', borderBottom:`1px solid ${B.border}` }}>
              <span style={{ fontSize:14.5, fontWeight:500 }}>Live logs</span>
              <span style={{ marginLeft:10, fontSize:12.5, color:B.textSub, fontFamily:B.mono }}>cycle_summary.log</span>
              <div style={{ flex:1 }}/>
              <span style={{ display:'inline-flex', gap:6, alignItems:'center', fontSize:12.5, color:B.ok }}>
                <PulseDot color={B.ok} size={5}/> live
              </span>
            </div>
            <div style={{ padding:'12px 16px', fontFamily:B.mono, fontSize:13.5, lineHeight:1.75, maxHeight:180, overflow:'auto' }}>
              {MOCK.logs.slice(-8).map((l, i) => (
                <div key={i} style={{ display:'flex', gap:14, color: l.lvl==='warn'? B.warn : B.textDim }}>
                  <span style={{ color:B.textSub, width:52 }}>{l.t}</span>
                  <span style={{ width:50, color: l.stage==='PM'? B.info : l.stage==='Dev'? B.accent : B.warn }}>{l.stage}</span>
                  <span style={{ color: l.lvl==='warn'? B.warn : B.text }}>{l.msg}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function B_Pipeline() {
  // vertical "timeline" deep view
  const events = [
    { t:'14:23', stage:'PM',  type:'start', title:'PM stage started', meta:'opus-4.5' },
    { t:'14:24', stage:'PM',  type:'emit',  title:'Emitted 5 tasks', meta:'schema v2 OK · BACKLOG.json written' },
    { t:'14:24', stage:'Dev', type:'start', title:'Dev stage · T-017', meta:'sonnet-4.5' },
    { t:'14:31', stage:'Dev', type:'edit',  title:'src/category/rules.py', meta:'+148 −22' },
    { t:'14:36', stage:'Dev', type:'done',  title:'T-017 complete', meta:'moving to T-018' },
    { t:'14:44', stage:'Dev', type:'warn',  title:'build: 2 type warnings', meta:'non-blocking' },
    { t:'15:02', stage:'Dev', type:'ckpt',  title:'Checkpoint c14b3ee', meta:'T-018 · 70%' },
    { t:'now',   stage:'Dev', type:'live',  title:'Working on override persistence…', meta:'' },
  ];
  return (
    <div style={{ width:'100%', height:'100%', background:B.bg, fontFamily:B.sans, color:B.text, padding:28, overflow:'auto' }}>
      <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:20 }}>
        <h2 style={{ fontSize:28, fontWeight:500, margin:0, letterSpacing:'-0.02em' }}>Pipeline timeline</h2>
        <span style={{ fontSize:14.5, color:B.textDim, fontFamily:B.mono }}>#{MOCK.activeRun.id.slice(-6)}</span>
      </div>

      {/* stage chips */}
      <div style={{ display:'flex', gap:10, marginBottom:22 }}>
        {MOCK.stages.map(s => {
          const active = s.status==='running', done = s.status==='done';
          return (
            <div key={s.id} style={{
              flex:1, padding:14, borderRadius:10,
              background: active ? 'rgba(166,140,247,0.1)' : B.surface,
              border:`1px solid ${active ? B.accent : B.border}`,
            }}>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ width:8, height:8, borderRadius:'50%', background: done? B.ok : active? B.accent : B.textSub }}/>
                <span style={{ fontSize:15.5, fontWeight:500 }}>{s.label}</span>
                {active && <PulseDot color={B.accent} size={5}/>}
                <span style={{ marginLeft:'auto', fontSize:12.5, color:B.textDim, fontFamily:B.mono }}>{s.model}</span>
              </div>
              <div style={{ fontSize:13.5, color:B.textDim, marginTop:4 }}>{s.title} · {done? fmtDuration(s.durationSec) : active? 'in progress' : 'queued'}</div>
            </div>
          );
        })}
      </div>

      {/* timeline */}
      <div style={{ background:B.surface, border:`1px solid ${B.border}`, borderRadius:12, padding:20 }}>
        <div style={{ position:'relative', paddingLeft:28 }}>
          <div style={{ position:'absolute', left:10, top:0, bottom:0, width:1, background:B.border }}/>
          {events.map((e,i) => {
            const color = e.type==='done'? B.ok : e.type==='warn'? B.warn : e.type==='live'? B.accent : e.stage==='PM'? B.info : B.accent;
            return (
              <div key={i} style={{ position:'relative', paddingBottom:18, display:'flex', gap:16 }}>
                <div style={{ position:'absolute', left:-22, top:3, width:12, height:12, borderRadius:'50%', background: e.type==='live'? 'transparent' : color, border: e.type==='live'? `2px solid ${color}` : 'none', boxShadow:`0 0 0 3px ${B.surface}` }}>
                  {e.type==='live' && <div style={{ position:'absolute', inset:1, borderRadius:'50%', background: color, animation:'acli-pulse 1.6s ease-out infinite' }}/>}
                </div>
                <div style={{ fontFamily:B.mono, fontSize:12.5, color:B.textSub, width:48, marginTop:2 }}>{e.t}</div>
                <div style={{ flex:1 }}>
                  <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                    <span style={{ fontSize:11.5, padding:'1px 7px', background:B.surface2, border:`1px solid ${B.border}`, borderRadius:10, color:B.textDim, fontFamily:B.mono }}>{e.stage}</span>
                    <span style={{ fontSize:15, color:B.text, fontWeight: e.type==='live'? 500 : 400 }}>{e.title}</span>
                    {e.type==='live' && <span style={{ fontSize:12.5, color:B.accent, fontFamily:B.mono, display:'inline-flex', gap:4, alignItems:'center' }}><PulseDot color={B.accent} size={4}/>live</span>}
                  </div>
                  {e.meta && <div style={{ fontSize:13.5, color:B.textDim, marginTop:3, fontFamily: e.type==='edit'||e.type==='ckpt'?B.mono:B.sans }}>{e.meta}</div>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function B_RunDetail() {
  // Runs list + details
  return (
    <div style={{ width:'100%', height:'100%', background:B.bg, fontFamily:B.sans, color:B.text, display:'grid', gridTemplateColumns:'300px 1fr' }}>
      <div style={{ borderRight:`1px solid ${B.border}`, overflow:'auto' }}>
        <div style={{ padding:'18px 18px 10px' }}>
          <div style={{ fontSize:20, fontWeight:500, letterSpacing:'-0.02em' }}>Runs</div>
          <div style={{ fontSize:13.5, color:B.textDim, marginTop:2 }}>7 runs · last 3 days</div>
        </div>
        {MOCK.runs.map((r,i) => {
          const color = r.status==='success'? B.ok : r.status==='failed'? B.err : r.status==='stopped'? B.warn : B.accent;
          return (
            <div key={r.id} style={{
              padding:'12px 18px', borderTop:`1px solid ${B.border}`, cursor:'pointer',
              background: i===0 ? 'rgba(166,140,247,0.07)' : 'transparent',
              borderLeft: i===0 ? `2px solid ${B.accent}` : '2px solid transparent',
            }}>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                {r.status==='running' ? <PulseDot color={color} size={6}/> : <span style={{ width:6, height:6, borderRadius:'50%', background:color }}/>}
                <span style={{ fontFamily:B.mono, fontSize:13.5 }}>{r.id.slice(4, 17)}</span>
                <span style={{ marginLeft:'auto', fontSize:12.5, color:B.textSub }}>{fmtRelative(r.startedAt)}</span>
              </div>
              <div style={{ fontSize:14, color:B.textDim, marginTop:4, fontFamily:B.mono }}>{r.branch}</div>
              <div style={{ display:'flex', gap:10, fontSize:12.5, color:B.textSub, marginTop:4 }}>
                <span>{r.tasksDone}/{r.tasksTotal} tasks</span>
                <span>·</span>
                <span>{r.durationSec ? fmtDuration(r.durationSec) : 'running'}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ overflow:'auto', padding:28 }}>
        <div style={{ display:'flex', alignItems:'baseline', gap:12, marginBottom:6 }}>
          <h2 style={{ fontSize:28, margin:0, fontWeight:500, letterSpacing:'-0.02em' }}>Backlog</h2>
          <span style={{ fontSize:14.5, color:B.textDim, fontFamily:B.mono }}>8 tasks · 2 done · 2 in flight</span>
        </div>
        <div style={{ fontSize:14.5, color:B.textDim, marginBottom:20 }}>{MOCK.activeRun.id} · feat/category-rules</div>

        {/* compact table */}
        <div style={{ background:B.surface, border:`1px solid ${B.border}`, borderRadius:12, overflow:'hidden' }}>
          <div style={{ display:'grid', gridTemplateColumns:'80px 1fr 90px 80px 120px 80px', padding:'10px 14px', fontSize:12.5, color:B.textSub, letterSpacing:'0.08em', textTransform:'uppercase', borderBottom:`1px solid ${B.border}` }}>
            <span>ID</span><span>Title</span><span>Priority</span><span>Status</span><span>Tags</span><span style={{textAlign:'right'}}>Size</span>
          </div>
          {MOCK.backlog.map(t => {
            const sc = t.status==='done'? B.ok : t.status==='in_progress'? B.accent : t.status==='failed'? B.err : B.textDim;
            return (
              <div key={t.id} style={{ display:'grid', gridTemplateColumns:'80px 1fr 90px 80px 120px 80px', padding:'12px 14px', borderTop:`1px solid ${B.border}`, alignItems:'center', fontSize:14.5 }}>
                <span style={{ fontFamily:B.mono, fontSize:13.5, color:B.textDim }}>{t.id}</span>
                <span style={{ color: t.status==='done'? B.textDim : B.text }}>{t.title}</span>
                <span>
                  <span style={{ fontSize:11.5, padding:'2px 7px', borderRadius:4, fontFamily:B.mono, fontWeight:600, color: t.priority==='P0'? B.err : B.warn, background: t.priority==='P0'? 'rgba(244,122,128,0.1)' : 'rgba(241,184,114,0.1)' }}>{t.priority}</span>
                </span>
                <span style={{ display:'flex', alignItems:'center', gap:6, fontSize:13.5, color:sc }}>
                  {t.status==='in_progress' ? <PulseDot color={sc} size={5}/> : <span style={{ width:5, height:5, borderRadius:'50%', background:sc }}/>}
                  {t.status.replace('_',' ')}
                </span>
                <span style={{ display:'flex', gap:4, flexWrap:'wrap' }}>
                  {t.tags.slice(0,2).map(tag => <span key={tag} style={{ fontSize:11.5, padding:'2px 6px', border:`1px solid ${B.border}`, borderRadius:4, color:B.textDim }}>{tag}</span>)}
                </span>
                <span style={{ textAlign:'right', fontFamily:B.mono, fontSize:12.5, color:B.textSub }}>{t.estimate}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function B_Mobile() {
  return (
    <div style={{ width:'100%', height:'100%', background:B.bg, fontFamily:B.sans, color:B.text, display:'flex', flexDirection:'column' }}>
      <div style={{ display:'flex', justifyContent:'space-between', padding:'10px 20px 4px', fontSize:13.5, color:B.text, fontWeight:500 }}>
        <span>22:47</span>
        <span>●●●</span>
      </div>
      <div style={{ padding:'10px 20px 18px' }}>
        <div style={{ fontSize:13.5, color:B.textDim, marginBottom:4 }}>home-pc-main</div>
        <div style={{ display:'flex', alignItems:'baseline', gap:10 }}>
          <h1 style={{ margin:0, fontSize:30, letterSpacing:'-0.025em', fontWeight:500 }}>BudgetBook</h1>
          <span style={{ fontSize:12.5, padding:'2px 7px', background:'rgba(108,217,168,0.15)', color:B.ok, borderRadius:999, display:'inline-flex', gap:5, alignItems:'center' }}>
            <PulseDot color={B.ok} size={5}/>live
          </span>
        </div>
        <div style={{ fontSize:13.5, color:B.textSub, marginTop:6, fontFamily:B.mono }}>run_…142311 · 47m</div>
      </div>

      {/* pipeline pill */}
      <div style={{ margin:'0 16px 16px', padding:14, background:B.surface, border:`1px solid ${B.border}`, borderRadius:14 }}>
        <div style={{ display:'flex', justifyContent:'space-between', marginBottom:12, fontSize:13.5 }}>
          <span style={{ color:B.textDim }}>Pipeline</span>
          <span style={{ color:B.accent, fontFamily:B.mono }}>iter 3/5</span>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:6 }}>
          {MOCK.stages.map((s,i) => {
            const done = s.status==='done', active = s.status==='running';
            const color = done? B.ok : active? B.accent : B.border;
            return (
              <React.Fragment key={s.id}>
                <div style={{ flex:'0 0 auto', display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
                  <div style={{ width:26, height:26, borderRadius:'50%', background: done||active? color : 'transparent', border:`2px solid ${color}`, display:'flex', alignItems:'center', justifyContent:'center', color: done||active? '#0b0b10' : B.textSub, fontSize:12.5, fontWeight:600 }}>
                    {done ? '✓' : active ? <PulseDot color="#0b0b10" size={6}/> : i+1}
                  </div>
                  <span style={{ fontSize:12, color: done||active? B.text : B.textDim, fontFamily:B.mono }}>{s.label}</span>
                </div>
                {i < MOCK.stages.length-1 && (
                  <div style={{ flex:1, height:2, background: done? B.ok : B.border, borderRadius:2, marginTop:-14, position:'relative', overflow:'hidden' }}>
                    {MOCK.stages[i+1].status==='running' && <div style={{ position:'absolute', inset:0, background:`linear-gradient(90deg, transparent, ${B.accent}, transparent)`, backgroundSize:'200% 100%', animation:'acli-shimmer 1.6s linear infinite' }}/>}
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </div>
        <div style={{ marginTop:16, padding:'10px 12px', background:B.surface2, borderRadius:8 }}>
          <div style={{ fontSize:12.5, color:B.textSub, fontFamily:B.mono }}>T-018 · now</div>
          <div style={{ fontSize:14.5, marginTop:2, lineHeight:1.35 }}>Persist manual category overrides to SQLite</div>
        </div>
      </div>

      {/* notifications */}
      <div style={{ padding:'0 16px', flex:1, overflow:'auto' }}>
        <div style={{ fontSize:13.5, color:B.textDim, marginBottom:10, padding:'0 6px' }}>Activity</div>
        {MOCK.notifications.slice(0,5).map((n,i) => {
          const color = n.kind==='task_done'? B.ok : n.kind==='error' || n.kind==='task_failed'? B.err : n.kind==='quota'? B.warn : B.accent;
          return (
            <div key={i} style={{ display:'flex', gap:12, padding:'12px', marginBottom:6, background:B.surface, borderRadius:10, border:`1px solid ${B.border}` }}>
              <div style={{ width:8, height:8, borderRadius:'50%', background:color, marginTop:6, flexShrink:0 }}/>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:14.5, lineHeight:1.4 }}>{n.text}</div>
                <div style={{ fontSize:12.5, color:B.textSub, marginTop:3, fontFamily:B.mono }}>{n.kind} · {fmtRelative(n.t)}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* tab bar */}
      <div style={{ display:'flex', padding:'10px 8px 14px', borderTop:`1px solid ${B.border}`, background:B.surface }}>
        {[
          { i:Icon.term(16),  l:'Run', active:true },
          { i:Icon.spark(16), l:'Pipeline' },
          { i:Icon.bell(16),  l:'Alerts' },
          { i:Icon.clock(16), l:'History' },
        ].map((t,i) => (
          <div key={i} style={{ flex:1, textAlign:'center', color: t.active ? B.accent : B.textDim, fontSize:12.5 }}>
            <div>{t.i}</div>
            <div style={{ marginTop:3 }}>{t.l}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { B_Landing, B_Dashboard, B_Pipeline, B_RunDetail, B_Mobile });
