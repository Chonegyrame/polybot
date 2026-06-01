// =============================================================
// esports.jsx — Esports sharps section
// Live feed of tracked esports specialists' entries/exits (with the price
// WE'd pay to follow) + the vetted watchlist. Reads the local-SQLite tracker
// via GET /esports/* (separate from the Supabase generalist tracker).
// =============================================================

function SectorChips({ sectors }) {
  const parts = (sectors || '').split(',').map(s => s.trim()).filter(Boolean);
  return (
    <span style={{ display: 'inline-flex', gap: 4 }}>
      {parts.map(p => (
        <span key={p} className="chip" style={{ padding: '1px 6px', fontSize: 9, textTransform: 'uppercase' }}>{p}</span>
      ))}
    </span>
  );
}

function SideChip({ side }) {
  const buy = side === 'BUY';
  return (
    <span className={`chip ${buy ? 'ok' : ''}`}
      style={{ padding: '1px 7px', fontSize: 10, fontWeight: 600,
        color: buy ? undefined : 'var(--neg, #f06)',
        borderColor: buy ? undefined : 'var(--neg, #f06)' }}>
      {side}
    </span>
  );
}

const TYPE_LABEL = { winner: 'Winner', handicap: 'Handicap', total: 'Total', prop: 'Prop' };

