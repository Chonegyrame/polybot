// =============================================================
// news.jsx — News tab: 3-card overview + inline expand
// =============================================================
//
// Three stat cards in a row at the top, each shows just the count for its
// feed. Clicking a card toggles the matching full list inline below; only
// one section is open at a time. Default open = "New signals".
//
//   1. New signals  · last 24h  -- /signals/recent?hours=24
//   2. Exits/trims  · last 72h  -- /signals/exits/recent?hours=72
//   3. Lost signals · last 72h  -- /signals/lost?hours=72
//
// Polls every 60s on all three feeds via one App-level hook (useNewsBadge),
// so the sidebar badge and the page share a single timer. Dismissal of
// lost signals is client-side only (localStorage); other feeds have no
// dismiss concept.

const NEWS_LAST_SEEN_KEY = 'news_last_seen_at';
const NEWS_DISMISSED_KEY = 'news_dismissed_lost_ids';

function _readDismissedSet() {
  try {
    const arr = JSON.parse(localStorage.getItem(NEWS_DISMISSED_KEY) || '[]');
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function _writeDismissedSet(set) {
  try {
    localStorage.setItem(NEWS_DISMISSED_KEY, JSON.stringify([...set]));
  } catch {}
}

function _readLastSeen() {
  return localStorage.getItem(NEWS_LAST_SEEN_KEY) || null;
}

function _writeLastSeenNow() {
  localStorage.setItem(NEWS_LAST_SEEN_KEY, new Date().toISOString());
}

// One shared hook — App calls this and passes the result down to both Sidebar
// (for the badge count) and NewsPage (for the full data). Avoids two poll
// intervals running concurrently when the user is on the News tab.
function useNewsBadge() {
  const recentRes = useApi('/signals/recent?hours=24', null, { pollMs: 60_000 });
  const lostRes   = useApi('/signals/lost?hours=72',   null, { pollMs: 60_000 });
  const exitsRes  = useApi('/signals/exits/recent?hours=72', null, { pollMs: 60_000 });
  // Re-render trigger so dismiss/markRead are reflected without external state.
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick(x => x + 1), []);

  const recentSignals = recentRes.data?.recent_signals || [];
  const lostSignals = lostRes.data?.lost_signals || [];
  const exits = exitsRes.data?.exits || [];
  const dismissed = _readDismissedSet();
  const lastSeen = _readLastSeen();
  const lastSeenMs = lastSeen ? new Date(lastSeen).getTime() : 0;

  // Unread = sum across all three feeds of items newer than lastSeen.
  // Dismissed lost signals don't count toward unread.
  let unread = 0;
  for (const r of recentSignals) {
    const t = r.first_fired_at ? new Date(r.first_fired_at).getTime() : 0;
    if (t > lastSeenMs) unread++;
  }
  for (const e of exits) {
    const t = e.exited_at ? new Date(e.exited_at).getTime() : 0;
    if (t > lastSeenMs) unread++;
  }
  for (const r of lostSignals) {
    if (dismissed.has(r.signal_log_id)) continue;
    const t = r.last_seen_at ? new Date(r.last_seen_at).getTime() : 0;
    if (t > lastSeenMs) unread++;
  }

  // Card counts: post-dedup row counts users would see if they expanded.
  // ExitsCard dedupes by (cid, direction) keeping the most-recent exited_at;
  // mirror that here so the card number matches the list length below.
  const exitsDedupKey = new Set();
  let exitsCount = 0;
  const exitsSorted = [...exits].sort(
    (a,b) => new Date(b.exited_at||0) - new Date(a.exited_at||0),
  );
  for (const r of exitsSorted) {
    const k = `${r.condition_id}-${r.direction}`;
    if (exitsDedupKey.has(k)) continue;
    exitsDedupKey.add(k);
    exitsCount++;
  }
  // Lost-signals card count excludes dismissed rows (matches what user sees
  // before they expand the "Show N dismissed" details block).
  const lostVisibleCount = lostSignals.filter(r => !dismissed.has(r.signal_log_id)).length;

  return {
    recentSignals,
    exits,
    lostSignals,
    recentLoading: recentRes.loading,
    exitsLoading: exitsRes.loading,
    lostLoading: lostRes.loading,
    recentError: recentRes.error,
    exitsError: exitsRes.error,
    lostError: lostRes.error,
    counts: {
      recent: recentSignals.length,
      exits: exitsCount,
      lost: lostVisibleCount,
    },
    unread,
    dismissed,
    dismiss(signalLogId) {
      const s = _readDismissedSet();
      s.add(signalLogId);
      _writeDismissedSet(s);
      bump();
    },
    undismiss(signalLogId) {
      const s = _readDismissedSet();
      s.delete(signalLogId);
      _writeDismissedSet(s);
      bump();
    },
    markRead() {
      _writeLastSeenNow();
      bump();
    },
    _tick: tick,
  };
}

function NewsPage({ feed, openMarket }) {
  // Default-open: New signals. State is page-local — switching tab and back
  // resets to default, which is fine ("New" is what the user wants 95% of
  // the time per the design discussion).
  const [expanded, setExpanded] = useState('recent');

  // Mark-read on mount AND whenever any feed updates while we're on the page.
  // Without the feed deps, a new item arriving via the 60s poll would bump
  // the badge to 1 even though the user is staring at the page.
  useEffect(() => {
    feed.markRead();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.recentSignals, feed.exits, feed.lostSignals]);

  return (
    <>
      <div className="topbar">
        <div>
          <h1>News</h1>
          <div className="topbar-sub">
            new arrivals · smart-money activity · signals that rolled off · auto-refresh 60s
          </div>
        </div>
      </div>
      <div className="content">
        <div style={{display:'grid',gridTemplateColumns:'repeat(3, 1fr)',gap:14,marginBottom:18}}>
          <StatCard
            title="New signals"
            windowLabel="last 24h"
            count={feed.counts.recent}
            loading={feed.recentLoading}
            active={expanded === 'recent'}
            onClick={() => setExpanded('recent')}
            tone="ok"
          />
          <StatCard
            title="Exits / trims"
            windowLabel="last 72h"
            count={feed.counts.exits}
            loading={feed.exitsLoading}
            active={expanded === 'exits'}
            onClick={() => setExpanded('exits')}
            tone="warn"
          />
          <StatCard
            title="Lost signals"
            windowLabel="last 72h"
            count={feed.counts.lost}
            loading={feed.lostLoading}
            active={expanded === 'lost'}
            onClick={() => setExpanded('lost')}
            tone="bad"
          />
        </div>
        <div>
          {expanded === 'recent' && (
            <NewSignalsCard
              rows={feed.recentSignals}
              loading={feed.recentLoading}
              error={feed.recentError}
              openMarket={openMarket}
            />
          )}
          {expanded === 'exits' && (
            <ActivityCard
              exits={feed.exits}
              loading={feed.exitsLoading}
              error={feed.exitsError}
              openMarket={openMarket}
            />
          )}
          {expanded === 'lost' && (
            <LostSignalsCard
              rows={feed.lostSignals.filter(r => !feed.dismissed.has(r.signal_log_id))}
              dismissedRows={feed.lostSignals.filter(r => feed.dismissed.has(r.signal_log_id))}
              loading={feed.lostLoading}
              error={feed.lostError}
              openMarket={openMarket}
              onDismiss={feed.dismiss}
              onUndismiss={feed.undismiss}
            />
          )}
        </div>
      </div>
    </>
  );
}

function StatCard({ title, windowLabel, count, loading, active, onClick, tone }) {
  // Active state = thicker accent border + slight bg lift, so the user can
  // tell at a glance which section is showing below. Tone colors the count
  // text via inline style (CSS vars from the theme), since .pos/.neg in
  // styles.css are scoped to specific containers and won't apply here.
  const toneColor = tone === 'ok'  ? 'var(--accent)'
                  : tone === 'bad' ? 'var(--no)'
                  : tone === 'warn' ? 'var(--amber)'
                  : 'inherit';
  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        cursor:'pointer',
        padding:'14px 16px',
        border: active ? '1px solid var(--accent)' : '1px solid var(--border)',
        background: active ? 'var(--accent-soft)' : undefined,
        transition:'border-color 120ms, background 120ms',
      }}
    >
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',gap:8}}>
        <div style={{fontSize:13,fontWeight:600}}>{title}</div>
        <div className="muted mono" style={{fontSize:11}}>{windowLabel}</div>
      </div>
      <div style={{display:'flex',alignItems:'baseline',gap:10,marginTop:6}}>
        <div style={{fontSize:28,fontWeight:600,lineHeight:1,color:toneColor}}>
          {loading && count === 0 ? '…' : count}
        </div>
        <div className="muted" style={{fontSize:11}}>
          {active ? 'showing below' : 'tap to view'}
        </div>
      </div>
    </div>
  );
}

