import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';
import {
  TrendingUp, TrendingDown, Loader2, RefreshCw,
  CheckCircle, XCircle, Clock, ChevronLeft, ChevronRight,
} from 'lucide-react';

const SPORTS = ['All', 'AFL', 'NBA', 'NRL'];

const parseMoney = (v) => parseFloat(String(v || '0').replace(/[$,]/g, '')) || 0;

// ── Colour tokens (CSS vars defined in global theme) ──────────────────────────
const C = {
  green:      'var(--success)',
  greenDim:   'var(--success-muted, oklch(28% 0.08 155))',
  red:        'var(--danger)',
  redDim:     'var(--danger-muted, oklch(28% 0.09 25))',
  blue:       '#3b82f6',
  blueDim:    'oklch(28% 0.09 245)',
  amber:      'var(--warning, #f59e0b)',
  amberDim:   'oklch(32% 0.08 75)',
  muted:      'var(--text-muted)',
  sec:        'var(--text-secondary)',
  border:     'var(--border)',
  cardAlt:    'var(--secondary)',
};

function getLegTypeTag(legText, stat) {
  const t = `${legText} ${stat || ''}`.toLowerCase();
  if (t.includes('tryscorer') || t.includes('try scorer') || (t.includes('try') && !t.includes('total'))) return 'Try';
  if (t.includes('disposal') || t.includes('disp')) return 'DISP';
  if (t.includes('goal')) return 'GOAL';
  if (t.includes('mark')) return 'MARK';
  if (t.includes('assist')) return 'AST';
  if (t.includes('rebound') || t.includes('reb')) return 'REB';
  if (t.includes('point') || t.includes(' pts')) return 'PTS';
  if (t.includes('head to head') || t.includes('h2h') || t.includes('match result') || t.includes('winner')) return 'H2H';
  if (t.includes('over') || t.includes('under') || t.includes('total') || t.includes('pyot') || t.includes('pyol')) return 'PYOT';
  return 'LEG';
}

// ── StatusBadge ───────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const s = (status || '').toLowerCase();
  const base = {
    display: 'inline-flex', alignItems: 'center', gap: 4,
    padding: '3px 8px', borderRadius: 4,
    fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
    flexShrink: 0,
  };
  if (s === 'won')  return <span style={{ ...base, background: C.greenDim, color: C.green }}><CheckCircle size={9} /> Won</span>;
  if (s === 'mb1')  return <span style={{ ...base, background: C.blueDim,  color: C.blue  }}><RefreshCw  size={9} /> MB1</span>;
  if (s === 'lost') return <span style={{ ...base, background: C.redDim,   color: C.red   }}><XCircle    size={9} /> Lost</span>;
  if (s === 'void') return <span style={{ ...base, background: 'var(--bg-card)', color: C.muted, border: `1px solid ${C.border}` }}><RefreshCw size={9} /> Void</span>;
  return                   <span style={{ ...base, background: C.amberDim, color: C.amber }}><Clock      size={9} /> Pending</span>;
}

