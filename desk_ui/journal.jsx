// Journal.jsx — futures trade journal: stats, table, detail

function computeStats(trades) {
  const n = trades.length;
  const wins = trades.filter(t => t.pnl > 0);
  const losses = trades.filter(t => t.pnl < 0);
  const winRate = n ? Math.round((wins.length / n) * 100) : 0;
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
  const avgR = n ? trades.reduce((s, t) => s + t.r, 0) / n : 0;
  const grossWin = wins.reduce((s, t) => s + t.pnl, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
  const pf = grossLoss ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0;
  return { n, winCount: wins.length, lossCount: losses.length, winRate, totalPnl, avgR, pf };
}

function StatTiles({ trades }) {
  const s = computeStats(trades);
  return (
    <div className="stat-row">
      <div className="stat">
        <div className="k"><Icon name="dollar" /> Net P&L</div>
        <div className={`v ${s.totalPnl >= 0 ? 'up' : 'down'}`}>{fmtMoney(s.totalPnl)}</div>
        <div className="meta">{s.n} trades · last 10 sessions</div>
      </div>
      <div className="stat">
        <div className="k"><Icon name="target" /> Win rate</div>
        <div className="v">{s.winRate}<span style={{ fontSize: 18, color: 'var(--fg-3)' }}>%</span></div>
        <div className="wrbar"><i style={{ width: s.winRate + '%' }} /></div>
        <div className="meta">{s.winCount}W · {s.lossCount}L</div>
      </div>
      <div className="stat">
        <div className="k"><Icon name="gauge" /> Avg R</div>
        <div className={`v ${s.avgR >= 0 ? 'up' : 'down'}`}>{fmtR(s.avgR)}</div>
        <div className="meta">per trade, risk-adjusted</div>
      </div>
      <div className="stat">
        <div className="k"><Icon name="layers" /> Profit factor</div>
        <div className="v">{s.pf === Infinity ? '∞' : s.pf.toFixed(2)}</div>
        <div className="spark">
          {trades.map((t, i) => <i key={i} className={t.pnl >= 0 ? 'w' : 'l'} style={{ height: Math.max(2, Math.min(22, Math.abs(t.r) * 6)) + 'px' }} />)}
        </div>
      </div>
    </div>
  );
}

function JournalScreen({ trades, onOpen, onLog }) {
  const [setup, setSetup] = React.useState('all');
  const setups = React.useMemo(() => ['all', ...Array.from(new Set(trades.map(t => t.setup)))], [trades]);
  const shown = setup === 'all' ? trades : trades.filter(t => t.setup === setup);

  return (
    <div className="content-inner wide">
      <div className="page-head">
        <div>
          <div className="ttl">Journal</div>
          <div className="sub">Futures · executed trades, logged honestly</div>
        </div>
        <button className="btn primary" onClick={onLog}><Icon name="plus" /> Log trade</button>
      </div>

      <StatTiles trades={trades} />

      <div className="notes-toolbar">
        <div className="filters">
          {setups.map(s => (
            <button key={s} className={`chip ${setup === s ? 'active' : ''}`} onClick={() => setSetup(s)}>
              {s === 'all' ? 'All setups' : s}
            </button>
          ))}
        </div>
      </div>

      <div className="table j-table">
        <div className="thead">
          <span>Contract</span>
          <span>Side</span>
          <span>Setup</span>
          <span className="td-r">Entry</span>
          <span className="td-r">Exit</span>
          <span className="td-r">R</span>
          <span className="td-r">P&L</span>
        </div>
        {shown.length === 0 && (
          <div className="trow" style={{ cursor: 'default' }}>
            <span className="muted" style={{ gridColumn: '1 / -1', padding: '6px 0' }}>
              {trades.length === 0 ? 'No trades logged yet — hit “Log trade” to add your first.' : 'No trades for this setup.'}
            </span>
          </div>
        )}
        {shown.map(t => (
          <div key={t.id} className="trow" onClick={() => onOpen(t)}>
            <span>
              <span className="cell-sym">{t.sym}</span>
              <span className="cell-sub" style={{ display: 'block', marginTop: 1 }}>{t.date}</span>
            </span>
            <span><DirStamp dir={t.dir} /></span>
            <span className="cell-sub" style={{ fontSize: 12.5, color: 'var(--fg-2)' }}>{t.setup}</span>
            <span className="td-r cell-mono">{fmtPx(t.entry)}</span>
            <span className="td-r cell-mono">{fmtPx(t.exit)}</span>
            <span className={`td-r cell-mono ${t.r >= 0 ? 'up' : 'down'}`}>{fmtR(t.r)}</span>
            <span className={`td-r cell-pnl ${t.pnl >= 0 ? 'up' : 'down'}`}>{fmtMoney(t.pnl)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Trade detail panel ──
function TradeDetail({ trade, onClose, onEdit }) {
  React.useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="panel" role="dialog" aria-label={`${trade.sym} trade`}>
        <div className="panel-head">
          <div>
            <div className="detail-tkr">
              <span className="sym">{trade.sym}</span>
              <span className="co">{trade.name}</span>
            </div>
            <div className="rowflex" style={{ marginTop: 10, gap: 8 }}>
              <DirStamp dir={trade.dir} />
              <Stamp tone="brown">{trade.setup}</Stamp>
            </div>
          </div>
          <button className="iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>

        <div className="panel-body">
          <div className="pricebox" style={{ background: trade.pnl >= 0 ? 'var(--gain-soft)' : 'var(--loss-soft)', borderColor: trade.pnl >= 0 ? 'var(--gain-line)' : 'var(--loss-line)' }}>
            <div className="k">Realized P&L</div>
            <div className={`v ${trade.pnl >= 0 ? 'up' : 'down'}`} style={{ fontSize: 34, fontFamily: 'var(--font-mono)', fontWeight: 600, letterSpacing: '-0.02em', marginTop: 4 }}>{fmtMoney(trade.pnl)}</div>
            <div className="d">
              <span className={trade.r >= 0 ? 'up' : 'down'}>{fmtR(trade.r)}</span>
              <span className="muted">  ·  {trade.ticks > 0 ? '+' : '−'}{Math.abs(trade.ticks)} ticks  ·  {trade.size} contract{trade.size > 1 ? 's' : ''}</span>
            </div>
          </div>

          <div className="tradeline">
            <div className="seg2"><span className="k">Entry</span><span className="v">{fmtPx(trade.entry)}</span></div>
            <div className="seg2"><span className="k">Exit</span><span className="v">{fmtPx(trade.exit)}</span></div>
            <div className="seg2"><span className="k">Held</span><span className="v">{trade.dur}</span></div>
          </div>

          <div className="block">
            <span className="label">Session</span>
            <div className="lvl-list">
              <div className="lvl-row"><span className="nm"><Icon name="calendar" className="ico" style={{ width: 15, height: 15, color: 'var(--fg-3)' }} /> Date</span><span className="vl">{trade.date} · {trade.time}</span></div>
              <div className="lvl-row"><span className="nm"><Icon name="hash" className="ico" style={{ width: 15, height: 15, color: 'var(--fg-3)' }} /> Size</span><span className="vl">{trade.size} × {trade.sym}</span></div>
            </div>
          </div>

          <div className="block">
            <span className="label">Reflection</span>
            <div className="reflect">
              <div className="col good">
                <div className="h"><Icon name="check-circle" /> What I did well</div>
                <p>{trade.wentWell}</p>
              </div>
              <div className="col bad">
                <div className="h"><Icon name="rotate" /> What I'd change</div>
                <p>{trade.wouldChange}</p>
              </div>
            </div>
          </div>
        </div>

        <div className="panel-foot">
          <button className="btn secondary" onClick={() => onEdit(trade)}><Icon name="pencil" /> Edit entry</button>
        </div>
      </aside>
    </>
  );
}

// Common futures contracts → full name (auto-fills the detail panel's subtitle).
const CONTRACTS = {
  ES: 'E-mini S&P 500', MES: 'Micro E-mini S&P 500', NQ: 'E-mini Nasdaq 100',
  MNQ: 'Micro Nasdaq 100', YM: 'E-mini Dow', RTY: 'E-mini Russell 2000',
  CL: 'Crude Oil', MCL: 'Micro Crude Oil', NG: 'Natural Gas',
  GC: 'Gold', MGC: 'Micro Gold', SI: 'Silver',
  ZB: '30Y T-Bond', ZN: '10Y T-Note', '6E': 'Euro FX', BTC: 'Bitcoin Futures',
};

// ── Log-trade modal ──
function LogTradeModal({ trade, onClose, onSave }) {
  const editing = !!trade;
  const today = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const str = (v) => (v != null ? String(v) : '');
  const [sym, setSym] = React.useState(trade?.sym || '');
  const [dir, setDir] = React.useState(trade?.dir || 'long');
  const [setup, setSetup] = React.useState(trade && trade.setup !== '—' ? (trade.setup || '') : '');
  const [entry, setEntry] = React.useState(str(trade?.entry));
  const [exit, setExit] = React.useState(str(trade?.exit));
  const [size, setSize] = React.useState(trade?.size != null ? String(trade.size) : '1');
  const [pnl, setPnl] = React.useState(str(trade?.pnl));
  const [r, setR] = React.useState(str(trade?.r));
  const [date, setDate] = React.useState(trade?.date || today);
  const [time, setTime] = React.useState(trade?.time || '');
  const [dur, setDur] = React.useState(trade?.dur || '');
  const [well, setWell] = React.useState(trade?.wentWell || '');
  const [change, setChange] = React.useState(trade?.wouldChange || '');
  const symRef = React.useRef(null);

  React.useEffect(() => { symRef.current && symRef.current.focus(); }, []);
  React.useEffect(() => {
    const h = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });

  const num = (v) => { const n = parseFloat(String(v).replace(/[^0-9.\-]/g, '')); return isNaN(n) ? null : n; };
  const valid = sym.trim().length > 0;
  function submit() {
    if (!valid) return;
    const s = sym.trim().toUpperCase();
    const sz = num(size);
    const base = {
      sym: s, name: CONTRACTS[s] || (editing ? (trade.name || '') : ''), dir, setup: setup.trim() || '—',
      entry: num(entry), exit: num(exit), size: sz != null ? Math.round(sz) : null,
      ticks: editing ? (trade.ticks ?? null) : null, pnl: num(pnl) || 0, r: num(r) || 0,
      date: date.trim() || today, time: time.trim(), dur: dur.trim(),
      wentWell: well.trim(), wouldChange: change.trim(),
    };
    // Editing keeps the id (app PATCHes); new trade has none (app POSTs).
    onSave(editing ? { ...trade, ...base } : base);
  }
  const dirBtn = (key, label) => (
    <button className={dir === key ? `on ${key}` : ''} onClick={() => setDir(key)}>{label}</button>
  );

  return (
    <div className="modal" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="box">
        <div className="modal-head">
          <Icon name="journal" />
          <span className="t">{editing ? 'Edit trade' : 'Log a trade'}</span>
          <button className="iconbtn esc" onClick={onClose}><Icon name="x" /></button>
        </div>
        <div className="modal-body">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div className="field tkr-input">
              <label>Contract</label>
              <input ref={symRef} list="contract-list" value={sym} maxLength={6}
                onChange={(e) => setSym(e.target.value.toUpperCase())} placeholder="ES" />
              <datalist id="contract-list">{Object.keys(CONTRACTS).map(c => <option key={c} value={c} />)}</datalist>
            </div>
            <div className="field">
              <label>Side</label>
              <div className="seg dir">{dirBtn('long', 'Long')}{dirBtn('short', 'Short')}</div>
            </div>
          </div>
          <div className="field">
            <label>Setup</label>
            <input value={setup} onChange={(e) => setSetup(e.target.value)} placeholder="Opening range breakout" />
          </div>
          <div className="field">
            <label>Entry · Exit · Size</label>
            <div className="row3">
              <input className="mono" value={entry} onChange={(e) => setEntry(e.target.value)} placeholder="Entry" inputMode="decimal" />
              <input className="mono" value={exit} onChange={(e) => setExit(e.target.value)} placeholder="Exit" inputMode="decimal" />
              <input className="mono" value={size} onChange={(e) => setSize(e.target.value)} placeholder="Size" inputMode="numeric" />
            </div>
          </div>
          <div className="field">
            <label>P&L ($) · R multiple</label>
            <div className="row2">
              <input className="mono" value={pnl} onChange={(e) => setPnl(e.target.value)} placeholder="+1825 or −640" inputMode="decimal" />
              <input className="mono" value={r} onChange={(e) => setR(e.target.value)} placeholder="2.4 or −1.0" inputMode="decimal" />
            </div>
          </div>
          <div className="field">
            <label>Date · Time · Held</label>
            <div className="row3">
              <input value={date} onChange={(e) => setDate(e.target.value)} placeholder="May 28" />
              <input value={time} onChange={(e) => setTime(e.target.value)} placeholder="9:38am" />
              <input value={dur} onChange={(e) => setDur(e.target.value)} placeholder="47m" />
            </div>
          </div>
          <div className="field">
            <label>What I did well</label>
            <textarea value={well} onChange={(e) => setWell(e.target.value)} placeholder="Waited for confirmation, sized right, trailed the runner…" />
          </div>
          <div className="field">
            <label>What I'd change</label>
            <textarea value={change} onChange={(e) => setChange(e.target.value)} placeholder="Took profit early, chased the entry…" />
          </div>
        </div>
        <div className="modal-foot">
          <span className="hint">⌘↵ to save</span>
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!valid} onClick={submit}><Icon name="check" /> {editing ? 'Save changes' : 'Save trade'}</button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { JournalScreen, TradeDetail, LogTradeModal, computeStats });
