// =============================================================
// shared.jsx — Sidebar, Topbar, Modal, helpers
// =============================================================
const { useState, useEffect, useRef, useMemo, useCallback } = React;
const D = window.POLYBOT_DATA;

// ---------- formatters ----------
const fmtUSD = (n, decimals = 0) => {
  if (n == null) return '—';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(decimals === 0 ? 2 : decimals)}M`;
  if (abs >= 10_000)    return `${sign}$${(abs / 1000).toFixed(0)}k`;
  if (abs >= 1_000)     return `${sign}$${abs.toLocaleString('en-US', {maximumFractionDigits: 0})}`;
  return `${sign}$${abs.toFixed(2)}`;
};
const fmtPct = (n, decimals = 1) => n == null ? '—' : `${(n * 100).toFixed(decimals)}%`;
const fmtPctSigned = (n, decimals = 1) => n == null ? '—' : `${n >= 0 ? '+' : ''}${(n * 100).toFixed(decimals)}%`;
const fmtNum = (n) => n == null ? '—' : n.toLocaleString('en-US');
const truncWallet = (w) => w && w.length > 12 ? w : w;
const tsAgo = (iso) => {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
};

// ---------- icons (simple, monoline) ----------
const I = {
  feed: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 5h16M4 12h16M4 19h10"/></svg>,
  traders: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="9" cy="8" r="3"/><path d="M3 19c0-3 3-5 6-5s6 2 6 5"/><circle cx="17" cy="9" r="2.5"/><path d="M14 18c1-2 3-3 5-3s2 1 3 2"/></svg>,
  beaker: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M9 3h6M10 3v6L4 19a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-10V3"/></svg>,
  wallet: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 9h18M16 13h2"/></svg>,
  bag: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M5 8h14l-1 12H6L5 8zM9 8V6a3 3 0 0 1 6 0v2"/></svg>,
  chart: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 19h16M6 16V9m4 7V5m4 11v-9m4 9v-5"/></svg>,
  pulse: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg>,
  cog: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.4 1a7 7 0 0 0-1.7-1L14.5 3h-5l-.3 2.5a7 7 0 0 0-1.7 1l-2.4-1-2 3.5L5 10.5a7 7 0 0 0 0 3l-2 1.5 2 3.5 2.4-1a7 7 0 0 0 1.7 1L9.5 21h5l.3-2.5a7 7 0 0 0 1.7-1l2.4 1 2-3.5L19 13.5"/></svg>,
  help: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17v.01"/></svg>,
  bell: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M6 16l-1 2h14l-1-2V11a6 6 0 1 0-12 0v5zM10 20a2 2 0 0 0 4 0"/></svg>,
  copy: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>,
  ext: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M14 4h6v6M10 14L20 4M19 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>,
  insider: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="3"/><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/></svg>,
  cluster: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8 6h8M6 8v8M18 8v8M8 18h8M8 8l8 8M16 8l-8 8"/></svg>,
  caret: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6"/></svg>,
  x: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18"/></svg>,
  warning: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M12 3l10 18H2L12 3zM12 10v5M12 18v.01"/></svg>,
  target: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>,
};

// ---------- Sparkline ----------
function Sparkline({ data, w = 80, h = 24, color }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const stepX = w / (data.length - 1);
  const pts = data.map((v, i) => `${i * stepX},${h - ((v - min) / range) * h}`).join(' ');
  const trend = data[data.length - 1] - data[0];
  const cls = color ? '' : (trend >= 0 ? '' : 'down');
  return (
    <svg width={w} height={h} className="sparkline-wrap" style={{ display: 'block' }}>
      <polyline points={pts} className={`sparkline ${cls}`} style={color ? { stroke: color } : null} />
    </svg>
  );
}

// ---------- Sidebar ----------
function Sidebar({ route, setRoute, status }) {
  const items = [
    { id: 'dashboard', label: 'Dashboard',  ic: I.feed, badge: 4 },
    { id: 'traders',   label: 'Top Traders', ic: I.traders },
    { id: 'testing',   label: 'Testing',    ic: I.beaker },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">P</div>
        <div className="brand-word">POLYBOT<span className="dot">.</span></div>
      </div>
      <div className="nav-group-label">Navigate</div>
      {items.map(it => (
        <div
          key={it.id}
          className={`nav-item ${route.startsWith(it.id) ? 'active' : ''}`}
          onClick={() => setRoute(it.id)}
        >
          {it.ic}<span className="nav-label">{it.label}</span>
          {it.badge ? <span className="nav-badge">{it.badge}</span> : null}
        </div>
      ))}

      <div className="nav-group-label">Workspace</div>
      <div className="nav-item"><span style={{width:16,height:16}}>{I.chart}</span><span className="nav-label">Backtest</span></div>
      <div className="nav-item"><span style={{width:16,height:16}}>{I.pulse}</span><span className="nav-label">Diagnostics</span></div>
      <div className="nav-item"><span style={{width:16,height:16}}>{I.insider}</span><span className="nav-label">Insider list</span></div>
      <div className="nav-item"><span style={{width:16,height:16}}>{I.cog}</span><span className="nav-label">Settings</span></div>
      <div className="nav-item"><span style={{width:16,height:16}}>{I.help}</span><span className="nav-label">Help</span></div>

      <div className="sidebar-foot">
        <HealthPillSide status={status} />
        <div className="user-card">
          <div className="avatar">AB</div>
          <div>
            <div className="user-name">Alex Bruña</div>
            <div className="user-role mono">trader · paper</div>
          </div>
        </div>
      </div>
    </aside>
  );
}

function HealthPillSide({ status }) {
  const [open, setOpen] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!open) return;
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div className="health-pill-side" onClick={() => setOpen(o => !o)}>
        <span className={`health-dot ${status.overall_health}`}></span>
        <span>All systems healthy</span>
        <span className="micro" style={{ marginLeft: 'auto' }}>{status.components.position_refresh.minutes_since}m</span>
      </div>
      {open && (
        <div className="status-pop" style={{ top: 'auto', bottom: '100%', right: 0, left: 0, marginBottom: 6, marginTop: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>System status · {status.overall_health}</div>
          <StatusRow k="Last position refresh" v={`${status.components.position_refresh.minutes_since}m ago`} />
          <StatusRow k="Last cycle duration" v={`${status.last_cycle_duration_s}s`} />
          <StatusRow k="Long cycles (consec.)" v={status.consecutive_long_cycles} />
          <StatusRow k="Dropped positions" v={status.dropped_positions_last_cycle} />
          <StatusRow k="Tracked wallets" v={status.components.tracked_wallets.count} />
          <StatusRow k="Daily snapshot" v={`${status.components.daily_snapshot.succeeded}/28 ✓`} />
          <StatusRow k="Stats" v={status.components.stats_freshness.fresh ? 'fresh' : 'stale'} />
          <StatusRow k="Signals fired (72h)" v={status.components.recent_signals.fired_last_72h} />
          <StatusRow k="Zombie drops (24h)" v={status.zombie_drops_last_24h} />
        </div>
      )}
    </div>
  );
}
function StatusRow({ k, v }) {
  return <div className="status-row"><span className="k">{k}</span><span className="v">{v}</span></div>;
}

// ---------- Modal ----------
function Modal({ children, onClose }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div className="modal-scrim" onClick={(e) => e.target.classList.contains('modal-scrim') && onClose()}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

// ---------- toast ----------
function useToast() {
  const [t, setT] = useState(null);
  const show = (msg, kind='ok') => {
    setT({ msg, kind, id: Date.now() });
    setTimeout(() => setT(null), 2400);
  };
  const node = t ? (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, zIndex: 200,
      background: 'var(--panel)', border: '1px solid var(--border-2)',
      padding: '10px 14px', borderRadius: 10,
      boxShadow: 'var(--shadow-pop)', fontSize: 13,
      color: t.kind === 'ok' ? 'var(--accent)' : t.kind === 'bad' ? 'var(--no)' : 'var(--text)',
    }}>{t.msg}</div>
  ) : null;
  return [show, node];
}

Object.assign(window, {
  PB: D, fmtUSD, fmtPct, fmtPctSigned, fmtNum, truncWallet, tsAgo,
  Sidebar, Modal, Sparkline, useToast, ICONS: I,
});
