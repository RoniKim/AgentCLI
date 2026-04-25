// Direction C: Ops Console
// Data-dense monitoring feel, amber accent with red/green status,
// like a NOC / trading desk for agents. Mixed mono + sans. Uses more chart.

const C = {
  bg: '#0d0f12',
  surface: '#14171c',
  surface2: '#1b1f26',
  border: '#232832',
  borderHi: '#2f3544',
  text: '#e4e7ee',
  textDim: '#8891a0',
  textSub: '#555d6b',
  accent: '#f5b343',       // amber
  ok: '#4ccd8d',
  warn: '#f5b343',
  err: '#e56161',
  info: '#6ab0f3',
  sans: 'Inter, -apple-system, system-ui, sans-serif',
  mono: '"JetBrains Mono", ui-monospace, Menlo, monospace',
};

function C_Landing() {
  return (
    <div style={{ width:'100%', height:'100%', background:C.bg, fontFamily:C.sans, color:C.text, position:'relative', overflow:'hidden' }}>
      {/* scanline grid */}
      <div style={{ position:'absolute', inset:0, backgroundImage:
        `repeating-linear-gradient(0deg, rgba(245,179,67,0.025) 0 1px, transparent 1px 3px)`,
        pointerEvents:'none' }}/>

      {/* top strip like a status bar */}
      <div style={{ position:'relative', display:'flex', alignItems:'center', gap:20, padding:'10px 24px', borderBottom:`1px solid ${C.border}`, background:C.surface, fontFamily:C.mono, fontSize:12.5, color:C.textDim }}>
        <span style={{ color:C.accent, fontWeight:600 }}>◆ AGENTCLI</span>
        <span style={{ color:C.textSub }}>/</span>
        <span>v0.8.2</span>
        <span style={{ color:C.textSub }}>//</span>
        <span style={{ display:'inline-flex', alignItems:'center', gap:6 }}><PulseDot color={C.ok} size={5}/>1 runner live</span>
        <span style={{ color:C.textSub }}>//</span>
        <span>3 backends ready</span>
        <span style={{ marginLeft:'auto' }}>{new Date().toISOString().slice(0,19)}Z</span>
      </div>

      {/* hero split */}
      <div style={{ position:'relative', display:'grid', gridTemplateColumns:'1fr 1fr', gap:0 }}>
        <div style={{ padding:'80px 56px', borderRight:`1px solid ${C.border}` }}>
          <div style={{ fontFamily:C.mono, fontSize:12.5, color:C.accent, letterSpacing:'0.14em', marginBottom:18 }}>
            [OPS CONSOLE · OPEN SOURCE]
          </div>
          <h1 style={{ fontSize:60, fontWeight:600, letterSpacing:'-0.035em', lineHeight:1.02, margin:0 }}>
            Mission control<br/>
            for your<br/>
            <span style={{ color:C.accent }}>coding agents.</span>
          </h1>
          <p style={{ fontSize:17.5, color:C.textDim, maxWidth:480, marginTop:24, lineHeight:1.55 }}>
            AgentCLI loops <strong style={{color:C.text}}>PM → Dev → QA</strong> against your repo.
            The web console gives you NOC-style monitoring: live logs, token burn, budget, quota, stalled-run alerts.
          </p>
          <div style={{ display:'flex', gap:10, marginTop:32 }}>
            <button style={{ background:C.accent, color:'#0d0f12', border:'none', padding:'11px 18px', fontSize:14.5, fontWeight:600, cursor:'pointer', fontFamily:C.mono }}>▶ LAUNCH CONSOLE</button>
            <button style={{ background:'transparent', color:C.text, border:`1px solid ${C.borderHi}`, padding:'11px 18px', fontSize:14.5, cursor:'pointer', fontFamily:C.mono }}>VIEW ON GITHUB ↗</button>
          </div>
        </div>

        {/* stats panel */}
        <div style={{ padding:'80px 56px', background:`radial-gradient(ellipse at top right, rgba(245,179,67,0.06), transparent 70%)` }}>
          <div style={{ fontFamily:C.mono, fontSize:12.5, color:C.textSub, letterSpacing:'0.14em', marginBottom:18 }}>// LIVE</div>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:14 }}>
            {[
              { l:'RUN.STATE',  v:'RUNNING', sub:'stage=Dev · iter 3/5', color:C.ok },
              { l:'TOKENS.IN',  v:'184K',    sub:'+2.1K / min',          data:MOCK.metrics.tokens24h.slice(-12) },
              { l:'BUDGET',     v:'$3.27',   sub:'/ $8.00 · 41%',        bar:0.41 },
              { l:'QUOTA.5H',   v:'34%',     sub:'reset in 3h 12m',      bar:0.34 },
            ].map((k, i) => (
              <div key={i} style={{ background:C.surface, border:`1px solid ${C.border}`, padding:14 }}>
                <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.1em' }}>{k.l}</div>
                <div style={{ fontFamily:C.mono, fontSize:28, fontWeight:500, color: k.color || C.text, marginTop:6 }}>{k.v}</div>
                <div style={{ fontSize:12.5, color:C.textDim, marginTop:4 }}>{k.sub}</div>
                {k.data && <div style={{ marginTop:8 }}><Sparkline data={k.data} width={160} height={24} stroke={C.accent}/></div>}
                {k.bar != null && (
                  <div style={{ marginTop:8, height:3, background:C.border }}>
                    <div style={{ width:(k.bar*100)+'%', height:'100%', background: k.bar>0.7? C.err : k.bar>0.4? C.warn : C.ok }}/>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div style={{ marginTop:18, padding:12, border:`1px solid ${C.border}`, background:C.surface, fontFamily:C.mono, fontSize:12.5 }}>
            <div style={{ color:C.textSub, marginBottom:6 }}>// RECENT EVENTS</div>
            {MOCK.notifications.slice(0,3).map((n,i) => {
              const color = n.kind==='task_done'? C.ok : n.kind==='error'? C.err : n.kind==='quota'? C.warn : C.info;
              return (
                <div key={i} style={{ display:'flex', gap:10, padding:'3px 0' }}>
                  <span style={{ color }}>{'▸'}</span>
                  <span style={{ color:C.textDim, width:60 }}>{fmtRelative(n.t)}</span>
                  <span style={{ color:C.text, flex:1 }}>{n.text}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* bottom feature strip */}
      <div style={{ position:'relative', borderTop:`1px solid ${C.border}`, padding:'24px 56px', display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:32, fontSize:14.5, background:C.surface }}>
        {[
          { k:'MONITOR',  v:'Real-time logs, metrics, stage flow' },
          { k:'CONTROL',  v:'Start / stop / restart from browser' },
          { k:'REMOTE',   v:'Telegram mobile parity' },
          { k:'SAFE',     v:'Budget caps · quota pre-check · worktree isolation' },
        ].map((f,i) => (
          <div key={i}>
            <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.accent, letterSpacing:'0.14em', marginBottom:6 }}>{f.k}</div>
            <div style={{ color:C.textDim, lineHeight:1.45 }}>{f.v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function C_Dashboard() {
  const r = MOCK.activeRun;
  return (
    <div style={{ width:'100%', height:'100%', background:C.bg, fontFamily:C.sans, color:C.text, display:'grid', gridTemplateRows:'36px 1fr' }}>
      {/* top status bar */}
      <div style={{ display:'flex', alignItems:'center', gap:14, padding:'0 16px', background:C.surface, borderBottom:`1px solid ${C.border}`, fontFamily:C.mono, fontSize:12.5 }}>
        <span style={{ color:C.accent, fontWeight:700 }}>◆ AGENTCLI</span>
        <span style={{ color:C.textSub }}>home-pc-main</span>
        <span style={{ color:C.textSub }}>/</span>
        <span>BudgetBook</span>
        <span style={{ color:C.textSub }}>::</span>
        <span>{r.id}</span>
        <div style={{ flex:1 }}/>
        <span style={{ color:C.ok, display:'inline-flex', gap:6, alignItems:'center' }}><PulseDot color={C.ok} size={5}/>LIVE</span>
        <span style={{ color:C.textSub }}>|</span>
        <span style={{ color:C.warn }}>BUDGET 41%</span>
        <span style={{ color:C.textSub }}>|</span>
        <span style={{ color:C.textDim }}>QUOTA 34%</span>
        <span style={{ color:C.textSub }}>|</span>
        <span style={{ color:C.textDim }}>{new Date().toISOString().slice(11,19)}Z</span>
      </div>

      {/* grid layout: 3 rows x 3 cols */}
      <div style={{
        display:'grid',
        gridTemplateColumns:'1.4fr 1fr 1fr',
        gridTemplateRows:'auto auto 1fr',
        gap:1, background:C.border, padding:1,
      }}>
        {/* pipeline panel - spans 2 cols */}
        <div style={{ gridColumn:'1 / 3', background:C.bg, padding:16 }}>
          <PanelHeader label="PIPELINE" sub="PM→Dev→QA · iter 3/5" dot={C.warn}/>
          <div style={{ display:'flex', gap:10, marginTop:14 }}>
            {MOCK.stages.map((s,i) => {
              const color = s.status==='done'? C.ok : s.status==='running'? C.accent : C.textSub;
              return (
                <div key={s.id} style={{ flex:1, position:'relative' }}>
                  <div style={{ border:`1px solid ${color}`, background: s.status==='running'? 'rgba(245,179,67,0.08)':'transparent', padding:10 }}>
                    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:8 }}>
                      <span style={{ fontFamily:C.mono, fontSize:13.5, color, fontWeight:600 }}>{s.label}</span>
                      <span style={{ fontFamily:C.mono, fontSize:11.5, color, letterSpacing:'0.1em' }}>{s.status.toUpperCase()}</span>
                    </div>
                    <div style={{ fontSize:14.5, color:C.text, marginBottom:4 }}>{s.title}</div>
                    <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textDim, display:'flex', justifyContent:'space-between' }}>
                      <span>{s.model}</span>
                      <span>{s.status==='running'? 'live' : fmtDuration(s.durationSec)}</span>
                    </div>
                    {s.status==='running' && (
                      <div style={{ position:'absolute', bottom:0, left:0, right:0, height:2, background:C.border, overflow:'hidden' }}>
                        <div style={{ width:'62%', height:'100%', background:C.accent, boxShadow:`0 0 8px ${C.accent}` }}/>
                      </div>
                    )}
                  </div>
                  {i < 2 && (
                    <svg style={{ position:'absolute', right:-15, top:'50%', transform:'translateY(-50%)', zIndex:2 }} width="16" height="10" viewBox="0 0 16 10">
                      <path d="M0 5 L14 5 M10 1 L14 5 L10 9" stroke={s.status==='done'? C.ok : C.border} strokeWidth="1.3" fill="none"/>
                    </svg>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* alerts column - spans 2 rows */}
        <div style={{ gridRow:'1 / 3', background:C.bg, padding:16 }}>
          <PanelHeader label="ALERTS" sub="last 24h" dot={C.err}/>
          <div style={{ marginTop:12 }}>
            {MOCK.notifications.slice(0,6).map((n,i) => {
              const color = n.kind==='task_done'? C.ok : n.kind==='error' || n.kind==='task_failed'? C.err : n.kind==='quota'? C.warn : C.info;
              return (
                <div key={i} style={{ padding:'8px 0', borderTop: i ? `1px solid ${C.border}` : 'none' }}>
                  <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                    <span style={{ width:4, height:4, background:color }}/>
                    <span style={{ fontFamily:C.mono, fontSize:11.5, color, letterSpacing:'0.1em' }}>{n.kind.replace('_','.').toUpperCase()}</span>
                    <span style={{ marginLeft:'auto', fontFamily:C.mono, fontSize:11.5, color:C.textSub }}>{fmtRelative(n.t)}</span>
                  </div>
                  <div style={{ fontSize:14, color:C.text, marginTop:3, lineHeight:1.4 }}>{n.text}</div>
                </div>
              );
            })}
          </div>
        </div>

        {/* charts row */}
        <div style={{ background:C.bg, padding:16 }}>
          <PanelHeader label="TOKENS · 24H" sub="3.2M · ↗ 12%"/>
          <div style={{ marginTop:10 }}>
            <Sparkline data={MOCK.metrics.tokens24h} width={360} height={70} stroke={C.accent} fill="rgba(245,179,67,0.08)" strokeWidth={1.5}/>
          </div>
          <div style={{ display:'flex', justifyContent:'space-between', fontFamily:C.mono, fontSize:11.5, color:C.textSub, marginTop:4 }}>
            <span>-24h</span><span>-12h</span><span>now</span>
          </div>
        </div>
        <div style={{ background:C.bg, padding:16 }}>
          <PanelHeader label="BUDGET BURN" sub="$3.27 / $8.00"/>
          <div style={{ display:'flex', gap:2, marginTop:16, height:44 }}>
            {Array.from({length:20}).map((_,i) => (
              <div key={i} style={{ flex:1, background: i<8 ? C.ok : i<10 ? C.warn : C.border }}/>
            ))}
          </div>
          <div style={{ display:'flex', justifyContent:'space-between', fontFamily:C.mono, fontSize:11.5, color:C.textSub, marginTop:6 }}>
            <span>iter 1</span>
            <span style={{ color:C.accent }}>iter 3 · NOW</span>
            <span>cap 5</span>
          </div>
        </div>

        {/* backlog mini + goals - 2 cols */}
        <div style={{ background:C.bg, padding:16 }}>
          <PanelHeader label="BACKLOG" sub="8 tasks"/>
          <div style={{ marginTop:10, fontFamily:C.mono, fontSize:12.5 }}>
            {MOCK.backlog.slice(0,5).map((t,i) => {
              const color = t.status==='done'? C.ok : t.status==='in_progress'? C.accent : t.status==='failed'? C.err : C.textDim;
              return (
                <div key={t.id} style={{ display:'flex', gap:10, padding:'5px 0', borderTop: i? `1px dotted ${C.border}`:'none' }}>
                  <span style={{ color, width:10 }}>■</span>
                  <span style={{ color:C.textDim, width:50 }}>{t.id}</span>
                  <span style={{ color:C.text, flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{t.title}</span>
                  <span style={{ color: t.priority==='P0'? C.err : C.warn, fontSize:11.5 }}>{t.priority}</span>
                </div>
              );
            })}
          </div>
        </div>
        <div style={{ background:C.bg, padding:16 }}>
          <PanelHeader label="GOALS · P0" sub="2/4 done"/>
          <div style={{ marginTop:10 }}>
            {MOCK.goals.p0.map((g,i) => (
              <div key={i} style={{ display:'flex', gap:10, padding:'5px 0', fontSize:13.5, fontFamily:C.mono, color: g.done? C.textDim : C.text }}>
                <span style={{ color: g.done? C.ok : C.textSub, width:14 }}>{g.done? '[✓]':'[ ]'}</span>
                <span style={{ textDecoration: g.done? 'line-through':'none' }}>{g.text}</span>
              </div>
            ))}
          </div>
        </div>

        {/* logs spanning all 3 cols */}
        <div style={{ gridColumn:'1 / -1', background:C.bg, padding:16 }}>
          <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:10 }}>
            <PanelHeader label="LIVE LOG" sub="cycle_summary.log · tail"/>
            <div style={{ flex:1 }}/>
            <div style={{ display:'flex', gap:8, fontFamily:C.mono, fontSize:11.5 }}>
              {['ALL','INFO','WARN','ERR'].map((f,i) => (
                <span key={f} style={{ padding:'3px 8px', border:`1px solid ${i===0?C.accent:C.border}`, color: i===0?C.accent:C.textDim, letterSpacing:'0.1em' }}>{f}</span>
              ))}
            </div>
          </div>
          <div style={{ background:'#080a0d', border:`1px solid ${C.border}`, padding:'10px 14px', fontFamily:C.mono, fontSize:13.5, lineHeight:1.7, maxHeight:180, overflow:'auto' }}>
            {MOCK.logs.slice(-9).map((l, i) => (
              <div key={i} style={{ display:'flex', gap:12 }}>
                <span style={{ color:C.textSub, width:60 }}>{l.t}</span>
                <span style={{ width:44, color: l.lvl==='warn'? C.warn : l.lvl==='err'? C.err : l.lvl==='debug'? C.textSub : C.ok }}>{l.lvl.toUpperCase()}</span>
                <span style={{ width:44, color: l.stage==='PM'? C.info : l.stage==='Dev'? C.accent : l.stage==='QA'? C.warn : C.textDim }}>[{l.stage}]</span>
                <span style={{ color: l.lvl==='warn'? C.warn : C.text, flex:1 }}>{l.msg}</span>
              </div>
            ))}
            <div style={{ display:'flex', gap:12, color:C.accent }}>
              <span style={{ color:C.textSub, width:60 }}>15:10:02</span>
              <span style={{ animation:'acli-cursor 1s step-end infinite' }}>▍</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PanelHeader({ label, sub, dot }) {
  return (
    <div style={{ display:'flex', alignItems:'center', gap:8, fontFamily:C.mono, fontSize:12, letterSpacing:'0.14em', color:C.textDim }}>
      {dot && <PulseDot color={dot} size={5}/>}
      <span style={{ color:C.accent, fontWeight:600 }}>// {label}</span>
      {sub && <span style={{ color:C.textSub }}>— {sub}</span>}
    </div>
  );
}

function C_RunHistory() {
  return (
    <div style={{ width:'100%', height:'100%', background:C.bg, fontFamily:C.sans, color:C.text, overflow:'auto' }}>
      <div style={{ padding:'20px 24px', borderBottom:`1px solid ${C.border}` }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.accent, letterSpacing:'0.14em' }}>// RUN HISTORY</div>
        <div style={{ display:'flex', alignItems:'baseline', gap:12, marginTop:6 }}>
          <h2 style={{ fontSize:28, margin:0, fontWeight:600, letterSpacing:'-0.02em' }}>7 runs</h2>
          <span style={{ color:C.textDim, fontSize:14.5 }}>last 3 days · 71% success · avg 29m</span>
        </div>
      </div>

      {/* metric strip */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:1, background:C.border, borderBottom:`1px solid ${C.border}` }}>
        {[
          { l:'SUCCESS RATE', v:'5/7', sub:'71%', color:C.ok },
          { l:'AVG DURATION', v:'29m',  sub:'σ 18m' },
          { l:'TOTAL TOKENS', v:'12.4M', sub:'3d' },
          { l:'TOTAL SPEND',  v:'$24.80', sub:'avg $3.54/run' },
        ].map((k,i) => (
          <div key={i} style={{ background:C.bg, padding:'16px 20px' }}>
            <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em' }}>{k.l}</div>
            <div style={{ fontFamily:C.mono, fontSize:30, fontWeight:500, color: k.color || C.text, marginTop:6 }}>{k.v}</div>
            <div style={{ fontSize:12.5, color:C.textDim, marginTop:2 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      {/* table */}
      <div style={{ padding:20 }}>
        <div style={{ display:'grid', gridTemplateColumns:'12px 80px 1fr 90px 100px 100px 80px 60px', gap:14, padding:'8px 14px', fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.1em', borderBottom:`1px solid ${C.border}` }}>
          <span></span>
          <span>STATUS</span>
          <span>BRANCH</span>
          <span>TASKS</span>
          <span>DURATION</span>
          <span>STARTED</span>
          <span style={{textAlign:'right'}}>TOKENS</span>
          <span style={{textAlign:'right'}}>COST</span>
        </div>
        {MOCK.runs.map((r,i) => {
          const color = r.status==='success'? C.ok : r.status==='failed'? C.err : r.status==='stopped'? C.warn : C.accent;
          return (
            <div key={r.id} style={{ display:'grid', gridTemplateColumns:'12px 80px 1fr 90px 100px 100px 80px 60px', gap:14, padding:'12px 14px', borderBottom:`1px solid ${C.border}`, alignItems:'center', fontSize:14.5, fontFamily:C.mono }}>
              <span style={{ width:4, height:24, background:color }}/>
              <span style={{ display:'flex', alignItems:'center', gap:6, color, fontSize:12.5, letterSpacing:'0.08em' }}>
                {r.status==='running' ? <PulseDot color={color} size={5}/> : <span style={{ width:6, height:6, background:color }}/>}
                {r.status.toUpperCase()}
              </span>
              <span>
                <span style={{ color:C.text }}>{r.branch}</span>
                <div style={{ fontSize:11.5, color:C.textSub, marginTop:2 }}>{r.id}</div>
              </span>
              <span style={{ display:'flex', gap:2 }}>
                {Array.from({length: r.tasksTotal}).map((_,j) => (
                  <div key={j} style={{ width:14, height:4, background: j < r.tasksDone ? C.ok : r.status==='failed' && j===r.tasksDone ? C.err : C.border }}/>
                ))}
                <span style={{ marginLeft:8, fontSize:12.5, color:C.textDim }}>{r.tasksDone}/{r.tasksTotal}</span>
              </span>
              <span style={{ color:C.textDim }}>{fmtDuration(r.durationSec)}</span>
              <span style={{ color:C.textDim, fontSize:12.5 }}>{fmtRelative(r.startedAt)}</span>
              <span style={{ textAlign:'right', color:C.textDim, fontSize:12.5 }}>{(400 + i*180) + 'K'}</span>
              <span style={{ textAlign:'right', color:C.text, fontSize:13.5 }}>${(2.2 + i*0.5).toFixed(2)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function C_Pipeline() {
  // Deep pipeline: left stages, center details, right output
  return (
    <div style={{ width:'100%', height:'100%', background:C.bg, fontFamily:C.sans, color:C.text, display:'grid', gridTemplateColumns:'260px 1fr 300px', gridTemplateRows:'44px 1fr' }}>
      <div style={{ gridColumn:'1 / -1', display:'flex', alignItems:'center', padding:'0 20px', borderBottom:`1px solid ${C.border}`, background:C.surface, fontFamily:C.mono, fontSize:12.5 }}>
        <span style={{ color:C.accent, fontWeight:700 }}>// PIPELINE</span>
        <span style={{ marginLeft:12, color:C.textSub }}>{MOCK.activeRun.id}</span>
        <div style={{ flex:1 }}/>
        <span style={{ color:C.ok, display:'inline-flex', gap:6, alignItems:'center' }}><PulseDot color={C.ok} size={5}/>ITER 3/5</span>
      </div>

      {/* left: stages */}
      <div style={{ borderRight:`1px solid ${C.border}`, padding:16, overflow:'auto' }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em', marginBottom:10 }}>STAGES</div>
        {MOCK.stages.map((s,i) => {
          const color = s.status==='done'? C.ok : s.status==='running'? C.accent : C.textSub;
          const active = s.status==='running';
          return (
            <div key={s.id} style={{
              padding:12, marginBottom:8,
              border:`1px solid ${active? color : C.border}`,
              background: active? 'rgba(245,179,67,0.06)' : C.surface,
              position:'relative',
            }}>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <span style={{ fontFamily:C.mono, fontSize:13.5, color, fontWeight:600 }}>{String(i+1).padStart(2,'0')} · {s.label}</span>
                {active && <PulseDot color={color} size={5}/>}
              </div>
              <div style={{ fontSize:13.5, color:C.text, marginTop:4 }}>{s.title}</div>
              <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textDim, marginTop:4, display:'flex', justifyContent:'space-between' }}>
                <span>{s.model}</span>
                <span>{s.status==='pending'? '—' : fmtDuration(s.durationSec)}</span>
              </div>
            </div>
          );
        })}

        <div style={{ marginTop:18, padding:12, background:C.surface, border:`1px solid ${C.border}` }}>
          <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em', marginBottom:8 }}>CONTROLS</div>
          <button style={{ width:'100%', padding:'8px', marginBottom:6, background:'transparent', color:C.err, border:`1px solid ${C.err}`, fontFamily:C.mono, fontSize:12.5, cursor:'pointer' }}>■ STOP RUN</button>
          <button style={{ width:'100%', padding:'8px', background:C.accent, color:'#0d0f12', border:'none', fontFamily:C.mono, fontSize:12.5, fontWeight:600, cursor:'pointer' }}>↻ RESTART STAGE</button>
        </div>
      </div>

      {/* center: current task + diff */}
      <div style={{ padding:20, overflow:'auto' }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.accent, letterSpacing:'0.12em' }}>// NOW EDITING · T-018</div>
        <h3 style={{ fontSize:22, margin:'6px 0 16px', fontWeight:500, letterSpacing:'-0.015em' }}>Persist manual category overrides to SQLite</h3>

        <div style={{ background:C.surface, border:`1px solid ${C.border}`, marginBottom:14 }}>
          <div style={{ display:'flex', alignItems:'center', gap:10, padding:'8px 14px', borderBottom:`1px solid ${C.border}`, fontFamily:C.mono, fontSize:12.5 }}>
            <span style={{ color:C.accent }}>◆</span>
            <span>src/db/overrides.sql</span>
            <span style={{ marginLeft:'auto', color:C.ok }}>+34</span>
            <span style={{ color:C.err }}>−0</span>
          </div>
          <div style={{ padding:'10px 14px', fontFamily:C.mono, fontSize:13.5, lineHeight:1.6 }}>
            <div style={{ color:C.textSub }}>--- overrides schema</div>
            <div style={{ color:C.ok, background:'rgba(76,205,141,0.06)' }}>+ CREATE TABLE category_overrides (</div>
            <div style={{ color:C.ok, background:'rgba(76,205,141,0.06)' }}>+&nbsp;&nbsp;tx_id TEXT PRIMARY KEY,</div>
            <div style={{ color:C.ok, background:'rgba(76,205,141,0.06)' }}>+&nbsp;&nbsp;category_id TEXT NOT NULL,</div>
            <div style={{ color:C.ok, background:'rgba(76,205,141,0.06)' }}>+&nbsp;&nbsp;updated_at INTEGER DEFAULT (unixepoch()),</div>
            <div style={{ color:C.ok, background:'rgba(76,205,141,0.06)' }}>+&nbsp;&nbsp;FOREIGN KEY (tx_id) REFERENCES transactions(id)</div>
            <div style={{ color:C.ok, background:'rgba(76,205,141,0.06)' }}>+ );</div>
          </div>
        </div>

        <div style={{ background:C.surface, border:`1px solid ${C.border}` }}>
          <div style={{ display:'flex', gap:12, padding:'8px 14px', borderBottom:`1px solid ${C.border}`, fontFamily:C.mono, fontSize:12.5 }}>
            <span style={{ color:C.accent }}>◆</span>
            <span>AGENT THOUGHT</span>
            <span style={{ marginLeft:'auto', color:C.textSub }}>streaming</span>
            <PulseDot color={C.accent} size={5}/>
          </div>
          <div style={{ padding:14, fontSize:14.5, lineHeight:1.55, color:C.textDim }}>
            Creating the overrides table as a one-way audit log. I'll attach the repository layer next — <span style={{color:C.text}}>OverrideRepository</span> with <span style={{color:C.accent}}>set/get/clear</span> methods. On re-import, ingest will <code style={{background:C.surface2, padding:'1px 5px'}}>LEFT JOIN</code> against this table to preserve manual categories
            <span style={{ color:C.accent, animation:'acli-cursor 1s step-end infinite' }}>▍</span>
          </div>
        </div>
      </div>

      {/* right: stats */}
      <div style={{ borderLeft:`1px solid ${C.border}`, padding:16, overflow:'auto', background:C.surface }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em', marginBottom:10 }}>STAGE METRICS</div>
        {[
          { l:'DURATION',    v:'30m 42s',  bar:0.6 },
          { l:'TOKENS IN',   v:'41.2K',    bar:0.51 },
          { l:'TOKENS OUT',  v:'11.9K',    bar:0.28 },
          { l:'EDITS',       v:'3 files',  sub:'+264 −22' },
          { l:'MODEL',       v:'sonnet-4.5', sub:'tier 0 / tier 1 = opus' },
          { l:'ESCALATIONS', v:'0 / 3',    bar:0 },
        ].map((m,i) => (
          <div key={i} style={{ padding:'10px 0', borderTop: i? `1px dotted ${C.border}` : 'none' }}>
            <div style={{ display:'flex', justifyContent:'space-between', fontFamily:C.mono, fontSize:12.5 }}>
              <span style={{ color:C.textSub, letterSpacing:'0.08em' }}>{m.l}</span>
              <span style={{ color:C.text }}>{m.v}</span>
            </div>
            {m.bar != null && (
              <div style={{ marginTop:6, height:2, background:C.border }}>
                <div style={{ width:(m.bar*100)+'%', height:'100%', background: m.bar>0.7? C.err : m.bar>0.4? C.warn : C.ok }}/>
              </div>
            )}
            {m.sub && <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, marginTop:3 }}>{m.sub}</div>}
          </div>
        ))}

        <div style={{ marginTop:16 }}>
          <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em', marginBottom:8 }}>BUDGET TRAJECTORY</div>
          <Sparkline data={MOCK.metrics.budget} width={260} height={50} stroke={C.warn} fill="rgba(245,179,67,0.1)"/>
          <div style={{ display:'flex', justifyContent:'space-between', fontFamily:C.mono, fontSize:11.5, color:C.textSub, marginTop:4 }}>
            <span>$0.00</span>
            <span style={{ color:C.accent }}>$3.27 NOW</span>
            <span>$8.00</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function C_Mobile() {
  return (
    <div style={{ width:'100%', height:'100%', background:C.bg, fontFamily:C.sans, color:C.text, display:'flex', flexDirection:'column' }}>
      <div style={{ padding:'10px 16px 4px', display:'flex', justifyContent:'space-between', fontSize:12.5, fontFamily:C.mono, color:C.textDim }}>
        <span style={{color:C.accent, fontWeight:700}}>◆ AGENTCLI</span>
        <span>●●●</span>
      </div>
      <div style={{ padding:'10px 16px 16px', borderBottom:`1px solid ${C.border}` }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em' }}>// HOME-PC-MAIN</div>
        <div style={{ display:'flex', alignItems:'baseline', gap:10, marginTop:4 }}>
          <h1 style={{ margin:0, fontSize:26, fontWeight:600, letterSpacing:'-0.02em' }}>BudgetBook</h1>
          <span style={{ color:C.ok, fontFamily:C.mono, fontSize:11.5, display:'inline-flex', gap:4, alignItems:'center' }}><PulseDot color={C.ok} size={4}/>LIVE</span>
        </div>
        <div style={{ fontFamily:C.mono, fontSize:12.5, color:C.textDim, marginTop:4 }}>run_…142311 · {fmtDuration(MOCK.activeRun.elapsedSec)}</div>
      </div>

      {/* stat blocks */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:1, background:C.border, borderBottom:`1px solid ${C.border}` }}>
        {[
          { l:'STAGE',   v:'DEV',    sub:'iter 3/5', color:C.accent },
          { l:'TASKS',   v:'2/5',    sub:'3 pending' },
          { l:'BUDGET',  v:'$3.27',  sub:'41%',       bar:0.41 },
          { l:'QUOTA',   v:'34%',    sub:'5h window', bar:0.34 },
        ].map((k,i) => (
          <div key={i} style={{ background:C.bg, padding:12 }}>
            <div style={{ fontFamily:C.mono, fontSize:11, color:C.textSub, letterSpacing:'0.12em' }}>{k.l}</div>
            <div style={{ fontFamily:C.mono, fontSize:22, fontWeight:600, color: k.color || C.text, marginTop:3 }}>{k.v}</div>
            <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textDim, marginTop:2 }}>{k.sub}</div>
            {k.bar != null && (
              <div style={{ marginTop:6, height:2, background:C.border }}>
                <div style={{ width:(k.bar*100)+'%', height:'100%', background: k.bar>0.7? C.err : k.bar>0.4? C.warn : C.ok }}/>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* pipeline compact */}
      <div style={{ padding:'14px 16px', borderBottom:`1px solid ${C.border}` }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em', marginBottom:10 }}>// PIPELINE</div>
        {MOCK.stages.map((s,i) => {
          const color = s.status==='done'? C.ok : s.status==='running'? C.accent : C.textSub;
          return (
            <div key={s.id} style={{ display:'flex', gap:10, alignItems:'center', padding:'7px 0', borderTop: i? `1px dotted ${C.border}` : 'none' }}>
              <span style={{ fontFamily:C.mono, fontSize:12.5, color, width:58, fontWeight:600 }}>{s.label.toUpperCase()}</span>
              <span style={{ flex:1, fontSize:14, color:C.text }}>{s.title}</span>
              {s.status==='running' ? <PulseDot color={color} size={5}/> : <span style={{ color, fontSize:11.5, fontFamily:C.mono, letterSpacing:'0.08em' }}>{s.status.toUpperCase()}</span>}
            </div>
          );
        })}
      </div>

      {/* alerts */}
      <div style={{ padding:'12px 16px', flex:1, overflow:'auto' }}>
        <div style={{ fontFamily:C.mono, fontSize:11.5, color:C.textSub, letterSpacing:'0.12em', marginBottom:8 }}>// ALERTS</div>
        {MOCK.notifications.slice(0,4).map((n,i) => {
          const color = n.kind==='task_done'? C.ok : n.kind==='error' || n.kind==='task_failed'? C.err : n.kind==='quota'? C.warn : C.info;
          return (
            <div key={i} style={{ padding:'10px 0', borderTop: i ? `1px dotted ${C.border}` : 'none' }}>
              <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                <span style={{ width:4, height:4, background:color }}/>
                <span style={{ fontFamily:C.mono, fontSize:11.5, color, letterSpacing:'0.1em' }}>{n.kind.toUpperCase()}</span>
                <span style={{ marginLeft:'auto', fontFamily:C.mono, fontSize:11.5, color:C.textSub }}>{fmtRelative(n.t)}</span>
              </div>
              <div style={{ fontSize:14, color:C.text, marginTop:3, lineHeight:1.4 }}>{n.text}</div>
            </div>
          );
        })}
      </div>

      {/* commands */}
      <div style={{ display:'flex', gap:6, padding:'10px 14px', borderTop:`1px solid ${C.border}`, background:C.surface }}>
        {[
          { l:'/status', color:C.ok },
          { l:'/detail' },
          { l:'/tail' },
          { l:'/stop', color:C.err },
        ].map(c => (
          <span key={c.l} style={{ flex:1, textAlign:'center', padding:'7px 0', border:`1px solid ${c.color || C.borderHi}`, fontFamily:C.mono, fontSize:12.5, color: c.color || C.text, letterSpacing:'0.05em' }}>{c.l}</span>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { C_Landing, C_Dashboard, C_Pipeline, C_RunHistory, C_Mobile });
