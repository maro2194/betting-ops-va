import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';
import {
  TrendingUp, TrendingDown, Loader2, RefreshCw,
  CheckCircle, XCircle, Clock, Filter,
} from 'lucide-react';

const SPORTS = ['All', 'AFL', 'NBA', 'NRL'];

const parseMoney = (v) => parseFloat(String(v || '0').replace(/[$,]/g, '')) || 0;

function StatusBadge({ status }) {
  const s = (status || '').toLowerCase();
  if (s === 'won') return <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><CheckCircle size={10} /> Won</span>;
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

    // Build the stat display e.g. "15+ Disposals → 18"
    let statDisplay = null;
    if (legResult.actual !== null && legResult.actual !== undefined && legResult.line !== null) {
      const lineLabel = `${legResult.line}+ ${legResult.stat || ''}`.trim();
      const actualLabel = `${legResult.actual}`;
      statDisplay = (
        <span style={{
          marginLeft: 6,
          fontSize: 11,
          color: iconColor,
          fontWeight: 600,
          whiteSpace: 'nowrap',
        }}>
          {lineLabel} → {actualLabel} {isWon ? '✓' : isLost ? '✗' : ''}
        </span>
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

  // Fallback: use bet-level status (old behaviour)
  const s = (betStatus || '').toLowerCase();
  let color = 'var(--text-secondary)';
  let icon = null;
  if (s === 'won') {
    color = 'var(--success)';
    icon = <CheckCircle size={11} style={{ color, flexShrink: 0 }} />;
  } else if (s === 'lost') {
    color = 'var(--danger)';
    icon = <XCircle size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />;
  } else {
    icon = <Clock size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />;
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
      {icon}
      <span style={{ color }}>{legText}</span>
    </div>
  );
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

  // Track which bet IDs we've already fetched leg results for to avoid re-fetching
  const fetchedBetIds = useRef(new Set());

  const fetchBets = useCallback(async () => {
    try {
      // Use local date (AEST), not UTC — bets at 9am AEST are stored as previous day UTC
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
      const data = await api.getBetHistory(undefined, undefined, 500, today, today);
      const allBets = (data.bets || []).map((b) => ({
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
  }, []);

  // Fetch per-leg results only for PENDING bets (settled bets already have outcomes)
  const fetchLegResults = useCallback(async (betsToCheck) => {
    const eligible = betsToCheck.filter((b) => {
      if (fetchedBetIds.current.has(b.id)) return false;
      if (b._status !== 'pending') return false; // Only check pending bets
      const legs = b.legs || [];
      if (!legs.length) return false;
      const text = JSON.stringify(legs).toUpperCase();
      return text.includes('AFL') || text.includes('NBA');
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
    fetchBets().then((allBets) => {
      if (allBets.length) fetchLegResults(allBets);
    });
  }, [fetchBets, fetchLegResults]);

  const handleCheckResults = async () => {
    setChecking(true);
    setError('');
    try {
      await api.checkResults();
      const allBets = await fetchBets();
      // Re-fetch leg results for any bets that now have legs but haven't been checked
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

  const handleRefreshLegResults = async () => {
    // Force re-fetch leg results for currently visible bets
    fetchedBetIds.current.clear();
    fetchLegResults(bets);
  };

  // Filter
  const filtered = bets.filter((b) => {
    if (sportFilter !== 'All' && b._sport !== sportFilter) return false;
    if (statusFilter && b._status !== statusFilter) return false;
    return true;
  });

  // Calculations
  const totalStaked = filtered.reduce((s, b) => s + b._stake, 0);
  const wonBets = filtered.filter((b) => b._status === 'won');
  const lostBets = filtered.filter((b) => b._status === 'lost');
  const pendingBets = filtered.filter((b) => b._status === 'pending');

  const wonReturn = wonBets.reduce((s, b) => s + b._payout, 0);
  const settledStake = [...wonBets, ...lostBets].reduce((s, b) => s + b._stake, 0);
  const settledPL = wonReturn - settledStake;

  const pendingPotential = pendingBets.reduce((s, b) => s + (b._odds * b._stake) - b._stake, 0);
  const ceiling = settledPL + pendingPotential;

  const pendingStake = pendingBets.reduce((s, b) => s + b._stake, 0);
  const floor = settledPL - pendingStake;

  const currentPL = settledPL;

  return (
    <div className="animate-fade-in">
      {/* Sport + Status Filters */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 20 }}>
        <Filter size={14} style={{ color: 'var(--text-muted)' }} />
        {SPORTS.map((s) => (
          <button key={s} onClick={() => setSportFilter(s)}
            className={sportFilter === s ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            {s}
          </button>
        ))}
        <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />
        {[{ l: 'All', v: '' }, { l: 'Pending', v: 'pending' }, { l: 'Won', v: 'won' }, { l: 'Lost', v: 'lost' }].map((f) => (
          <button key={f.v} onClick={() => setStatusFilter(f.v)}
            className={statusFilter === f.v ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}>
            {f.l}
          </button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {legResultsLoading && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <Loader2 size={12} className="animate-spin" /> Fetching stats...
            </span>
          )}
          <button onClick={handleRefreshLegResults} disabled={legResultsLoading} className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>
            <RefreshCw size={14} />
            Stats
          </button>
          <button onClick={handleCheckResults} disabled={checking} className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>
            {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {checking ? 'Checking...' : 'Check Results'}
          </button>
          <button onClick={handleSyncManualBets} disabled={syncing} className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {syncing ? 'Syncing...' : 'Sync Manual Bets'}
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
          No bets found for today.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filtered.map((bet) => {
            const legs = bet.legs || [];
            const potReturn = bet._odds * bet._stake;
            const isWon = bet._status === 'won';
            const isLost = bet._status === 'lost';
            const borderColor = isWon ? 'var(--success)' : isLost ? 'var(--danger)' : 'var(--warning)';
            const betLegResults = legResults[bet.id] || [];

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
                    <StatusBadge status={bet.status} />
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
                    {isWon && <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--success)' }}>+${bet._payout.toFixed(2)}</span>}
                    {isLost && <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--danger)' }}>-${bet._stake.toFixed(2)}</span>}
                    {!isWon && !isLost && <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--warning)' }}>Pot. ${potReturn.toFixed(2)}</span>}
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
