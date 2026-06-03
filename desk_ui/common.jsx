// common.jsx — shared primitives & formatters

// ── number formatting ──
function fmtPx(n) { return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmtMoney(n) {
  const s = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return (n < 0 ? '−$' : '+$') + s;
}
function fmtMoneyPlain(n) {
  const neg = n < 0;
  return (neg ? '−$' : '$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}
function fmtR(r) { return (r >= 0 ? '+' : '−') + Math.abs(r).toFixed(1) + 'R'; }
function fmtPct(now, then) {
  const p = ((now - then) / then) * 100;
  return (p >= 0 ? '+' : '−') + Math.abs(p).toFixed(2) + '%';
}
function pctClass(now, then) { return now >= then ? 'up' : 'down'; }

// ── Stamp ──
function Stamp({ children, tone = 'neutral', led }) {
  return (
    <span className={`stamp ${tone}`}>
      {led && <i className="led" />}
      {children}
    </span>
  );
}

// ── Importance marker ──
const IMP_LABEL = { high: 'High conviction', watch: 'Watching', idea: 'Idea' };
const IMP_SHORT = { high: 'High', watch: 'Watch', idea: 'Idea' };
function Imp({ level, full }) {
  return (
    <span className={`imp ${level}`}>
      <i className="pip" />
      {full ? IMP_LABEL[level] : IMP_SHORT[level]}
    </span>
  );
}

// ── Direction stamp ──
function DirStamp({ dir }) {
  return <span className={`stamp ${dir === 'long' ? 'gain' : 'loss'}`}>{dir === 'long' ? 'Long' : 'Short'}</span>;
}

Object.assign(window, { fmtPx, fmtMoney, fmtMoneyPlain, fmtR, fmtPct, pctClass, Stamp, Imp, DirStamp, IMP_LABEL, IMP_SHORT });
