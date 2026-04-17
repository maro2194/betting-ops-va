import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';
import {
  TrendingUp, TrendingDown, Loader2, RefreshCw,
  CheckCircle, XCircle, Clock, Filter, ChevronLeft, ChevronRight, Calendar,
} from 'lucide-react';

const SPORTS = ['All', 'AFL', 'NBA', 'NRL'];

const parseMoney = (v) => parseFloat(String(v || '0').replace(/[$,]/g, '')) || 0;

function StatusBadge({ status }) {
  const s = (status || '').toLowerCase();
  if (s === 'won') return <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><CheckCircle size={10} /> Won</span>;
  if (s === 'mb1') return <span className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: '#3b82f6', color: '#fff' }}><RefreshCw size={10} /> MB1</span>;
  if (s === 'lost') return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> Lost</span>;
  return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Clock size={10} /> Pending</span>;
}

/**
 * LegStatus — shows a leg with real stat result if available.
 * legResult: {name, player, stat, line, actual, result: 'won'|'lost'|'pending', note}
 * betStatus: the overall bet status (used as fallback when no legResult)
 */
function LegStatus({ leg, betStatus, legResult }) {
  const legText = typeof leg === 'string' ? leg : (leg.name || JSON.stringify(leg));

  // If we have a real per-leg result, use it
  if (legResult) {
    const r = (legResult.result || 'pending').toLowerCase();
    const isWon = r === 'won';
    const isLost = r === 'lost';

    const iconColor = isWon ? 'var(--success)' : isLost ? 'var(--danger)' : 'var(--text-muted)';
    const textColor = isWon ? 'var(--success)' : isLost ? 'var(--danger)' : 'var(--text-secondary)';
    const icon = isWon
      ? <CheckCircle size={11} style={{ color: iconColor, flexShrink: 0 }} />
      : isLost
        ? <XCircle size={11} style={{ color: iconColor, flexShrink: 0 }} />
        : <Clock size={11} style={{ color: iconColor, flexShrink: 0 }} />;

    // Build progress bar + stat display
    let statDisplay = null;
    if (legResult.actual !== null && legResult.actual !== undefined && legResult.line !== null) {
      const target = parseFloat(legResult.line) || 0;
      const actual = parseFloat(legResult.actual) || 0;
      const pct = target > 0 ? Math.min((actual / target) * 100, 100) : 0;
      // "Miss by 1" promo: Points and Disposals — blue when exactly 1 away from target
      // Shows during game (one more to hit!) AND after game (promo refund triggered)
      const stat = (legResult.stat || '').toLowerCase();
      const legName = legText.toLowerCase();
      const isPromoStat = ['pts', 'points', 'disp', 'disposals'].some((k) => stat.includes(k) || legName.includes(k));
      const missedByOne = isPromoStat && target > 0 && actual === target - 1 && !isWon;
      const barColor = missedByOne ? '#3b82f6' : isWon ? 'var(--success)' : isLost ? 'var(--danger)' : pct >= 100 ? 'var(--success)' : pct >= 75 ? '#f59e0b' : '#ef4444';

      statDisplay = (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 6, flex: '0 0 140px' }}>
          <div style={{
            position: 'relative',
            width: 100,
            height: 8,
            borderRadius: 4,
            background: 'var(--border)',
            overflow: 'hidden',
          }}>
            {/* Progress fill */}
            <div style={{
              width: `${pct}%`,
              height: '100%',
              borderRadius: 4,
              background: barColor,
              transition: 'width 0.3s',
            }} />
            {/* Target marker line at 100% */}
            <div style={{
              position: 'absolute',
              right: 0,
              top: -1,
              width: 2,
              height: 10,
              background: 'var(--text-muted)',
              borderRadius: 1,
            }} />
          </div>
          <span style={{ fontSize: 11, fontWeight: 700, color: barColor, whiteSpace: 'nowrap' }}>
            {actual}{isWon ? ' ✓' : missedByOne ? ' ≈' : isLost ? ' ✗' : ''}<span style={{ fontSize: 9, fontWeight: 400, color: 'var(--text-muted)' }}>/{target}</span>
            {missedByOne && <span style={{ fontSize: 9, marginLeft: 2, color: '#3b82f6', fontWeight: 600 }}>MB1</span>}
          </span>
        </div>
      );
    } else if (legResult.note) {
      statDisplay = (
        <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic' }}>
          {legResult.note}
        </span>
      );
    }

    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, flexWrap: 'wrap' }}>
        {icon}
        <span style={{ color: textColor }}>{legText}</span>
        {statDisplay}
      </div>
    );
  }

  // Fallback: no individual leg result — show neutral/pending (don't inherit bet status)
  let color = 'var(--text-secondary)';
  let icon = <Clock size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
      {icon}
      <span style={{ color }}>{legText}</span>
    </div>
  );
}

