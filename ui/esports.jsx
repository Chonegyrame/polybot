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
  // Fast: meta + logged actions (local SQLite). Curve loads separately so the
  // modal paints instantly instead of hanging ~8s on the equity reconstruction.
  const res = useApi(wallet ? `/esports/wallet/${wallet}` : null, null);
  const curveRes = useApi(wallet ? `/esports/wallet/${wallet}/curve` : null, null);
  const d = res.data;
  const m = d?.meta, acts = d?.actions || [];
  const c = curveRes.data?.curve;
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
          {res.loading && !m && <div className="muted">Loading…</div>}
          {m?.note && <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>{m.note}</div>}
          {/* vetted stats render instantly from local data */}
          {m && (
            <div style={{ display: 'flex', gap: 18, marginBottom: 14, flexWrap: 'wrap' }}>
              {[['Vetted PnL', m.vet_pnl != null ? fmtUSD(m.vet_pnl) : '—', m.vet_pnl >= 0 ? 'pos' : 'neg'],
                ['Win rate', m.vet_win_rate != null ? `${(m.vet_win_rate * 100).toFixed(0)}%` : '—', ''],
                ['ROI', m.vet_roi != null ? fmtPctSigned(m.vet_roi, 0) : '—', m.vet_roi >= 0 ? 'pos' : 'neg'],
                ['Median entry', m.vet_median_entry != null ? m.vet_median_entry.toFixed(2) : '—', ''],
                ['Markets', m.vet_markets != null ? fmtNum(m.vet_markets) : '—', '']].map(([lbl, val, cls]) => (
                <div key={lbl}>
                  <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 }}>{lbl}</div>
                  <div className={`mono ${cls}`} style={{ fontSize: 14, fontWeight: 600 }}>{val}</div>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
            <span className="trade-label" style={{ margin: 0 }}>Recent-form equity (≤2500 trades, by resolution date)</span>
            {c && <span className={`mono ${c.total_pnl >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 14, fontWeight: 600 }}>
              {c.total_pnl != null ? fmtUSD(c.total_pnl) : '—'}
            </span>}
          </div>
          {curveRes.loading
            ? <div className="muted" style={{ fontSize: 12.5, padding: '24px 0', textAlign: 'center' }}>Reconstructing equity curve…</div>
            : c && (
              <>
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

// ---- Live-match drill-down: match → sub-markets → individual trades ---------

function StatusDot({ live }) {
  return (
    <span className={live ? 'es-live-dot' : ''}
      title={live ? 'market open (live)' : 'market closed / settling'}
      style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
        background: live ? 'var(--yes)' : 'var(--text-4)' }} />
  );
}

// Two-sided consensus bar: lean side fills from the left, with both counts.
function LeanBar({ outcomes, buyers, height = 7 }) {
  const lean = outcomes[0], against = outcomes[1];
  const leanPct = buyers ? Math.max(8, Math.min(92, Math.round(((lean?.buyers || 0) / buyers) * 100))) : 50;
  return (
    <div style={{ minWidth: 0, flex: 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11.5, marginBottom: 4 }}>
        <span className="ellipsis" style={{ color: 'var(--yes)', fontWeight: 600 }}>
          {lean ? lean.outcome : '—'} <span className="mono">{lean?.buyers || 0}</span>
        </span>
        {against && <span className="ellipsis muted" style={{ textAlign: 'right' }}>
          <span className="mono">{against.buyers}</span> {against.outcome}
        </span>}
      </div>
      <div style={{ height, borderRadius: 5, background: 'var(--no-soft)', overflow: 'hidden', display: 'flex' }}>
        <div style={{ width: `${leanPct}%`, background: 'var(--yes)' }} />
      </div>
    </div>
  );
}

// BUY = entry; SELL = exit/trim — shown red so fast moves stand out.
function ActionChip({ side }) {
  const sell = side === 'SELL';
  return (
    <span className="chip" style={{ padding: '1px 7px', fontSize: 9.5, fontWeight: 600,
      color: sell ? 'var(--no)' : 'var(--yes)', borderColor: sell ? 'var(--no)' : 'var(--yes)' }}>
      {sell ? 'EXIT' : 'BUY'}
    </span>
  );
}

// LEVEL 2 — one sub-market (Game 1 winner / Match winner / a total / handicap).
// Collapsed: label + consensus + how many sharps, how big, at what price.
// Expanded: the individual trades inside it (who, when, price, size, exits).
function SubMarketRow({ mk, openMarket, openWallet }) {
  const [open, setOpen] = useState(false);
  const oc = mk.outcomes || [];
  const leanAvg = oc[0]?.avg_entry;
  const typeChip = TYPE_LABEL[mk.market_type];
  const live = mk.market_open === true && !mk.resolved;
  return (
    <div style={{ borderTop: '1px solid var(--border)', opacity: mk.resolved ? 0.62 : 1 }}>
      <div className="es-sub-head" onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 14px', cursor: 'pointer' }}>
        <span style={{ width: 12, color: 'var(--text-3)', fontSize: 11, flexShrink: 0,
          transition: 'transform .15s', transform: open ? 'rotate(90deg)' : 'none' }}>▸</span>
        {mk.resolved
          ? <span style={{ width: 8, flexShrink: 0, fontSize: 12, lineHeight: 1, color: mk.consensus_correct ? 'var(--yes)' : 'var(--no)' }}>{mk.consensus_correct ? '✓' : '✗'}</span>
          : (live ? <StatusDot live /> : <span style={{ width: 8, flexShrink: 0 }} />)}
        <div style={{ width: 150, display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 12.5 }} className="ellipsis">{mk.label}</span>
          {mk.market_type && mk.market_type !== 'winner' && typeChip &&
            <span className={`chip ${mk.market_type === 'handicap' ? 'purple' : ''}`} style={{ padding: '0 5px', fontSize: 8 }}>{typeChip}</span>}
        </div>
        {mk.resolved ? (
          <div style={{ flex: 1, minWidth: 0, fontSize: 12 }} className="ellipsis">
            <span className="muted">won: </span>
            <span style={{ fontWeight: 600 }}>{mk.resolved_outcome || '—'}</span>
            <span className="muted"> · sharps had </span>
            <span style={{ color: mk.consensus_correct ? 'var(--yes)' : 'var(--no)', fontWeight: 600 }}>
              {mk.lean_outcome || '—'} {mk.consensus_correct ? '✓' : '✗'}
            </span>
          </div>
        ) : <LeanBar outcomes={oc} buyers={mk.buyers} />}
        <div style={{ display: 'flex', gap: 14, fontSize: 11.5, whiteSpace: 'nowrap', alignItems: 'center', flexShrink: 0 }}>
          <span className="muted" style={{ width: 56, textAlign: 'right' }}>{mk.buyers} sharp{mk.buyers === 1 ? '' : 's'}</span>
          <span className="mono" style={{ fontWeight: 600, width: 52, textAlign: 'right' }}>{fmtUSD(mk.notional)}</span>
          {mk.resolved ? (
            <span style={{ width: 78, textAlign: 'right' }} title="P&L per $1 if you'd followed the consensus at the price you'd have paid">
              {mk.follow_pnl != null
                ? <span className={`mono ${mk.follow_pnl >= 0 ? 'pos' : 'neg'}`}>{mk.follow_pnl >= 0 ? '+' : ''}{(mk.follow_pnl * 100).toFixed(0)}%</span>
                : <span className="muted">—</span>}
            </span>
          ) : (
            <span style={{ width: 78, textAlign: 'right' }} title="avg price the lean side paid → most recent ask we saw">
              <span className="mono muted">@{leanAvg != null ? leanAvg.toFixed(2) : '—'}</span>
              {mk.our_ask != null && mk.our_ask >= 0.02 && mk.our_ask <= 0.98 && leanAvg != null &&
                <span className={`mono ${mk.our_ask > leanAvg ? 'neg' : 'pos'}`}> →{mk.our_ask.toFixed(2)}</span>}
            </span>
          )}
          {mk.exits > 0 ? <span className="neg mono" style={{ width: 30 }} title="exits / sells">↩{mk.exits}</span>
            : <span style={{ width: 30 }} />}
        </div>
      </div>
      {open && (
        <div style={{ padding: '2px 14px 8px 38px', background: 'var(--panel)' }}>
          <table className="table">
            <tbody>
              {(mk.actions || []).map(a => (
                <tr key={a.id} className="es-trade-row row-clickable"
                  onClick={a.condition_id ? () => openMarket(a.condition_id) : undefined}>
                  <td className="muted mono" style={{ fontSize: 11, width: 56 }}>{tsAgo(a.detected_at)}</td>
                  <td>
                    <span className="link-like" onClick={(e) => { e.stopPropagation(); openWallet(a.wallet); }}
                      style={{ cursor: 'pointer', borderBottom: '1px dotted var(--border)' }}>
                      {a.name || <span className="mono muted">{a.wallet.slice(0, 8)}…</span>}
                    </span>
                  </td>
                  <td style={{ width: 56 }}><ActionChip side={a.side} /></td>
                  <td style={{ fontSize: 12, fontWeight: 500 }} className="ellipsis">{a.outcome || '—'}</td>
                  <td className="num mono" style={{ fontSize: 12, width: 50 }}>{a.their_price != null ? a.their_price.toFixed(2) : '—'}</td>
                  <td className="num muted mono" style={{ fontSize: 12, width: 64 }}>{a.notional != null ? fmtUSD(a.notional) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// LEVEL 1 — a match. Collapsed: teams, live status, totals + a one-line teaser
// of the most-active market. Expanded: every sub-market (live/hottest first).
function MatchCard({ m, openMarket, openWallet }) {
  const [open, setOpen] = useState(false);
  const isLive = mk => mk.market_open === true && !mk.resolved;
  // Live markets on top, then still-open, then finished at the bottom.
  const markets = [...(m.markets || [])].sort((a, b) =>
    (isLive(b) - isLive(a))
    || ((b.resolved ? 0 : 1) - (a.resolved ? 0 : 1))
    || (b.buyers - a.buyers) || (b.notional - a.notional));
  const hottest = markets.find(isLive) || markets.find(mk => !mk.resolved) || markets[0];
  const hoc = hottest?.outcomes || [];
  return (
    <div className="card" style={{ marginBottom: 12, overflow: 'hidden' }}>
      <div className="es-sub-head" onClick={() => setOpen(o => !o)}
        style={{ padding: '12px 14px', cursor: 'pointer', borderBottom: open ? '1px solid var(--border)' : 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ width: 12, color: 'var(--text-3)', fontSize: 12, flexShrink: 0,
            transition: 'transform .15s', transform: open ? 'rotate(90deg)' : 'none' }}>▸</span>
          <StatusDot live={m.is_live} />
          {m.game && <span className="chip" style={{ padding: '1px 6px', fontSize: 9, textTransform: 'uppercase' }}>{m.game}</span>}
          <span style={{ fontWeight: 700, fontSize: 14 }} className="ellipsis">{m.title}</span>
          <span style={{ flex: 1 }} />
          <span className="muted" style={{ fontSize: 11 }}>{markets.length} market{markets.length === 1 ? '' : 's'}</span>
          <span className="mono muted" style={{ fontSize: 11.5 }}>{m.sharps} sharp{m.sharps === 1 ? '' : 's'}</span>
          <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, width: 54, textAlign: 'right' }}>{fmtUSD(m.total_notional)}</span>
          {m.last_detected_at && <span className="muted mono" style={{ fontSize: 10.5, width: 54, textAlign: 'right' }}>{tsAgo(m.last_detected_at)}</span>}
        </div>
        {!open && hottest && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 9, paddingLeft: 22 }}>
            {hottest.market_open === true && <span className="chip" style={{ padding: '0 6px', fontSize: 8, color: 'var(--yes)', borderColor: 'var(--yes)' }}>LIVE</span>}
            <span className="chip" style={{ padding: '1px 7px', fontSize: 9 }}>{hottest.label}</span>
            <span style={{ fontSize: 11.5 }} className="ellipsis">
              <span style={{ color: 'var(--yes)', fontWeight: 600 }}>{hoc[0]?.outcome} {hoc[0]?.buyers || 0}</span>
              {hoc[1] && <span className="muted"> – {hoc[1].buyers} {hoc[1].outcome}</span>}
            </span>
            <span className="muted" style={{ fontSize: 10.5 }}>· open to see all markets &amp; trades</span>
          </div>
        )}
      </div>
      {open && (
        <div style={{ background: 'var(--bg-2)' }}>
          {markets.map((mk, i) => (
            <React.Fragment key={mk.condition_id}>
              {mk.resolved && (i === 0 || !markets[i - 1].resolved) && (
                <div className="muted" style={{ padding: '7px 14px 3px 38px', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.6 }}>
                  Finished
                </div>
              )}
              <SubMarketRow mk={mk} openMarket={openMarket} openWallet={openWallet} />
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

function MatchStat({ label, value, accent }) {
  return (
    <div style={{ flex: 1 }}>
      <div className="muted" style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div className="mono" style={{ fontSize: 19, fontWeight: 600, color: accent }}>{value}</div>
    </div>
  );
}

function MatchesView({ followOnly, game, openMarket, openWallet }) {
  let path = `/esports/matches?window=400`;
  if (followOnly) path += '&follow_only=true';
  if (game && game !== 'all') path += `&game=${game}`;
  const res = useApi(path, { matches: [] }, { pollMs: 15000 });
  const d = res.data;
  const matches = d?.matches || [];
  const offline = res.source === 'mock';
  const sb = useApi(`/esports/scoreboard${game && game !== 'all' ? `?game=${game}` : ''}`, null, { pollMs: 30000 }).data;
  const hasScore = sb && sb.resolved_markets > 0;

  if (res.loading && matches.length === 0) {
    return <div className="card card-pad muted">Loading matches…</div>;
  }
  if (matches.length === 0) {
    return (
      <div className="card card-pad muted" style={{ lineHeight: 1.6 }}>
        {offline
          ? '⚠ Backend offline — start polybot to read the tracker.'
          : 'No sharp activity grouped yet. Once your sharps trade an esports market, matches show up here with the consensus lean.'}
      </div>
    );
  }
  return (
    <>
      <div className="card" style={{ display: 'flex', gap: 8, padding: '12px 16px', marginBottom: 14 }}>
        <MatchStat label="Live matches" value={d.live_count ?? 0} accent="var(--yes)" />
        <MatchStat label="Sharps active" value={d.sharps_active ?? 0} />
        <MatchStat label="Sharp money in" value={fmtUSD(d.notional || 0)} />
        {hasScore && (
          <div style={{ flex: 1 }} title={`Forward-test: of ${sb.resolved_markets} resolved markets with a consensus, the lean side won ${sb.consensus_correct}.`}>
            <div className="muted" style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.5 }}>Consensus hit rate</div>
            <div className="mono" style={{ fontSize: 19, fontWeight: 600 }}>
              {(sb.hit_rate * 100).toFixed(0)}% <span className="muted" style={{ fontSize: 12 }}>{sb.consensus_correct}/{sb.resolved_markets}</span>
            </div>
          </div>
        )}
        {hasScore && sb.avg_follow_pnl != null && (
          <div style={{ flex: 1 }} title="Avg P&L per $1 if you'd followed the consensus at the price you'd have paid, on resolved markets.">
            <div className="muted" style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.5 }}>Follow P&amp;L /$1</div>
            <div className={`mono ${sb.avg_follow_pnl >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 19, fontWeight: 600 }}>
              {sb.avg_follow_pnl >= 0 ? '+' : ''}{(sb.avg_follow_pnl * 100).toFixed(0)}%
            </div>
          </div>
        )}
      </div>
      {offline && <div className="card-pad muted" style={{ fontSize: 12 }}>⚠ Backend offline — showing cached/empty data.</div>}
      {matches.map(m => (
        <MatchCard key={m.match_key} m={m} openMarket={openMarket} openWallet={openWallet} />
      ))}
    </>
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
              <td className="num muted">{a.notional != null ? fmtUSD(a.notional) : '—'}</td>
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

// Real tracker-liveness ring: the arc fills toward the next expected poll, and
// the WHOLE thing is data-driven — green/cycling when healthy, amber when a pass
// lags or wallet polls fail, red "!" and stopped when the tracker stalls or is
// down. Polls /esports/health (cheap) and ticks locally between fetches.
function PollRing() {
  const res = useApi('/esports/health', null, { pollMs: 4000 });
  const h = res.data;
  const anchor = useRef({ at: 0, age: 0 });
  const [, force] = useState(0);
  useEffect(() => {
    if (h && h.age_seconds != null) anchor.current = { at: Date.now(), age: h.age_seconds };
  }, [h?.age_seconds, h?.cycles]);
  useEffect(() => {
    const id = setInterval(() => force(t => t + 1), 250);
    return () => clearInterval(id);
  }, []);

  const status = h?.status || (res.loading ? 'loading' : 'down');
  const cs = h?.cycle_seconds || 8;
  const liveAge = (h && h.age_seconds != null) ? anchor.current.age + (Date.now() - anchor.current.at) / 1000 : 0;
  const dead = status === 'down' || status === 'stale';
  const progress = dead ? 1 : Math.max(0, Math.min(1, liveAge / cs));
  const color = status === 'ok' ? 'var(--yes)'
    : (status === 'lagging' || status === 'error') ? 'var(--amber)'
    : status === 'loading' ? 'var(--text-4)' : 'var(--no)';
  const label = { ok: 'live', lagging: 'lagging', error: 'errors', loading: '…', down: 'offline', stale: 'stalled' }[status] || status;
  const tip = status === 'down' ? 'Tracker not running — launch polybot.'
    : status === 'stale' ? `Tracker stalled — no poll in ${Math.round(liveAge)}s. Check the polybot console.`
    : status === 'error' ? `${h.errors_last_cycle}/${h.wallets} wallet polls failing${h.last_error ? ' · ' + h.last_error : ''}`
    : status === 'lagging' ? `Last pass took ${(h.last_cycle_ms / 1000).toFixed(1)}s (interval ${cs}s)`
    : status === 'ok' ? `Polling every ${cs}s · ${h.cycles} cycles · last poll ${Math.round(liveAge)}s ago`
    : 'Connecting…';
  const R = 8.5, C = 2 * Math.PI * R;
  return (
    <div title={tip} style={{ display: 'flex', alignItems: 'center', gap: 7, flexShrink: 0 }}>
      <svg width="22" height="22" viewBox="0 0 22 22" style={{ display: 'block' }}>
        <circle cx="11" cy="11" r={R} fill="none" stroke="var(--border)" strokeWidth="2.4" />
        <circle cx="11" cy="11" r={R} fill="none" stroke={color} strokeWidth="2.4"
          strokeDasharray={C} strokeDashoffset={C * (1 - progress)} strokeLinecap="round"
          transform="rotate(-90 11 11)" />
        {dead && <text x="11" y="15.5" textAnchor="middle" fontSize="13" fontWeight="700" fill={color}>!</text>}
      </svg>
      <span style={{ fontSize: 11, fontWeight: 500, color: status === 'ok' ? 'var(--yes)' : dead ? 'var(--no)' : 'var(--text-3)' }}>{label}</span>
    </div>
  );
}

function EsportsPage({ openMarket }) {
  const [followOnly, setFollowOnly] = useState(false);
  const [game, setGame] = useState('all');
  const [type, setType] = useState('all');
  const [tab, setTab] = useState('matches');
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
        <div style={{ marginLeft: 'auto' }}><PollRing /></div>
      </div>
      <div className="content">
        <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="segmented green">
            <button className={tab === 'matches' ? 'on' : ''} onClick={() => setTab('matches')}>Live matches</button>
            <button className={tab === 'feed' ? 'on' : ''} onClick={() => setTab('feed')}>Feed</button>
            <button className={tab === 'watch' ? 'on' : ''} onClick={() => setTab('watch')}>Watchlist</button>
          </div>
          {(tab === 'matches' || tab === 'feed') && (
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
            </>
          )}
          {tab === 'feed' && (
            <>
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
        {tab === 'matches'
          ? <MatchesView followOnly={followOnly} game={game} openMarket={openMarket} openWallet={setWalletModal} />
          : tab === 'feed'
          ? <EsportsFeed followOnly={followOnly} game={game} type={type} openMarket={openMarket} openWallet={setWalletModal} />
          : <EsportsWatchlist openWallet={setWalletModal} />}
      </div>
      {walletModal && (
        <EsportsWalletModal wallet={walletModal} onClose={() => setWalletModal(null)} openMarket={openMarket} />
      )}
    </>
  );
}
