// =============================================================
// app.jsx — root + routing + paper-trade state + Tweaks
// =============================================================
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#00d97e",
  "density": "spacious",
  "showCounterparty": true,
  "showStaleSignals": true
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [route, setRoute] = useState('dashboard');
  const [signalState, setSignalState] = useState({
    mode: 'absolute', category: 'overall', top_n: 50, sort: 'gap',
  });
  const [trader, setTrader] = useState(null);
  const [marketCtx, setMarketCtx] = useState(null);
  // Paper trades: fetched live from GET /paper_trades, with mock fallback.
  // `placePaperTrade` POSTs to backend then re-fetches; offline writes go local.
  const [paperTrades, setPaperTrades] = useState(PB.PAPER_TRADES);
  const [paperTradesOffline, setPaperTradesOffline] = useState(false);
  useEffect(() => {
    let cancelled = false;
    apiGet('/paper_trades').then(
      (resp) => { if (!cancelled) { setPaperTrades(resp.trades || []); setPaperTradesOffline(false); } },
      (e) => { if (!cancelled) { console.warn('Paper trades offline:', e.message); setPaperTradesOffline(true); } }
    );
    return () => { cancelled = true; };
  }, []);

  // apply tweaks
  useEffect(() => {
    document.documentElement.style.setProperty('--accent', t.accent);
    const accentRgb = hexToRgb(t.accent);
    if (accentRgb) {
      document.documentElement.style.setProperty('--accent-soft', `rgba(${accentRgb}, 0.13)`);
      document.documentElement.style.setProperty('--accent-line', `rgba(${accentRgb}, 0.32)`);
      document.documentElement.style.setProperty('--yes', t.accent);
      document.documentElement.style.setProperty('--yes-soft', `rgba(${accentRgb}, 0.14)`);
    }
    document.body.dataset.density = t.density;
  }, [t.accent, t.density]);

  function openTrader(wallet) {
    setTrader(wallet);
    setMarketCtx(null);
  }
  function openMarket(conditionId, direction) {
    setMarketCtx({ conditionId, direction });
    setTrader(null);
  }
  async function placePaperTrade(trade) {
    // Try POST /paper_trades to the backend. Server validates + computes effective entry,
    // then we re-fetch the list to pick up the canonical row. If backend is offline,
    // fall back to local-only state so the user can still play with the form.
    try {
      const body = {
        condition_id: trade.condition_id,
        direction: trade.direction,
        size_usdc: trade.entry_size_usdc,
        signal_log_id: trade.signal_log_id ?? null,
        notes: trade.notes ?? null,
      };
      await apiPost('/paper_trades', body);
      const resp = await apiGet('/paper_trades');
      setPaperTrades(resp.trades || []);
      setPaperTradesOffline(false);
    } catch (e) {
      console.warn('Paper trade POST failed (offline?):', e.message);
      setPaperTradesOffline(true);
      setPaperTrades(prev => [{
        id: Date.now(),
        ...trade,
        unrealized_pnl_usdc: 0,
        realized_pnl_usdc: null,
        status: 'open',
        exit_reason: null,
        entry_at: new Date().toISOString(),
        exit_at: null,
      }, ...prev]);
    }
  }

  return (
    <div className="app">
      <Sidebar route={route} setRoute={setRoute} status={PB.SYSTEM_STATUS} />
      <main className="main">
        {route === 'dashboard' && (
          <Dashboard
            state={signalState} setState={setSignalState}
            openTrader={openTrader} openMarket={openMarket}
          />
        )}
        {route === 'traders' && <TradersPage openTrader={openTrader} />}
        {route === 'testing' && <Testing paperTrades={paperTrades} openMarket={openMarket} />}
        {route === 'insider' && <InsiderWallets />}
      </main>
      {trader && <TraderModal wallet={trader} onClose={() => setTrader(null)} openMarket={openMarket} />}
      {marketCtx && (
        <MarketView
          conditionId={marketCtx.conditionId}
          presetDirection={marketCtx.direction}
          onClose={() => setMarketCtx(null)}
          openTrader={(w) => { setMarketCtx(null); setTrader(w); }}
          onPaperTrade={placePaperTrade}
        />
      )}
      <TweaksPanel title="Tweaks">
        <TweakSection label="Accent color" />
        <TweakColor
          label="Accent"
          value={t.accent}
          options={['#00d97e', '#3b82f6', '#9b8cff', '#f7b955']}
          onChange={(v) => setTweak('accent', v)}
        />
        <TweakSection label="Density" />
        <TweakRadio
          label="Card density"
          value={t.density}
          options={['compact', 'spacious']}
          onChange={(v) => setTweak('density', v)}
        />
        <TweakSection label="Display" />
        <TweakToggle
          label="Show counterparty conflicts"
          value={t.showCounterparty}
          onChange={(v) => setTweak('showCounterparty', v)}
        />
        <TweakToggle
          label="Show stale signals"
          value={t.showStaleSignals}
          onChange={(v) => setTweak('showStaleSignals', v)}
        />
      </TweaksPanel>
    </div>
  );
}