function Sparkline({ points, height = 130 }) {
  if (!points || points.length < 2) {
    return <div className="muted" style={{ fontSize: 12.5, padding: '20px 0' }}>Not enough resolved markets to chart a curve yet.</div>;
  }
  const ys = points.map(p => p.cum);
  const min = Math.min(0, ...ys), max = Math.max(0, ...ys);
  const W = 680, H = height, pad = 6;
  const n = points.length;
  const span = (max - min) || 1;
  const x = i => pad + (i / (n - 1)) * (W - 2 * pad);
  const y = v => (H - pad) - ((v - min) / span) * (H - 2 * pad);
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' ');
  const area = `${line} L${x(n - 1).toFixed(1)},${y(min).toFixed(1)} L${x(0).toFixed(1)},${y(min).toFixed(1)} Z`;
  const pos = ys[ys.length - 1] >= 0;
  const col = pos ? 'var(--accent)' : 'var(--neg, #f06)';
  const zeroY = y(0).toFixed(1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
      <defs>
        <linearGradient id="sparkfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={col} stopOpacity="0.22" />
          <stop offset="100%" stopColor={col} stopOpacity="0" />
        </linearGradient>
      </defs>
      <line x1={pad} y1={zeroY} x2={W - pad} y2={zeroY} stroke="var(--border)" strokeWidth="1" strokeDasharray="3 3" />
      <path d={area} fill="url(#sparkfill)" />
      <path d={line} fill="none" stroke={col} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

function EsportsWalletModal({ wallet, onClose, openMarket }) {
  const res = useApi(wallet ? `/esports/wallet/${wallet}` : null, null);
  const d = res.data;
  const m = d?.meta, c = d?.curve, acts = d?.actions || [];
  return (
    <div className="modal-backdrop" onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 50, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '6vh 16px', overflowY: 'auto' }}>
      <div className="card" onClick={e => e.stopPropagation()}
        style={{ width: 'min(720px, 100%)', padding: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <h2 style={{ margin: 0, fontSize: 18 }}>{m?.name || (wallet.slice(0, 10) + '…')}</h2>
              {m && <span className={`chip ${m.follow ? 'ok' : ''}`} style={{ padding: '1px 7px', fontSize: 9 }}>{m.follow ? 'follow' : 'watch'}</span>}
              {m && <SectorChips sectors={m.sectors} />}
            </div>
            <a className="mono muted" href={`https://polymarket.com/profile/${wallet}`} target="_blank" rel="noreferrer" style={{ fontSize: 11 }}>{wallet} ↗</a>
          </div>
          <button className="btn" onClick={onClose} style={{ fontSize: 18, lineHeight: 1, padding: '2px 10px' }}>×</button>
        </div>
        <div style={{ padding: 18 }}>
          {res.loading && <div className="muted">Loading equity curve…</div>}
          {m?.note && <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>{m.note}</div>}
          {c && (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <span className="trade-label" style={{ margin: 0 }}>Recent-form equity (≤2500 trades, by resolution date)</span>
                <span className={`mono ${c.total_pnl >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 14, fontWeight: 600 }}>
                  {c.total_pnl != null ? fmtUSD(c.total_pnl) : '—'}
                </span>
              </div>
              <Sparkline points={c.points} />
              <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                {c.markets} resolved esports markets · win {c.win_rate != null ? `${(c.win_rate * 100).toFixed(0)}%` : '—'}
                {c.error ? ' · curve unavailable (API error)' : ''}
              </div>
            </>
          )}
          <div className="trade-label" style={{ marginTop: 18 }}>Logged actions ({acts.length})</div>
          {acts.length === 0 ? (
            <div className="muted" style={{ fontSize: 12.5, padding: '6px 0' }}>None yet — captured live as this wallet trades while the tracker runs.</div>
          ) : (
            <table className="table" style={{ marginTop: 4 }}>
              <tbody>
                {acts.map(a => (
                  <tr key={a.id} className={a.condition_id ? 'row-clickable' : ''} onClick={a.condition_id ? () => { openMarket(a.condition_id); onClose(); } : undefined}>
                    <td className="muted mono" style={{ fontSize: 11, width: 64 }}>{tsAgo(a.detected_at)}</td>
                    <td><SideChip side={a.side} /></td>
                    <td style={{ fontSize: 12 }}>{a.market_type && <span className="chip" style={{ padding: '0 5px', fontSize: 8, marginRight: 5 }}>{TYPE_LABEL[a.market_type] || a.market_type}</span>}{(a.title || '').slice(0, 42)}</td>
                    <td className="num" style={{ fontSize: 12 }}>{a.their_price != null ? a.their_price.toFixed(2) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function EsportsFeed({ followOnly, game, type, openMarket, openWallet }) {
  let path = `/esports/actions?limit=120`;
  if (followOnly) path += '&follow_only=true';
  if (game && game !== 'all') path += `&game=${game}`;
  if (type && type !== 'all') path += `&market_type=${type}`;
  const res = useApi(path, { actions: [] }, { pollMs: 15000 });
  const actions = res.data?.actions || [];
  const offline = res.source === 'mock';

  if (res.loading && actions.length === 0) {
    return <div className="card card-pad muted">Loading feed…</div>;
  }
  if (actions.length === 0) {
    return (
      <div className="card card-pad muted" style={{ lineHeight: 1.6 }}>
        {offline
          ? '⚠ Backend offline — start the API (polybot) to read the tracker.'
          : 'No actions logged yet. Launch esports.bat to start the tracker; ' +
            'detected entries/exits will stream in here as your sharps trade.'}
      </div>
    );
  }
  return (
    <div className="card">
      {offline && <div className="card-pad muted" style={{ fontSize: 12, borderBottom: '1px solid var(--border)' }}>⚠ Backend offline — showing cached/empty data.</div>}
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 70 }}>Seen</th>
            <th>Sharp</th>
            <th style={{ width: 60 }}>Side</th>
            <th style={{ width: 130 }}>Type</th>
            <th>Market</th>
            <th className="num">Their px</th>
            <th className="num">Our ask</th>
            <th className="num">Slippage</th>
            <th className="num">Size</th>
          </tr>
        </thead>
        <tbody>
          {actions.map(a => (
            <tr key={a.id} className={a.condition_id ? 'row-clickable' : ''}
              onClick={a.condition_id ? () => openMarket(a.condition_id) : undefined}>
              <td className="muted mono" style={{ fontSize: 11 }}>{tsAgo(a.detected_at)}</td>
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="link-like" onClick={(e) => { e.stopPropagation(); openWallet(a.wallet); }}
                    style={{ cursor: 'pointer', borderBottom: '1px dotted var(--border)' }}>
                    {a.name || <span className="mono muted">{a.wallet.slice(0, 10)}…</span>}
                  </span>
                  {a.follow === false && <span className="chip" style={{ padding: '0 5px', fontSize: 8 }}>watch</span>}
                </div>
              </td>
              <td><SideChip side={a.side} /></td>
              <td>
                <span style={{ display: 'inline-flex', gap: 4 }}>
                  {a.game && <span className="chip" style={{ padding: '1px 6px', fontSize: 9, textTransform: 'uppercase' }}>{a.game}</span>}
                  {a.market_type && <span className={`chip ${a.market_type === 'handicap' ? 'purple' : ''}`} style={{ padding: '1px 6px', fontSize: 9 }}>{TYPE_LABEL[a.market_type] || a.market_type}</span>}
                </span>
              </td>
              <td style={{ maxWidth: 360 }}>
                <span className="ellipsis" title={a.title || ''}>{a.title || a.slug || a.condition_id?.slice(0, 16)}</span>
                {a.outcome && <span className="mono muted" style={{ fontSize: 11 }}> · {a.outcome}</span>}
              </td>
              <td className="num">{a.their_price != null ? a.their_price.toFixed(2) : '—'}</td>
              <td className="num">{a.live_ask != null ? a.live_ask.toFixed(2) : '—'}</td>
              <td className={`num ${a.slippage > 0 ? 'neg' : a.slippage < 0 ? 'pos' : 'muted'}`}>
                {a.slippage != null ? `${a.slippage >= 0 ? '+' : ''}${(a.slippage * 100).toFixed(0)}¢` : '—'}
              </td>
              <td className="num muted">{a.usdc_size != null ? fmtUSD(a.usdc_size) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EsportsWatchlist({ openWallet }) {
  const res = useApi('/esports/sharps', { sharps: [] });
  const sharps = res.data?.sharps || [];
  if (res.loading && sharps.length === 0) {
    return <div className="card card-pad muted">Loading watchlist…</div>;
  }
  return (
    <div className="card">
      <table className="table">
        <thead>
          <tr>
            <th>Sharp</th>
            <th>Games</th>
            <th style={{ width: 70 }}>Status</th>
            <th className="num">Vet PnL</th>
            <th className="num">Win%</th>
            <th className="num">ROI</th>
            <th className="num">Entry</th>
            <th className="num">Logged</th>
          </tr>
        </thead>
        <tbody>
          {sharps.map(s => (
            <tr key={s.wallet} className="row-clickable" onClick={() => openWallet(s.wallet)}>
              <td>
                <span style={{ color: 'var(--text-1)', borderBottom: '1px dotted var(--border)' }} title={s.note || s.wallet}>
                  {s.name || <span className="mono muted">{s.wallet.slice(0, 12)}…</span>}
                </span>
                {s.note && <div className="muted" style={{ fontSize: 10.5 }}>{s.note}</div>}
              </td>
              <td><SectorChips sectors={s.sectors} /></td>
              <td>
                <span className={`chip ${s.follow ? 'ok' : ''}`} style={{ padding: '1px 7px', fontSize: 9 }}>
                  {s.follow ? 'follow' : 'watch'}
                </span>
              </td>
              <td className={`num ${s.vet_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtUSD(s.vet_pnl)}</td>
              <td className="num muted">{s.vet_win_rate != null ? `${(s.vet_win_rate * 100).toFixed(0)}%` : '—'}</td>
              <td className={`num ${s.vet_roi >= 0 ? 'pos' : 'neg'}`}>{s.vet_roi != null ? fmtPctSigned(s.vet_roi, 0) : '—'}</td>
              <td className="num muted">{s.vet_median_entry != null ? s.vet_median_entry.toFixed(2) : '—'}</td>
              <td className="num muted">{s.action_count || 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EsportsPage({ openMarket }) {
  const [followOnly, setFollowOnly] = useState(false);
  const [game, setGame] = useState('all');
  const [type, setType] = useState('all');
  const [tab, setTab] = useState('feed');
  const [walletModal, setWalletModal] = useState(null);
  const sum = useApi('/esports/summary', null, { pollMs: 30000 });
  const s = sum.data;
  return (
    <>
      <div className="topbar">
        <div>
          <h1>Esports sharps</h1>
          <div className="topbar-sub">
            {s && s.tracking
              ? `tracking ${s.wallets} wallets (${s.follow} follow · ${s.watch} watch) · ${s.actions} actions logged${s.last_detected_at ? ' · last ' + tsAgo(s.last_detected_at) : ''}`
              : 'local-SQLite tracker · forward-test capture (read-only)'}
          </div>
        </div>
      </div>
      <div className="content">
        <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="segmented green">
            <button className={tab === 'feed' ? 'on' : ''} onClick={() => setTab('feed')}>Live feed</button>
            <button className={tab === 'watch' ? 'on' : ''} onClick={() => setTab('watch')}>Watchlist</button>
          </div>
          {tab === 'feed' && (
            <>
              <span className="trade-label" style={{ margin: '0 0 0 8px' }}>Show</span>
              <div className="segmented">
                <button className={!followOnly ? 'on' : ''} onClick={() => setFollowOnly(false)}>All</button>
                <button className={followOnly ? 'on' : ''} onClick={() => setFollowOnly(true)}>Follow only</button>
              </div>
              <span className="trade-label" style={{ margin: '0 0 0 8px' }}>Game</span>
              <div className="segmented">
                {['all', 'lol', 'cs'].map(g => (
                  <button key={g} className={game === g ? 'on' : ''} onClick={() => setGame(g)}>
                    {g === 'all' ? 'All' : g.toUpperCase()}
                  </button>
                ))}
              </div>
              <span className="trade-label" style={{ margin: '0 0 0 8px' }}>Type</span>
              <div className="segmented">
                {['all', 'winner', 'handicap', 'total', 'prop'].map(ty => (
                  <button key={ty} className={type === ty ? 'on' : ''} onClick={() => setType(ty)}>
                    {ty === 'all' ? 'All' : TYPE_LABEL[ty]}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        {tab === 'feed'
          ? <EsportsFeed followOnly={followOnly} game={game} type={type} openMarket={openMarket} openWallet={setWalletModal} />
          : <EsportsWatchlist openWallet={setWalletModal} />}
      </div>
      {walletModal && (
        <EsportsWalletModal wallet={walletModal} onClose={() => setWalletModal(null)} openMarket={openMarket} />
      )}
    </>
  );
}