// ── LegStatus — all original logic, new visual ────────────────────────────────
function LegStatus({ leg, legResult }) {
  const legText = typeof leg === 'string' ? leg : (leg.name || JSON.stringify(leg));
  const typeTag = getLegTypeTag(legText, legResult?.stat);

  const rowStyle = {
    display: 'grid',
    gridTemplateColumns: '14px 1fr auto',
    alignItems: 'center',
    gap: 7,
  };
  const infoStyle = { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 };
  const nameStyle = { fontSize: 11, fontWeight: 500, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
  const tagStyle  = { fontSize: 9, color: C.muted, textAlign: 'right', whiteSpace: 'nowrap' };

  if (legResult) {
    const r = (legResult.result || 'pending').toLowerCase();
    const isWon  = r === 'won';
    const isLost = r === 'lost';

    // Pre-compute missedByOne so we can use it for the icon too
    let missedByOne = false;
    if (legResult.actual !== null && legResult.actual !== undefined && legResult.line !== null) {
      const _stat   = (legResult.stat || '').toLowerCase();
      const _legLow = legText.toLowerCase();
      const _isPromo = ['pts', 'points', 'disp', 'disposals'].some((k) => _stat.includes(k) || _legLow.includes(k));
      missedByOne = _isPromo && parseFloat(legResult.line) > 0 && parseFloat(legResult.actual) === parseFloat(legResult.line) - 1 && !isWon;
    }

    const iconColor = missedByOne ? C.blue : isWon ? C.green : isLost ? C.red : C.amber;
    const icon = missedByOne
      ? <RefreshCw size={12} style={{ color: iconColor }} />
      : isWon
        ? <CheckCircle size={12} style={{ color: iconColor }} />
        : isLost
          ? <XCircle size={12} style={{ color: iconColor }} />
          : <Clock   size={12} style={{ color: iconColor }} />;

    let statDisplay = null;
    if (legResult.actual !== null && legResult.actual !== undefined && legResult.line !== null) {
      const target = parseFloat(legResult.line) || 0;
      const actual = parseFloat(legResult.actual) || 0;
      const pct    = target > 0 ? Math.min((actual / target) * 100, 100) : 0;
      const stat   = (legResult.stat || '').toLowerCase();
      const legLow = legText.toLowerCase();
      const isPromoStat = ['pts', 'points', 'disp', 'disposals'].some((k) => stat.includes(k) || legLow.includes(k));
      const barColor = missedByOne ? C.blue
        : isWon  ? C.green
        : isLost ? C.red
        : pct >= 100 ? C.green : pct >= 75 ? C.amber : C.red;

      statDisplay = (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 2 }}>
          <div style={{ position: 'relative', height: 4, borderRadius: 99, background: C.border, flex: 1, maxWidth: 130, overflow: 'visible' }}>
            <div style={{ width: `${pct}%`, height: '100%', borderRadius: 99, background: barColor, transition: 'width 0.4s' }} />
            <div style={{ position: 'absolute', right: 0, top: -3, width: 2, height: 10, borderRadius: 1, background: 'oklch(42% 0.01 240)' }} />
          </div>
          <span style={{ fontSize: 10, fontWeight: 700, color: barColor, whiteSpace: 'nowrap' }}>
            {actual}
            {isWon ? ' ✓' : missedByOne ? ' ≈' : isLost ? ' ✗' : ''}
            <span style={{ fontSize: 9, fontWeight: 400, color: C.muted }}>/{target}</span>
            {missedByOne && <span style={{ fontSize: 8, marginLeft: 2, color: C.blue, fontWeight: 700 }}>MB1</span>}
          </span>
        </div>
      );
    } else if (legResult.note) {
      statDisplay = <span style={{ fontSize: 10, color: C.muted, fontStyle: 'italic', marginTop: 2 }}>{legResult.note}</span>;
    }

    return (
      <div style={rowStyle}>
        <div style={{ display: 'flex', alignItems: 'center' }}>{icon}</div>
        <div style={infoStyle}>
          <span style={nameStyle}>{legText}</span>
          {statDisplay}
        </div>
        <span style={tagStyle}>{typeTag}</span>
      </div>
    );
  }

  // Fallback: no leg result data yet
  return (
    <div style={rowStyle}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <Clock size={12} style={{ color: C.muted }} />
      </div>
      <div style={infoStyle}>
        <span style={{ ...nameStyle, color: C.sec }}>{legText}</span>
      </div>
      <span style={tagStyle}>{typeTag}</span>
    </div>
  );
}

// ── Date helpers ──────────────────────────────────────────────────────────────
function todayStr() { return fmtDate(new Date()); }
function fmtDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function displayDate(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  const isToday = dateStr === todayStr();
  const isYesterday = dateStr === fmtDate(new Date(Date.now() - 86400000));
  const label = isToday ? 'Today' : isYesterday ? 'Yesterday' : '';
  const long = d.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  return label ? `${label} · ${long}` : long;
}

// ── Pill button ───────────────────────────────────────────────────────────────
function Pill({ active, onClick, children, activeColor, activeBg }) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 9px', borderRadius: 4, fontSize: 10, fontWeight: 500,
      border: `1px solid ${active ? (activeColor || 'var(--primary)') : C.border}`,
      background: active ? (activeBg || 'var(--primary-muted, oklch(38% 0.1 160))') : 'transparent',
      color: active ? (activeColor || 'var(--primary)') : C.sec,
      cursor: 'pointer', whiteSpace: 'nowrap', letterSpacing: '0.02em',
      transition: 'all 0.12s',
    }}>
      {children}
    </button>
  );
}