function NewSignalsCard({ rows, loading, error, openMarket }) {
  // Already DESC by first_fired_at from the backend. No client-side dedup —
  // /signals/recent already returns one row per (cid, direction).
  return (
    <div className="card">
      <div className="card-head">
        <h3>New signals · last 24h</h3>
        <span className="muted mono" style={{fontSize:11}}>GET /signals/recent?hours=24</span>
      </div>
      <div className="muted" style={{fontSize:12,padding:'4px 14px 10px'}}>
        Signals whose very first fire landed in the last 24 hours, across
        any mode / category / top-N. Newest at the top.
      </div>
      {loading && rows.length === 0 && (
        <div className="card-pad muted">Loading new signals…</div>
      )}
      {!loading && rows.length === 0 && (
        <div className="card-pad muted">
          {error
            ? `Backend offline (${error}). New signals will appear here when reachable.`
            : 'No new signals in the last 24 hours.'}
        </div>
      )}
      <div>
        {rows.map(r => (
          <NewSignalRow key={`${r.condition_id}-${r.direction}`} r={r} openMarket={openMarket}/>
        ))}
      </div>
    </div>
  );
}

function NewSignalRow({ r, openMarket }) {
  // Group the fired_in combos by mode so the line stays short on cards that
  // fire on many combos. e.g. "absolute (sports, overall) + hybrid (sports)".
  const byMode = {};
  for (const c of (r.fired_in || [])) {
    if (!byMode[c.mode]) byMode[c.mode] = new Set();
    byMode[c.mode].add(c.category);
  }
  const firedInText = Object.entries(byMode)
    .map(([mode, cats]) => `${mode} (${[...cats].join(', ')})`)
    .join(' + ');

  // State chip: still firing somewhere, or already rolled off / exited.
  let stateChip = null;
  if (r.is_still_firing) {
    stateChip = <span className="chip ok" style={{minWidth:64,textAlign:'center'}}>FIRING</span>;
  } else if (r.last_exit_event_type === 'exit') {
    stateChip = <span className="chip bad" style={{minWidth:64,textAlign:'center'}}>EXITED</span>;
  } else if (r.last_exit_event_type === 'trim') {
    stateChip = <span className="chip warn" style={{minWidth:64,textAlign:'center'}}>TRIMMED</span>;
  } else {
    stateChip = <span className="chip" style={{minWidth:64,textAlign:'center'}}>ROLLED OFF</span>;
  }

  return (
    <div
      onClick={() => openMarket(r.condition_id, r.direction)}
      style={{display:'flex',alignItems:'center',gap:12,padding:'12px 14px',borderTop:'1px solid var(--border)',cursor:'pointer'}}
    >
      {stateChip}
      <div style={{flex:1, minWidth:0}}>
        <div style={{fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          <b>{r.market_question || (r.condition_id ? r.condition_id.slice(0,12)+'…' : '?')}</b>
          {' · '}
          <span className={`mono ${r.direction==='YES'?'pos':'neg'}`}>{r.direction}</span>
        </div>
        <div className="muted mono" style={{fontSize:11,marginTop:2}}>
          peak {r.peak_trader_count ?? '?'} traders · {fmtUSD(r.peak_aggregate_usdc)}
          {r.recent_cur_price != null ? ` · price $${Number(r.recent_cur_price).toFixed(2)}` : ''}
          {firedInText ? ` · fires on ${firedInText}` : ''}
        </div>
      </div>
      <div className="muted mono" style={{fontSize:11,whiteSpace:'nowrap',textAlign:'right'}}>
        <div>{r.first_fired_at ? tsAgo(r.first_fired_at) : ''}</div>
        <div style={{fontSize:10,opacity:0.7,marginTop:2}}>
          {r.first_fired_at ? new Date(r.first_fired_at).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : ''}
        </div>
      </div>
    </div>
  );
}

function ActivityCard({ exits, loading, error, openMarket }) {
  // Dedup: signal_exits fires once per (mode, category, top_n) combo, so the
  // same market exit shows up 3+ times. Collapse to one row per (condition_id,
  // direction), keeping the most-recent exited_at.
  const sorted = [...exits].sort(
    (a,b) => new Date(b.exited_at||0) - new Date(a.exited_at||0),
  );
  const seen = new Set();
  const rows = [];
  for (const r of sorted) {
    const key = `${r.condition_id}-${r.direction}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(r);
  }
  return (
    <div className="card">
      <div className="card-head">
        <h3>Exits / trims · last 72h</h3>
        <span className="muted mono" style={{fontSize:11}}>GET /signals/exits/recent?hours=72</span>
      </div>
      {loading && rows.length === 0 && (
        <div className="card-pad muted">Loading recent activity…</div>
      )}
      {!loading && rows.length === 0 && (
        <div className="card-pad muted">
          {error
            ? `Backend offline (${error}). Smart-money trims/exits and paper-trade resolutions will appear here when reachable.`
            : 'No activity in the last 72 hours. Smart-money trims/exits will appear here.'}
        </div>
      )}
      <div>
        {rows.map(r => <ExitRow key={r.exit_id} r={r} openMarket={openMarket}/>)}
      </div>
    </div>
  );
}

function ExitRow({ r, openMarket }) {
  // /signals/exits/recent doesn't currently return event_type per row, so we
  // infer the tier from the drop magnitude: trader_count or aggregate dropping
  // 50%+ = exit (red), 25-50% = trim (amber).
  const traderRetention = r.peak_trader_count > 0
    ? r.exit_trader_count / r.peak_trader_count : 0;
  const aggRetention = r.peak_aggregate_usdc > 0
    ? r.exit_aggregate_usdc / r.peak_aggregate_usdc : 0;
  const isExit = traderRetention <= 0.5 || aggRetention <= 0.5;
  const chipText = isExit ? 'EXIT' : 'TRIM';
  const chipKind = isExit ? 'bad' : 'warn';
  const dropFracTraders = 1 - traderRetention;
  const dropFracAgg = 1 - aggRetention;
  return (
    <div
      onClick={() => openMarket(r.condition_id, r.direction)}
      style={{display:'flex',alignItems:'center',gap:12,padding:'12px 14px',borderTop:'1px solid var(--border)',cursor:'pointer'}}
    >
      <span className={`chip ${chipKind}`} style={{minWidth:48,textAlign:'center'}}>{chipText}</span>
      <div style={{flex:1, minWidth:0}}>
        <div style={{fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          <b>{r.market_question || (r.condition_id ? r.condition_id.slice(0,12)+'…' : '?')}</b>
          {' · '}
          <span className={`mono ${r.direction==='YES'?'pos':'neg'}`}>{r.direction}</span>
        </div>
        <div className="muted mono" style={{fontSize:11,marginTop:2}}>
          traders {r.peak_trader_count} → {r.exit_trader_count} ({Math.round(dropFracTraders*100)}% drop) · aggregate {Math.round(dropFracAgg*100)}% drop
          {r.exit_bid_price != null ? ` · bid $${Number(r.exit_bid_price).toFixed(2)}` : ''}
        </div>
      </div>
      <div className="muted mono" style={{fontSize:11,whiteSpace:'nowrap'}}>
        {r.exited_at ? tsAgo(r.exited_at) : ''}
      </div>
    </div>
  );
}

function LostSignalsCard({ rows, dismissedRows, loading, error, openMarket, onDismiss, onUndismiss }) {
  // Bubble rows with an open paper trade to the top, then sort by recency.
  const sorted = [...rows].sort((a,b) => {
    const ap = a.open_paper_trade_id ? 1 : 0;
    const bp = b.open_paper_trade_id ? 1 : 0;
    if (ap !== bp) return bp - ap;
    return new Date(b.last_seen_at||0) - new Date(a.last_seen_at||0);
  });
  return (
    <div className="card">
      <div className="card-head">
        <h3>Lost signals · last 72h</h3>
        <span className="muted mono" style={{fontSize:11}}>
          GET /signals/lost?hours=72{dismissedRows.length ? ` · dismissed: ${dismissedRows.length}` : ''}
        </span>
      </div>
      <div className="muted" style={{fontSize:12,padding:'4px 14px 10px'}}>
        Signals that fired in the last 72 hours but stopped firing on every
        (mode, category, top-N) combo. Auto-purges after 3 days. Dismiss to
        hide a row from this view.
      </div>
      {loading && sorted.length === 0 && dismissedRows.length === 0 && (
        <div className="card-pad muted">Loading lost signals…</div>
      )}
      {!loading && sorted.length === 0 && dismissedRows.length === 0 && (
        <div className="card-pad muted">
          {error
            ? `Backend offline (${error}). Lost signals will appear here when reachable.`
            : 'No signals have rolled off in the last 72 hours.'}
        </div>
      )}
      <div>
        {sorted.map(r => (
          <LostRow key={r.signal_log_id} r={r} openMarket={openMarket} onDismiss={onDismiss}/>
        ))}
        {dismissedRows.length > 0 && (
          <details style={{borderTop:'1px solid var(--border)',padding:'8px 14px'}}>
            <summary className="muted" style={{fontSize:12,cursor:'pointer'}}>
              Show {dismissedRows.length} dismissed
            </summary>
            <div style={{marginTop:6}}>
              {dismissedRows.map(r => (
                <LostRow
                  key={'d-'+r.signal_log_id}
                  r={r}
                  openMarket={openMarket}
                  onUndismiss={onUndismiss}
                  dismissedRow
                />
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

function LostRow({ r, openMarket, onDismiss, onUndismiss, dismissedRow }) {
  // Color the why-label by category. Resolved/closed = neutral-positive,
  // exited/effectively-resolved = bad, trimmed = warn, unknown = muted.
  let labelTone = 'muted';
  if (r.why_label?.startsWith('Market resolved') || r.why_label === 'Market closed') labelTone = 'ok';
  else if (r.why_label?.startsWith('Effectively') || r.why_label === 'Smart money exited') labelTone = 'bad';
  else if (r.why_label === 'Trimmed below floor') labelTone = 'warn';

  return (
    <div style={{display:'flex',alignItems:'center',gap:12,padding:'12px 14px',borderTop:'1px solid var(--border)',opacity:dismissedRow?0.55:1}}>
      <span className={`chip ${labelTone}`} style={{minWidth:140,textAlign:'center'}} title={r.why_detail || ''}>
        {r.why_label}
      </span>
      <div
        style={{flex:1, minWidth:0, cursor:'pointer'}}
        onClick={()=>openMarket(r.condition_id, r.direction)}
      >
        <div style={{fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          <b>{r.market_question || (r.condition_id ? r.condition_id.slice(0,12)+'…' : '?')}</b>
          {' · '}
          <span className={`mono ${r.direction==='YES'?'pos':'neg'}`}>{r.direction}</span>
          {r.open_paper_trade_id != null && (
            <span className="chip warn" style={{marginLeft:8}}>⚠ OPEN PAPER TRADE</span>
          )}
        </div>
        <div className="muted mono" style={{fontSize:11,marginTop:2}}>
          peak {r.peak_trader_count ?? '?'} traders · {fmtUSD(r.peak_aggregate_usdc)}
          {r.recent_cur_price != null ? ` · last price $${Number(r.recent_cur_price).toFixed(2)}` : ''}
          {r.last_seen_at ? ` · last seen ${tsAgo(r.last_seen_at)}` : ''}
        </div>
        {r.why_detail && (
          <div className="muted" style={{fontSize:11.5,marginTop:2,fontStyle:'italic'}}>{r.why_detail}</div>
        )}
      </div>
      <div style={{display:'flex',gap:6,whiteSpace:'nowrap'}}>
        <button
          className="btn ghost sm"
          onClick={(e)=>{ e.stopPropagation(); openMarket(r.condition_id, r.direction); }}
        >Open market →</button>
        {dismissedRow ? (
          <button className="btn ghost sm" onClick={()=>onUndismiss(r.signal_log_id)}>Undo dismiss</button>
        ) : (
          <button className="btn ghost sm" onClick={()=>onDismiss(r.signal_log_id)}>Dismiss</button>
        )}
      </div>
    </div>
  );
}

window.NewsPage = NewsPage;
window.useNewsBadge = useNewsBadge;