function TradersPage({ openTrader }) {
  const [mode, setMode] = useState('absolute');
  const [cat, setCat] = useState('overall');
  return (
    <>
      <div className="topbar">
        <div>
          <h1>Top traders · {PB.MODES.find(m=>m.id===mode).label}</h1>
          <div className="topbar-sub">cluster-collapsed leaderboard · MM/arb/sybil filtered</div>
        </div>
      </div>
      <div className="content">
        <div className="card" style={{padding:14, marginBottom:18, display:'flex',gap:14,alignItems:'center',flexWrap:'wrap'}}>
          <span className="trade-label" style={{margin:0}}>Mode</span>
          <div className="segmented green">
            {PB.MODES.map(m => (
              <button key={m.id} className={mode===m.id?'on':''} onClick={()=>setMode(m.id)}>{m.label}</button>
            ))}
          </div>
          <span className="trade-label" style={{margin:'0 0 0 12px'}}>Category</span>
          <div className="segmented">
            {PB.CATEGORIES.map(c => (
              <button key={c} className={cat===c?'on':''} onClick={()=>setCat(c)}>{PB.CATEGORY_LABELS[c]}</button>
            ))}
          </div>
        </div>
        <TopTradersFullPage mode={mode} category={cat} openTrader={openTrader} />
      </div>
    </>
  );
}

function TopTradersFullPage({ mode, category, openTrader }) {
  // Live: GET /traders/top with the parent's mode + category.
  const path = `/traders/top?mode=${mode || 'absolute'}&category=${category || 'overall'}&top_n=100`;
  const res = useApi(path, { traders: PB.TOP_TRADERS });
  const traders = res.data?.traders || [];
  if (res.loading && traders.length === 0) {
    return <div className="card card-pad muted">Loading traders…</div>;
  }
  return (
    <div className="card">
      {res.error && <div className="card-pad muted" style={{fontSize:12,borderBottom:'1px solid var(--border)'}}>⚠ Backend offline — showing mock data.</div>}
      <table className="table">
        <thead>
          <tr>
            <th style={{width:50}}>#</th>
            <th>Trader</th>
            <th>Class</th>
            <th>PnL</th>
            <th>ROI</th>
            <th>Volume</th>
            <th>Resolved</th>
            <th>Active</th>
            <th>Cluster</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {traders.map(t => (
            <tr key={t.proxy_wallet} className="row-clickable" onClick={() => openTrader(t.proxy_wallet)}>
              <td className="num muted">#{t.rank}</td>
              <td>
                <div style={{display:'flex',alignItems:'center',gap:8}}>
                  <div className="avatar" style={{width:24,height:24,fontSize:10}}>{(t.user_name||'?')[0]}</div>
                  <div>
                    <div>{t.user_name || <span className="mono muted">{t.proxy_wallet}</span>}</div>
                    {t.user_name && <div className="mono muted" style={{fontSize:11}}>{t.proxy_wallet}</div>}
                  </div>
                  {t.verified_badge && <span className="chip ok" style={{padding:'1px 6px',fontSize:9}}>✓</span>}
                </div>
              </td>
              <td><span className="chip" style={{textTransform:'uppercase',fontSize:10}}>directional</span></td>
              <td className="num pos">{fmtUSD(t.pnl)}</td>
              <td className="num">{fmtPct(t.roi)}</td>
              <td className="num muted">{fmtUSD(t.vol)}</td>
              <td className="num muted">{t.n_resolved}</td>
              <td className="num muted">{t.n_active}</td>
              <td>{t.cluster_id ? <span className="chip purple" style={{padding:'1px 6px',fontSize:10}}>C{t.cluster_id}</span> : <span className="muted">—</span>}</td>
              <td className="muted">→</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function hexToRgb(hex) {
  const m = hex.match(/^#([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
  return m ? `${parseInt(m[1],16)}, ${parseInt(m[2],16)}, ${parseInt(m[3],16)}` : null;
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