// ── MetaItem ──────────────────────────────────────────────────────────────────
function MetaItem({ label, value, color }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
      <span style={{ fontSize: 8, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      <span style={{ fontSize: 11, fontWeight: 700, color: color || C.sec }}>{value}</span>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function CsbResults() {
  const [bets, setBets]                       = useState([]);
  const [loading, setLoading]                 = useState(true);
  const [checking, setChecking]               = useState(false);
  const [syncing, setSyncing]                 = useState(false);
  const [syncResult, setSyncResult]           = useState(null);
  const [legResults, setLegResults]           = useState({});
  const [legResultsLoading, setLegResultsLoading] = useState(false);
  const [error, setError]                     = useState('');
  const [sportFilter, setSportFilter]         = useState('All');
  const [statusFilter, setStatusFilter]       = useState('');
  const [accountFilter, setAccountFilter]     = useState('All');
  const [gameFilter, setGameFilter]           = useState('All');
  const [selectedDate, setSelectedDate]       = useState(todayStr());
  const [autoRefresh, setAutoRefresh]         = useState(false);
  const autoRefreshRef = useRef(null);
  const fetchedBetIds  = useRef(new Set());

  const fetchBets = useCallback(async () => {
    try {
      const data = await api.getBetHistory(undefined, undefined, 500, selectedDate, selectedDate);
      const csbSources = new Set(['csb', 'csb_engine', 'expload']);
      const allBets = (data.bets || [])
        .filter((b) => csbSources.has((b.source || '').toLowerCase()))
        .map((b) => ({
          ...b,
          _stake:  parseMoney(b.stake),
          _odds:   parseMoney(b.combined_odds),
          _payout: parseMoney(b.payout),
          _status: (b.status || '').toLowerCase(),
          _sport:  detectSport(b),
        }));
      setBets(allBets);
      return allBets;
    } catch (err) {
      setError(err.message);
      return [];
    } finally {
      setLoading(false);
    }
  }, [selectedDate]);

  const fetchLegResults = useCallback(async (betsToCheck) => {
    const eligible = betsToCheck.filter((b) => {
      if (fetchedBetIds.current.has(b.id)) return false;
      const legs = b.legs || [];
      if (!legs.length) return false;
      const text = JSON.stringify(legs).toUpperCase();
      return text.includes('AFL') || text.includes('NBA')
        || text.includes('DISPOSALS') || text.includes('GOALS')
        || text.includes('POINTS') || text.includes('MARKS');
    });
    if (!eligible.length) return;
    setLegResultsLoading(true);
    const ids = eligible.map((b) => b.id);
    ids.forEach((id) => fetchedBetIds.current.add(id));
    try {
      const BATCH = 10;
      const merged = {};
      for (let i = 0; i < ids.length; i += BATCH) {
        const chunk = ids.slice(i, i + BATCH);
        const result = await api.getLegResults(chunk);
        Object.assign(merged, result);
      }
      setLegResults((prev) => ({ ...prev, ...merged }));
    } catch (err) {
      console.warn('leg-results fetch failed:', err.message);
    } finally {
      setLegResultsLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchedBetIds.current.clear();
    setLegResults({});
    fetchBets().then((allBets) => { if (allBets.length) fetchLegResults(allBets); });
  }, [fetchBets, fetchLegResults, selectedDate]);

  useEffect(() => {
    if (autoRefreshRef.current) clearInterval(autoRefreshRef.current);
    if (autoRefresh && bets.length > 0) {
      autoRefreshRef.current = setInterval(() => {
        fetchedBetIds.current.clear();
        fetchLegResults(bets);
      }, 60000);
    }
    return () => { if (autoRefreshRef.current) clearInterval(autoRefreshRef.current); };
  }, [autoRefresh, bets, fetchLegResults]);

  const handleRefreshAll = async () => {
    setChecking(true);
    setError('');
    try {
      await api.checkResults();
      const allBets = await fetchBets();
      fetchedBetIds.current.clear();
      if (allBets.length) fetchLegResults(allBets);
    } catch (err) {
      setError(err.message);
    } finally {
      setChecking(false);
    }
  };

  const handleSyncManualBets = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = await api.syncManualBets();
      setSyncResult(result);
      if (result.imported > 0) {
        const allBets = await fetchBets();
        if (allBets.length) fetchLegResults(allBets);
      }
    } catch (err) {
      setSyncResult({ imported: 0, error: err.message });
    } finally {
      setSyncing(false);
    }
  };

  // ── Status helpers (unchanged logic) ───────────────────────────────────────
  const isLegHit = (l) => {
    const r = (l.result || '').toLowerCase();
    if (r === 'won') return true;
    if (r === 'lost') return false;
    if (l.actual !== null && l.actual !== undefined && l.line !== null && l.line !== undefined)
      return parseFloat(l.actual) >= parseFloat(l.line);
    return false;
  };
  const isLegLost = (l) => (l.result || '').toLowerCase() === 'lost';
  const isLegMissedByOne = (l) => {
    if (l.actual !== null && l.actual !== undefined && l.line !== null && l.line !== undefined)
      return parseFloat(l.actual) === parseFloat(l.line) - 1;
    return false;
  };

  // MB1 (Money Back if miss by 1) is a PER-LEG promo, applied only to certain
  // markets (currently NBA Points and AFL Disposals). When the leg's actual
  // equals line-1 on those markets, TAB pays that leg AS A WINNER. Multi
  // implication: every leg that wins outright OR triggers MB1 counts as a hit,
  // so a bet where all legs are hit-or-mb1hit is a winning bet (labelled 'mb1'
  // for visibility when any leg used the promo).
  const isMb1EligibleMarket = (legDef, legResult) => {
    const legName = (legDef && (legDef.name || legDef.selectionName)) || (typeof legDef === 'string' ? legDef : '') || '';
    const stat = (legResult && legResult.stat) || '';
    const hay = `${legName} ${stat}`.toLowerCase();
    return ['pts', 'points', 'disp', 'disposals'].some((k) => hay.includes(k));
  };

  // Returns 'hit' | 'mb1hit' | 'miss' | 'void' | 'pending' for a single leg.
  //
  // Asymmetry is intentional: once a player crosses their line mid-game the
  // stat can't go backwards so we CAN call it a hit before the game ends.
  // But being below the line mid-game does NOT mean the leg lost — the player
  // can still catch up in later quarters. So "miss" / "mb1hit" both require
  // TAB to have explicitly marked the leg `result = 'lost'` (i.e. game over).
  // Without that gate, live games would light up as lost the moment results
  // arrived with current actual < line.
  //
  // Void: player scratched, market withdrawn, etc. Handled per sport at the
  // bet level — NBA drops the voided leg and prices the remaining ones; AFL
  // treats any void as a whole-bet refund.
  const classifyLeg = (legDef, legResult) => {
    if (!legResult) return 'pending';
    const r = (legResult.result || '').toLowerCase();
    const hasData = legResult.actual !== null && legResult.actual !== undefined
                    && legResult.line !== null && legResult.line !== undefined;

    if (['void', 'voided', 'cancelled', 'canceled', 'pushed', 'push', 'refund', 'refunded']
        .includes(r)) {
      return 'void';
    }

    // Hits can lock in mid-game — stats don't go backwards.
    if (r === 'won') return 'hit';
    if (hasData && parseFloat(legResult.actual) >= parseFloat(legResult.line)) return 'hit';

    // Misses and MB1 require TAB to have called the leg lost (game finished).
    if (r === 'lost') {
      if (hasData
          && parseFloat(legResult.actual) === parseFloat(legResult.line) - 1
          && isMb1EligibleMarket(legDef, legResult)) {
        return 'mb1hit';
      }
      return 'miss';
    }
    return 'pending';
  };

  const getEffectiveStatus = (bet) => {
    // Bet-level settled states that TAB already decided — trust them directly.
    // These can come through check-results when TAB resulted a bet as void /
    // refunded (e.g. a voided leg in an SGM reduced to a single that then
    // lost, or an all-voided multi). No per-leg analysis needed.
    if (bet._status === 'refunded' || bet._status === 'void') return 'void';
    if (bet._status === 'cashed out') return 'won';  // TAB paid a cash-out — count as a positive outcome

    const lr = legResults[bet.id] || [];
    const legs = bet.legs || [];

    // Classify each leg. legs.map over the bet's leg list so unresolved-but-
    // expected legs (no leg_results row yet) still surface as 'pending'.
    const classifications = legs.length > 0
      ? legs.map((leg, i) => classifyLeg(leg, lr[i] || null))
      : lr.map((result, i) => classifyLeg(null, result));

    const hasVoid    = classifications.some((c) => c === 'void');
    const hasPending = classifications.some((c) => c === 'pending');
    const sport      = (bet._sport || '').toUpperCase();
    const isAFL      = sport === 'AFL';

    // AFL: any voided leg refunds the whole bet (after pending legs resolve).
    if (hasVoid && isAFL) {
      if (hasPending) return 'pending';
      return 'void';
    }

    // NBA (and everything else not AFL): voided legs drop out. The remaining
    // legs form a smaller multi priced at their product odds — if they all
    // hit the bet wins at the reduced payout TAB calculates; if any remaining
    // leg misses, the bet loses.
    const active = hasVoid
      ? classifications.filter((c) => c !== 'void')
      : classifications;

    const activePending = active.some((c) => c === 'pending');
    const activeMiss    = active.some((c) => c === 'miss');
    const activeMb1Hit  = active.some((c) => c === 'mb1hit');

    // Edge case: every leg voided → whole bet refunds.
    if (hasVoid && active.length === 0) return 'void';

    if (bet._status === 'pending') {
      if (activeMiss)    return activePending ? 'pending' : 'lost';
      if (activePending) return 'pending';
      return activeMb1Hit ? 'mb1' : 'won';
    }

    // Local record says Lost but our analysis shows all remaining legs hit-or-
    // mb1hit → bet actually won (via MB1 or at reduced odds after a void) and
    // TAB hadn't credited the result when we last synced.
    if (bet._status === 'lost' || bet._status === 'settled - loser') {
      if (active.length > 0 && !activeMiss && !activePending) {
        return activeMb1Hit ? 'mb1' : 'won';
      }
    }
    return bet._status;
  };

  // ── Derived data ──────────────────────────────────────────────────────────
  const accountLabels = [...new Set(bets.map((b) => b.account_label).filter(Boolean))].sort();

  // Extract the game(s) a bet touches. SGMs live on one game; cross-game multis
  // can span several. Reads bet.game_id first (formats like "20260420_PHX_OKC")
  // then falls back to team tags in leg names ("NBA OKC-Phx 10+Pts ..."). The
  // two sources use different home/visitor orderings — canonicalize by sorting
  // the two tags alphabetically so one bet produces one game label regardless.
  const canonGame = (a, b) => {
    const parts = [a.toUpperCase(), b.toUpperCase()].sort();
    return `${parts[0]}-${parts[1]}`;
  };
  const extractGames = (bet) => {
    const games = new Set();
    const gid = bet.game_id || bet.gameId || '';
    if (gid) {
      const m = gid.match(/([A-Z][A-Za-z]{1,3})[_-]([A-Z][A-Za-z]{1,3})/);
      if (m) games.add(canonGame(m[1], m[2]));
    }
    const legs = bet.legs || [];
    for (const leg of legs) {
      const name = (typeof leg === 'string' ? leg : (leg && leg.name)) || '';
      const m = name.match(/\b([A-Z][A-Za-z]{1,3})-([A-Z][A-Za-z]{1,3})\b/);
      if (m) games.add(canonGame(m[1], m[2]));
    }
    return [...games];
  };

  const gameLabels = [...new Set(bets.flatMap(extractGames))].sort();

  const filtered = bets.filter((b) => {
    if (sportFilter !== 'All' && b._sport !== sportFilter) return false;
    if (statusFilter && getEffectiveStatus(b) !== statusFilter) return false;
    if (accountFilter !== 'All' && b.account_label !== accountFilter) return false;
    if (gameFilter !== 'All') {
      const games = extractGames(b);
      if (!games.includes(gameFilter)) return false;
    }
    return true;
  });

  const totalStaked  = filtered.reduce((s, b) => s + b._stake, 0);
  // MB1 = refund-triggering win outcome. Once all legs have resolved, "MB1"
  // means exactly one leg missed by 1 and every other leg hit — that IS a
  // winning bet in operator P/L terms. The bug fixed earlier was the mis-
  // CLASSIFICATION of lost SGMs as MB1 (when a non-stat leg was also lost);
  // the isLegMissed rewrite in getEffectiveStatus handles that. MB1 stays
  // in wonBets for the money math.
  const wonBets      = filtered.filter((b) => { const s = getEffectiveStatus(b); return s === 'won' || s === 'mb1'; });
  const lostBets     = filtered.filter((b) => getEffectiveStatus(b) === 'lost');
  const pendingBets  = filtered.filter((b) => getEffectiveStatus(b) === 'pending');
  const mb1Bets      = filtered.filter((b) => getEffectiveStatus(b) === 'mb1');
  const wonProfit    = wonBets.reduce((s, b) => s + (b._payout > 0 ? (b._payout - b._stake) : (b._odds - 1) * b._stake), 0);
  const lostStake    = lostBets.reduce((s, b) => s + b._stake, 0);
  const settledPL    = wonProfit - lostStake;
  const pendingPotential = pendingBets.reduce((s, b) => s + (b._odds * b._stake) - b._stake, 0);
  const pendingStake = pendingBets.reduce((s, b) => s + b._stake, 0);
  const ceiling      = settledPL + pendingPotential;
  const floor        = settledPL - pendingStake;
  const currentPL    = settledPL;
  const winRate      = wonBets.length + lostBets.length > 0
    ? ((wonBets.length / (wonBets.length + lostBets.length)) * 100).toFixed(1)
    : null;

  // ── Navigation helpers ────────────────────────────────────────────────────
  const stepDate = (dir) => {
    const d = new Date(selectedDate + 'T12:00:00');
    d.setDate(d.getDate() + dir);
    setSelectedDate(fmtDate(d));
  };

  // ── Styles ────────────────────────────────────────────────────────────────
  const filterBarStyle = {
    display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8,
    padding: '10px 0 14px', marginBottom: 16,
    borderBottom: '1px solid var(--border)',
  };
  const filterSep = <div style={{ width: 1, height: 18, background: C.border, margin: '0 2px' }} />;

  const statsRowStyle = {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
    gap: 10,
    marginBottom: 18,
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="animate-fade-in">

      {/* ── Filter bar ── */}
      <div style={filterBarStyle}>
        {/* Date navigation */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <button className="btn btn-secondary" style={{ padding: '5px 7px' }} onClick={() => stepDate(-1)}>
            <ChevronLeft size={13} />
          </button>
          <input type="date" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)}
            style={{ padding: '4px 8px', fontSize: 11, borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer', fontFamily: 'inherit' }} />
          <button className="btn btn-secondary" style={{ padding: '5px 7px' }} onClick={() => stepDate(1)}>
            <ChevronRight size={13} />
          </button>
          {selectedDate !== todayStr() && (
            <button className="btn btn-primary" style={{ padding: '4px 9px', fontSize: 10 }} onClick={() => setSelectedDate(todayStr())}>
              Today
            </button>
          )}
        </div>

        {filterSep}

        {/* Sport */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <span style={{ fontSize: 9, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginRight: 2 }}>Sport</span>
          {SPORTS.map((s) => (
            <Pill key={s} active={sportFilter === s} onClick={() => setSportFilter(s)}>{s}</Pill>
          ))}
        </div>

        {filterSep}

        {/* Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <span style={{ fontSize: 9, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginRight: 2 }}>Status</span>
          <Pill active={statusFilter === ''} onClick={() => setStatusFilter('')}>All</Pill>
          <Pill active={statusFilter === 'won'}     onClick={() => setStatusFilter('won')}     activeColor={C.green} activeBg={C.greenDim}>Won</Pill>
          <Pill active={statusFilter === 'mb1'}     onClick={() => setStatusFilter('mb1')}     activeColor={C.blue}  activeBg={C.blueDim}>MB1</Pill>
          <Pill active={statusFilter === 'lost'}    onClick={() => setStatusFilter('lost')}    activeColor={C.red}   activeBg={C.redDim}>Lost</Pill>
          <Pill active={statusFilter === 'pending'} onClick={() => setStatusFilter('pending')} activeColor={C.amber} activeBg={C.amberDim}>Pending</Pill>
          <Pill active={statusFilter === 'void'}    onClick={() => setStatusFilter('void')}    activeColor={C.muted} activeBg={'var(--bg-card)'}>Void</Pill>
        </div>

        {accountLabels.length > 1 && (
          <>
            {filterSep}
            <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
              <span style={{ fontSize: 9, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginRight: 2 }}>Account</span>
              <Pill active={accountFilter === 'All'} onClick={() => setAccountFilter('All')}>All</Pill>
              {accountLabels.map((a) => (
                <Pill key={a} active={accountFilter === a} onClick={() => setAccountFilter(a)}>{a}</Pill>
              ))}
            </div>
          </>
        )}

        {gameLabels.length > 0 && (
          <>
            {filterSep}
            <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 9, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginRight: 2 }}>Game</span>
              <Pill active={gameFilter === 'All'} onClick={() => setGameFilter('All')}>All</Pill>
              {gameLabels.map((g) => (
                <Pill key={g} active={gameFilter === g} onClick={() => setGameFilter(g)}>{g}</Pill>
              ))}
            </div>
          </>
        )}

        {/* Right side: status indicators + actions */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {(legResultsLoading || checking) && (
            <span style={{ fontSize: 10, color: C.muted, display: 'flex', alignItems: 'center', gap: 4 }}>
              <Loader2 size={11} className="animate-spin" />
              {checking ? 'Checking...' : 'Fetching stats...'}
            </span>
          )}
          <button onClick={() => setAutoRefresh((p) => !p)}
            className={autoRefresh ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '5px 10px', fontSize: 11, display: 'flex', alignItems: 'center', gap: 5 }}>
            <Clock size={12} />{autoRefresh ? 'Auto 60s' : 'Auto'}
          </button>
          <button onClick={handleRefreshAll} disabled={checking || legResultsLoading}
            className="btn btn-secondary" style={{ padding: '5px 10px', fontSize: 11, display: 'flex', alignItems: 'center', gap: 5 }}>
            {checking ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Refresh
          </button>
          <button onClick={handleSyncManualBets} disabled={syncing}
            className="btn btn-secondary" style={{ padding: '5px 10px', fontSize: 11, display: 'flex', alignItems: 'center', gap: 5 }}>
            {syncing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Sync
          </button>
        </div>
      </div>

      {/* Sync result feedback */}
      {syncResult && (
        <div style={{ fontSize: 12, padding: '8px 14px', marginBottom: 14, borderRadius: 6,
          background: syncResult.imported > 0 ? 'var(--success-muted)' : 'var(--bg-card)',
          color: syncResult.error ? C.red : C.sec }}>
          {syncResult.error ? syncResult.error
            : syncResult.imported > 0
              ? <><CheckCircle size={13} style={{ color: C.green, verticalAlign: 'middle', marginRight: 4 }} />{syncResult.imported} manual bet{syncResult.imported !== 1 ? 's' : ''} imported</>
              : `No new manual bets found (${syncResult.accounts_checked?.length || 0} accounts checked)`}
        </div>
      )}

      {error && (
        <div style={{ background: 'var(--danger-muted)', color: C.red, padding: '10px 14px', borderRadius: 'var(--radius)', fontSize: 12, marginBottom: 14 }}>
          {error}
        </div>
      )}

      {/* ── Stats row ── */}
      {filtered.length > 0 && (
        <div style={statsRowStyle}>
          <div className="stat-card" style={{ padding: '12px 14px' }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: C.muted, marginBottom: 4 }}>Bets</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>{filtered.length}</div>
            <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>
              <span style={{ color: C.green }}>{wonBets.length - mb1Bets.length}W</span>
              {mb1Bets.length > 0 && <> / <span style={{ color: C.blue }}>{mb1Bets.length}MB1</span></>}
              {' / '}<span style={{ color: C.red }}>{lostBets.length}L</span>
              {' / '}<span style={{ color: C.amber }}>{pendingBets.length}P</span>
            </div>
          </div>

          <div className="stat-card" style={{ padding: '12px 14px' }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: C.muted, marginBottom: 4 }}>Staked</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>${totalStaked.toFixed(2)}</div>
          </div>

          <div className="stat-card" style={{ padding: '12px 14px', borderLeft: `3px solid ${C.red}` }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: C.red, marginBottom: 4 }}>Floor</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <TrendingDown size={14} style={{ color: C.red }} />
              <span style={{ fontSize: 20, fontWeight: 700, color: C.red }}>{floor >= 0 ? '+' : ''}${floor.toFixed(2)}</span>
            </div>
          </div>

          <div className="stat-card" style={{ padding: '12px 14px', borderLeft: '3px solid var(--primary)' }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--primary)', marginBottom: 4 }}>Current P/L</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              {currentPL >= 0
                ? <TrendingUp size={14} style={{ color: C.green }} />
                : <TrendingDown size={14} style={{ color: C.red }} />}
              <span style={{ fontSize: 20, fontWeight: 700, color: currentPL >= 0 ? C.green : C.red }}>
                {currentPL >= 0 ? '+' : ''}${currentPL.toFixed(2)}
              </span>
            </div>
          </div>

          <div className="stat-card" style={{ padding: '12px 14px', borderLeft: `3px solid ${C.green}` }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: C.green, marginBottom: 4 }}>Ceiling</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <TrendingUp size={14} style={{ color: C.green }} />
              <span style={{ fontSize: 20, fontWeight: 700, color: C.green }}>{ceiling >= 0 ? '+' : ''}${ceiling.toFixed(2)}</span>
            </div>
          </div>

          {winRate !== null && (
            <div className="stat-card" style={{ padding: '12px 14px' }}>
              <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: C.muted, marginBottom: 4 }}>Win Rate</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>{winRate}%</div>
            </div>
          )}
        </div>
      )}

      {/* ── Bet feed ── */}
      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: C.sec, padding: '56px 0' }}>
          <Loader2 size={16} className="animate-spin" /> Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '56px 24px', color: C.muted }}>
          No CSB bets found for {displayDate(selectedDate)}.
        </div>
      ) : (
        <>
          {/* Section header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10,
            paddingBottom: 8, borderBottom: '1px solid var(--border)' }}>
            <span style={{ fontSize: 10, fontWeight: 600, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              {displayDate(selectedDate)}
            </span>
            <span style={{ fontSize: 9, color: C.muted, background: 'var(--bg-card)', border: `1px solid ${C.border}`, borderRadius: 3, padding: '1px 5px' }}>
              {filtered.length} bets
            </span>
            <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 700, color: currentPL >= 0 ? C.green : C.red }}>
              {currentPL >= 0 ? '+' : ''}${currentPL.toFixed(2)}
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map((bet) => {
              const legs = bet.legs || [];
              const betLegResults  = legResults[bet.id] || [];
              const effectiveStatus = getEffectiveStatus(bet);
              const isWon     = effectiveStatus === 'won';
              const isLost    = effectiveStatus === 'lost';
              const isMb1     = effectiveStatus === 'mb1';
              const isVoid    = effectiveStatus === 'void';
              const isPending = effectiveStatus === 'pending';
              const borderColor = isWon ? C.green : isMb1 ? C.blue : isLost ? C.red : isVoid ? C.muted : C.amber;
              const matchName = bet.match || bet.event_name || bet.event || '';
              const placedTime = bet.placed_at
                ? new Date(bet.placed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : '';
              const potReturn = bet._odds * bet._stake;
              const profit = bet._payout > 0 ? (bet._payout - bet._stake) : (bet._odds - 1) * bet._stake;

              return (
                <article key={bet.id} style={{
                  background: 'var(--bg-card)',
                  border: `1px solid var(--border)`,
                  borderLeft: `3px solid ${borderColor}`,
                  borderRadius: 9,
                  overflow: 'hidden',
                }}>
                  {/* Card header */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 13px',
                    background: C.cardAlt, borderBottom: '1px solid var(--border)',
                  }}>
                    <span style={{
                      fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em',
                      padding: '2px 6px', borderRadius: 3,
                      background: 'var(--bg-card)', color: C.muted, border: `1px solid ${C.border}`,
                      flexShrink: 0,
                    }}>{bet._sport}</span>

                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {matchName || `${bet.bet_type} — ${bet.account_label || ''}`}
                    </span>

                    {/* Live pulse for pending */}
                    {isPending && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                        <div style={{
                          width: 6, height: 6, borderRadius: '50%', background: C.amber,
                          animation: 'csb-pulse 1.4s infinite',
                        }} />
                        <span style={{ fontSize: 10, color: C.amber }}>Live</span>
                      </div>
                    )}

                    <span style={{ fontSize: 10, color: C.muted, flexShrink: 0 }}>{placedTime}</span>
                  </div>

                  {/* Legs */}
                  <div style={{ padding: '9px 13px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {legs.map((leg, j) => (
                      <LegStatus
                        key={j}
                        leg={leg}
                        legResult={betLegResults[j] || null}
                      />
                    ))}
                  </div>

                  {/* Card footer */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '7px 13px',
                    borderTop: '1px solid var(--border)', background: C.cardAlt,
                  }}>
                    <span style={{ fontSize: 10, color: C.muted }}>
                      Account: <span style={{ color: C.sec, fontWeight: 600 }}>{bet.account_label || '—'}</span>
                      {bet.bet_type && <span style={{ marginLeft: 8, fontSize: 9, color: C.muted, border: `1px solid ${C.border}`, borderRadius: 3, padding: '1px 4px' }}>{bet.bet_type}</span>}
                    </span>

                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
                      <MetaItem label="Stake" value={`$${bet._stake.toFixed(2)}`} />
                      <MetaItem label="Odds" value={bet._odds.toFixed(2)} />
                      <MetaItem
                        label={isWon || isMb1 ? 'Profit' : isLost ? 'Loss' : isVoid ? 'Refund' : 'To Win'}
                        value={
                          isWon || isMb1 ? `+$${profit.toFixed(2)}`
                          : isLost       ? `-$${bet._stake.toFixed(2)}`
                          : isVoid       ? `$${bet._stake.toFixed(2)}`
                          : `$${potReturn.toFixed(2)}`
                        }
                        color={isWon || isMb1 ? C.green : isLost ? C.red : isVoid ? C.muted : C.amber}
                      />
                      <StatusBadge status={effectiveStatus} />
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </>
      )}

      <style>{`
        @keyframes csb-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.25; }
        }
      `}</style>
    </div>
  );
}

function detectSport(bet) {
  const text = JSON.stringify(bet.legs || []).toLowerCase();
  if (text.includes('afl') || text.includes('disp') || text.includes('disposal')) return 'AFL';
  if (text.includes('nba') || text.includes('pts') || text.includes('points') || text.includes('rebounds')) return 'NBA';
  if (text.includes('nrl') || text.includes('rugby')) return 'NRL';
  return 'Other';
}