function todayStr() {
  const now = new Date();
  return fmtDate(now);
}

function fmtDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export default function CsbResults() {
  const [bets, setBets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [legResults, setLegResults] = useState({}); // betId -> [legResult, ...]
  const [legResultsLoading, setLegResultsLoading] = useState(false);
  const [error, setError] = useState('');
  const [sportFilter, setSportFilter] = useState('All');
  const [statusFilter, setStatusFilter] = useState('');
  const [accountFilter, setAccountFilter] = useState('All');
  const [selectedDate, setSelectedDate] = useState(todayStr());
  const [autoRefresh, setAutoRefresh] = useState(false);
  const autoRefreshRef = useRef(null);

  // Track which bet IDs we've already fetched leg results for to avoid re-fetching
  const fetchedBetIds = useRef(new Set());

  const fetchBets = useCallback(async () => {
    try {
      const data = await api.getBetHistory(undefined, undefined, 500, selectedDate, selectedDate);
      const csbSources = new Set(['csb', 'csb_engine', 'expload']);
      const allBets = (data.bets || [])
        .filter((b) => csbSources.has((b.source || '').toLowerCase()))
        .map((b) => ({
          ...b,
          _stake: parseMoney(b.stake),
          _odds: parseMoney(b.combined_odds),
          _payout: parseMoney(b.payout),
          _status: (b.status || '').toLowerCase(),
          _sport: detectSport(b),
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

  // Fetch per-leg results for ALL bets (pending + settled — shows actual stats)
  const fetchLegResults = useCallback(async (betsToCheck) => {
    const eligible = betsToCheck.filter((b) => {
      if (fetchedBetIds.current.has(b.id)) return false;
      const legs = b.legs || [];
      if (!legs.length) return false;
      const text = JSON.stringify(legs).toUpperCase();
      // V1 has "AFL"/"NBA" prefix; V2 has stat names like "Disposals", "Goals", "Points"
      return text.includes('AFL') || text.includes('NBA') || text.includes('DISPOSALS') || text.includes('GOALS') || text.includes('POINTS') || text.includes('MARKS');
    });

    if (!eligible.length) return;

    setLegResultsLoading(true);
    const ids = eligible.map((b) => b.id);

    // Mark as fetched immediately to prevent duplicate calls
    ids.forEach((id) => fetchedBetIds.current.add(id));

    try {
      // Batch in groups of 10 to avoid huge query strings
      const BATCH = 10;
      const merged = {};
      for (let i = 0; i < ids.length; i += BATCH) {
        const chunk = ids.slice(i, i + BATCH);
        const result = await api.getLegResults(chunk);
        Object.assign(merged, result);
      }
      setLegResults((prev) => ({ ...prev, ...merged }));
    } catch (err) {
      // Non-fatal — leg results are supplemental
      console.warn('leg-results fetch failed:', err.message);
    } finally {
      setLegResultsLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchedBetIds.current.clear();
    setLegResults({});
    fetchBets().then((allBets) => {
      if (allBets.length) fetchLegResults(allBets);
    });
  }, [fetchBets, fetchLegResults, selectedDate]);

  // Auto-refresh: poll leg results every 60s when enabled
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

  // Combined refresh: check results from TAB + refresh live stats
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

  // Derive effective status from leg results for each bet
  // A leg is "effectively won" if result==='won' OR if actual >= line (target already hit)
  const isLegHit = (l) => {
    const r = (l.result || '').toLowerCase();
    if (r === 'won') return true;
    if (r === 'lost') return false;
    // Pending but actual >= line means target already hit (over line reached mid-game)
    if (l.actual !== null && l.actual !== undefined && l.line !== null && l.line !== undefined) {
      return parseFloat(l.actual) >= parseFloat(l.line);
    }
    return false;
  };
  const isLegLost = (l) => (l.result || '').toLowerCase() === 'lost';
  const isLegMissedByOne = (l) => {
    // Leg missed target by exactly 1 (for TAB SGM MB1 refund detection)
    if (l.actual !== null && l.actual !== undefined && l.line !== null && l.line !== undefined) {
      const actual = parseFloat(l.actual);
      const line = parseFloat(l.line);
      return actual === line - 1;
    }
    return false;
  };

  const getEffectiveStatus = (bet) => {
    const lr = legResults[bet.id] || [];
    if (lr.length > 0 && bet._status === 'pending') {
      // Any leg confirmed lost → check for MB1 first
      if (lr.some(isLegLost)) {
        // MB1: exactly 1 leg missed by exactly 1, all other legs hit
        const missedLegs = lr.filter((l) => !isLegHit(l));
        if (missedLegs.length === 1 && isLegMissedByOne(missedLegs[0])) return 'mb1';
        return 'lost';
      }
      const legs = bet.legs || [];
      // All legs hit their target → effectively won
      if (lr.length >= legs.length && lr.every(isLegHit)) return 'won';
      // Check MB1 mid-game: all legs resolved, exactly 1 missed by 1
      if (lr.length >= legs.length) {
        const missedLegs = lr.filter((l) => !isLegHit(l));
        if (missedLegs.length === 1 && isLegMissedByOne(missedLegs[0])) return 'mb1';
      }
    }
    // Also check settled bets — TAB might mark as "lost" but it's actually MB1
    if (lr.length > 0 && (bet._status === 'lost' || bet._status === 'Settled - Loser')) {
      const missedLegs = lr.filter((l) => !isLegHit(l));
      if (missedLegs.length === 1 && isLegMissedByOne(missedLegs[0])) return 'mb1';
    }
    return bet._status;
  };

  // Unique account labels for filter dropdown
  const accountLabels = [...new Set(bets.map((b) => b.account_label).filter(Boolean))].sort();

  // Filter (using effective status)
  const filtered = bets.filter((b) => {
    if (sportFilter !== 'All' && b._sport !== sportFilter) return false;
    if (statusFilter && getEffectiveStatus(b) !== statusFilter) return false;
    if (accountFilter !== 'All' && b.account_label !== accountFilter) return false;
    return true;
  });

  // Calculations (using effective status)
  const totalStaked = filtered.reduce((s, b) => s + b._stake, 0);
  const wonBets = filtered.filter((b) => { const s = getEffectiveStatus(b); return s === 'won' || s === 'mb1'; });
  const lostBets = filtered.filter((b) => getEffectiveStatus(b) === 'lost');
  const pendingBets = filtered.filter((b) => getEffectiveStatus(b) === 'pending');

  // For effectively-won bets that haven't been settled in DB yet, calculate profit from odds
  const wonProfit = wonBets.reduce((s, b) => {
    const profit = b._payout > 0 ? (b._payout - b._stake) : (b._odds - 1) * b._stake;
    return s + profit;
  }, 0);
  const lostStake = lostBets.reduce((s, b) => s + b._stake, 0);
  // MB1 bets are refunded — $0 P/L impact
  const settledPL = wonProfit - lostStake;

  const pendingPotential = pendingBets.reduce((s, b) => s + (b._odds * b._stake) - b._stake, 0);
  const ceiling = settledPL + pendingPotential;

  const pendingStake = pendingBets.reduce((s, b) => s + b._stake, 0);
  const floor = settledPL - pendingStake;

  const currentPL = settledPL;

  return (
    <div className="animate-fade-in">
      {/* Sport + Status Filters */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 20 }}>
        {/* Date picker */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <Calendar size={14} style={{ color: 'var(--text-muted)' }} />
          <button className="btn btn-secondary" style={{ padding: '6px 8px', fontSize: 12, lineHeight: 1 }}
            onClick={() => { const d = new Date(selectedDate + 'T12:00:00'); d.setDate(d.getDate() - 1); setSelectedDate(fmtDate(d)); }}>
            <ChevronLeft size={14} />
          </button>
          <input type="date" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)}
            style={{ padding: '5px 8px', fontSize: 12, borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer' }} />
          <button className="btn btn-secondary" style={{ padding: '6px 8px', fontSize: 12, lineHeight: 1 }}
            onClick={() => { const d = new Date(selectedDate + 'T12:00:00'); d.setDate(d.getDate() + 1); setSelectedDate(fmtDate(d)); }}>
            <ChevronRight size={14} />
          </button>
          {selectedDate !== todayStr() && (
            <button className="btn btn-primary" style={{ padding: '5px 10px', fontSize: 11 }}
              onClick={() => setSelectedDate(todayStr())}>
              Today
            </button>
          )}
        </div>
        <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />
        <Filter size={14} style={{ color: 'var(--text-muted)' }} />
        {SPORTS.map((s) => (
          <button key={s} onClick={() => setSportFilter(s)}
            className={sportFilter === s ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            {s}
          </button>
        ))}
        <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />
        {[{ l: 'All', v: '' }, { l: 'Pending', v: 'pending' }, { l: 'Won', v: 'won' }, { l: 'Lost', v: 'lost' }, { l: 'MB1', v: 'mb1' }].map((f) => (
          <button key={f.v} onClick={() => setStatusFilter(f.v)}
            className={statusFilter === f.v ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            {f.l}
          </button>
        ))}
        {accountLabels.length > 1 && (
          <select value={accountFilter} onChange={(e) => setAccountFilter(e.target.value)}
            className="t-input" style={{ padding: '5px 8px', fontSize: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-card)', color: 'var(--text-primary)' }}>
            <option value="All">All Accounts</option>
            {accountLabels.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {(legResultsLoading || checking) && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <Loader2 size={12} className="animate-spin" /> {checking ? 'Checking...' : 'Fetching stats...'}
            </span>
          )}
          <button onClick={() => setAutoRefresh((p) => !p)}
            className={autoRefresh ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            <Clock size={14} />
            {autoRefresh ? 'Auto 60s' : 'Auto'}
          </button>
          <button onClick={handleRefreshAll} disabled={checking || legResultsLoading} className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>
            {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
          <button onClick={handleSyncManualBets} disabled={syncing} className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Sync
          </button>
        </div>
        {syncResult && (
          <div style={{ fontSize: 12, padding: '8px 14px', marginTop: 8, borderRadius: 6, background: syncResult.imported > 0 ? 'var(--success-muted)' : 'var(--bg-card)', color: syncResult.error ? 'var(--danger)' : 'var(--text-secondary)' }}>
            {syncResult.error ? syncResult.error : syncResult.imported > 0 ? (
              <><CheckCircle size={13} style={{ color: 'var(--success)', verticalAlign: 'middle', marginRight: 4 }} />{syncResult.imported} manual bet{syncResult.imported !== 1 ? 's' : ''} imported</>
            ) : `No new manual bets found (${syncResult.accounts_checked?.length || 0} accounts checked)`}
          </div>
        )}
      </div>

      {/* Stats Cards: Floor / Current / Ceiling */}
      {filtered.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 24 }}>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Bets</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>{filtered.length}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              {wonBets.length}W / {lostBets.length}L / {pendingBets.length}P
            </div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Staked</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>${totalStaked.toFixed(2)}</div>
          </div>
          <div className="stat-card" style={{ padding: 16, borderLeft: '3px solid var(--danger)' }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--danger)', marginBottom: 6 }}>
              Floor (worst case)
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <TrendingDown size={16} style={{ color: 'var(--danger)' }} />
              <span style={{ fontSize: 22, fontWeight: 700, color: 'var(--danger)' }}>
                {floor >= 0 ? '+' : ''}${floor.toFixed(2)}
              </span>
            </div>
          </div>
          <div className="stat-card" style={{ padding: 16, borderLeft: '3px solid var(--primary)' }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--primary)', marginBottom: 6 }}>
              Current P/L (settled)
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {currentPL >= 0 ? <TrendingUp size={16} style={{ color: 'var(--success)' }} /> : <TrendingDown size={16} style={{ color: 'var(--danger)' }} />}
              <span style={{ fontSize: 22, fontWeight: 700, color: currentPL >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                {currentPL >= 0 ? '+' : ''}${currentPL.toFixed(2)}
              </span>
            </div>
          </div>
          <div className="stat-card" style={{ padding: 16, borderLeft: '3px solid var(--success)' }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--success)', marginBottom: 6 }}>
              Ceiling (best case)
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <TrendingUp size={16} style={{ color: 'var(--success)' }} />
              <span style={{ fontSize: 22, fontWeight: 700, color: 'var(--success)' }}>
                {ceiling >= 0 ? '+' : ''}${ceiling.toFixed(2)}
              </span>
            </div>
          </div>
          {wonBets.length + lostBets.length > 0 && (
            <div className="stat-card" style={{ padding: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Win Rate</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
                {((wonBets.length / (wonBets.length + lostBets.length)) * 100).toFixed(1)}%
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13, marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Bet Cards */}
      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-secondary)', padding: '48px 0' }}>
          <Loader2 size={16} className="animate-spin" /> Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}>
          No bets found for {selectedDate === todayStr() ? 'today' : new Date(selectedDate + 'T00:00:00').toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'short' })}.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filtered.map((bet) => {
            const legs = bet.legs || [];
            const potReturn = bet._odds * bet._stake;
            const betLegResults = legResults[bet.id] || [];
            const effectiveStatus = getEffectiveStatus(bet);
            const isWon = effectiveStatus === 'won';
            const isLost = effectiveStatus === 'lost';
            const isMb1 = effectiveStatus === 'mb1';
            const borderColor = isWon ? 'var(--success)' : isMb1 ? '#3b82f6' : isLost ? 'var(--danger)' : 'var(--warning)';

            return (
              <div key={bet.id} className="card" style={{
                padding: 0, overflow: 'hidden',
                borderLeft: `3px solid ${borderColor}`,
              }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="badge badge-neutral" style={{ fontSize: 11 }}>{bet.account_label || '-'}</span>
                    <span className={`badge ${bet.bet_type === 'SGM' ? 'badge-purple' : 'badge-accent'}`} style={{ fontSize: 11 }}>{bet.bet_type}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{bet._sport}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <StatusBadge status={effectiveStatus} />
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {bet.placed_at ? new Date(bet.placed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                    </span>
                  </div>
                </div>

                {/* Legs */}
                <div style={{ padding: '10px 16px' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {legs.map((leg, j) => (
                      <LegStatus
                        key={j}
                        leg={leg}
                        betStatus={bet.status}
                        legResult={betLegResults[j] || null}
                      />
                    ))}
                  </div>
                </div>

                {/* Footer */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 16px', borderTop: '1px solid var(--border)', background: 'var(--secondary)' }}>
                  <div style={{ fontSize: 12, display: 'flex', gap: 16 }}>
                    <span><span style={{ color: 'var(--text-muted)' }}>Stake </span><span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>${bet._stake.toFixed(2)}</span></span>
                    <span><span style={{ color: 'var(--text-muted)' }}>Odds </span><span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{bet._odds.toFixed(2)}</span></span>
                    <span><span style={{ color: 'var(--text-muted)' }}>Liability </span><span style={{ color: 'var(--text-muted)' }}>${((bet._odds - 1) * bet._stake).toFixed(0)}</span></span>
                  </div>
                  <div>
                    {isWon && <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--success)' }}>+${(bet._payout > 0 ? (bet._payout - bet._stake) : (bet._odds - 1) * bet._stake).toFixed(2)}</span>}
                    {isMb1 && <span style={{ fontSize: 16, fontWeight: 700, color: '#3b82f6' }}>+${((bet._odds - 1) * bet._stake).toFixed(2)}</span>}
                    {isLost && <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--danger)' }}>-${bet._stake.toFixed(2)}</span>}
                    {!isWon && !isLost && !isMb1 && <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--warning)' }}>Pot. ${potReturn.toFixed(2)}</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function detectSport(bet) {
  const legs = bet.legs || [];
  const text = JSON.stringify(legs).toLowerCase();
  if (text.includes('afl') || text.includes('disp') || text.includes('disposal')) return 'AFL';
  if (text.includes('nba') || text.includes('pts') || text.includes('points') || text.includes('rebounds')) return 'NBA';
  if (text.includes('nrl') || text.includes('rugby')) return 'NRL';
  return 'Other';
}
