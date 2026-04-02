import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import { Filter, CheckCircle, TrendingUp, TrendingDown, Loader2, LayoutGrid, List, RefreshCw } from 'lucide-react';

const STATUS_MAP = {
  Won: 'badge-success',
  won: 'badge-success',
  Lost: 'badge-danger',
  lost: 'badge-danger',
  Pending: 'badge-warning',
  pending: 'badge-warning',
};

function StatusBadge({ status }) {
  const cls = STATUS_MAP[status] || 'badge-neutral';
  return <span className={`badge ${cls}`}>{status}</span>;
}

function LegsCell({ legs }) {
  if (!legs || legs.length === 0) return <span style={{ color: 'var(--text-muted)' }}>-</span>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {legs.map((leg, i) => (
        <p key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280 }}>
          {typeof leg === 'string' ? leg : leg.name || leg.eventName || `Leg ${i + 1}`}
        </p>
      ))}
    </div>
  );
}

const parseMoney = (v) => parseFloat(String(v || '0').replace(/[$,]/g, '')) || 0;

export default function History() {
  const { sessions } = useSessions();
  const [bets, setBets] = useState([]);
  const [filter, setFilter] = useState('');
  const [accountFilter, setAccountFilter] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [viewMode, setViewMode] = useState('table'); // 'table' or 'card'
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [error, setError] = useState('');

  const fetchBets = useCallback(async () => {
    try {
      const data = await api.getBetHistory(
        filter || undefined,
        accountFilter || undefined,
        500,
        dateFrom || undefined,
        dateTo || undefined,
      );
      setBets(data.bets || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filter, accountFilter, dateFrom, dateTo]);

  useEffect(() => {
    setLoading(true);
    fetchBets();
  }, [fetchBets]);

  const handleCheckResults = async () => {
    setChecking(true);
    setError('');
    setCheckResult(null);
    try {
      const result = await api.checkResults();
      setCheckResult(result);
      await fetchBets();
    } catch (err) {
      setError(err.message);
    } finally {
      setChecking(false);
    }
  };

  const handleSyncManualBets = async () => {
    setSyncing(true);
    setError('');
    setSyncResult(null);
    try {
      const result = await api.syncManualBets();
      setSyncResult(result);
      if (result.imported > 0) await fetchBets();
    } catch (err) {
      setError(err.message);
    } finally {
      setSyncing(false);
    }
  };

  const statusFilters = [
    { label: 'All', value: '' },
    { label: 'Pending', value: 'Pending' },
    { label: 'Won', value: 'Won' },
    { label: 'Lost', value: 'Lost' },
  ];

  // Get unique accounts from bets
  const accounts = [...new Set(bets.map((b) => b.account_label || b.account_number).filter(Boolean))];

  const totalStake = bets.reduce((s, b) => s + parseMoney(b.stake), 0);
  const totalPayout = bets.reduce((s, b) => s + parseMoney(b.payout), 0);
  // P/L from settled bets only
  const settledBets = bets.filter((b) => b.status === 'Won' || b.status === 'won' || b.status === 'Lost' || b.status === 'lost');
  const settledStake = settledBets.reduce((s, b) => s + parseMoney(b.stake), 0);
  const settledPayout = settledBets.reduce((s, b) => s + parseMoney(b.payout), 0);
  const pl = settledPayout - settledStake;
  const wonCount = bets.filter((b) => b.status === 'Won' || b.status === 'won').length;
  const lostCount = bets.filter((b) => b.status === 'Lost' || b.status === 'lost').length;
  const pendingCount = bets.filter((b) => b.status === 'Pending' || b.status === 'pending').length;

  return (
    <div className="animate-fade-in">
      {/* Stat row */}
      {bets.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 24 }}>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Total Bets</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>{bets.length}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              {wonCount}W / {lostCount}L / {pendingCount}P
            </div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Staked</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>${totalStake.toFixed(2)}</div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Returned</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: totalPayout >= totalStake ? 'var(--success)' : 'var(--danger)' }}>${totalPayout.toFixed(2)}</div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>P/L (Settled)</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {pl >= 0 ? <TrendingUp size={16} style={{ color: 'var(--success)' }} /> : <TrendingDown size={16} style={{ color: 'var(--danger)' }} />}
              <span style={{ fontSize: 22, fontWeight: 700, color: pl >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                {pl >= 0 ? '+' : ''}${pl.toFixed(2)}
              </span>
            </div>
          </div>
          {wonCount + lostCount > 0 && (
            <div className="stat-card" style={{ padding: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Win Rate</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
                {((wonCount / (wonCount + lostCount)) * 100).toFixed(1)}%
              </div>
            </div>
          )}
        </div>
      )}

      {/* Filter bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 20 }}>
        <Filter size={14} style={{ color: 'var(--text-muted)' }} />
        {statusFilters.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={filter === f.value ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}
          >
            {f.label}
          </button>
        ))}

        {/* Account filter */}
        {accounts.length > 0 && (
          <>
            <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />
            <select
              value={accountFilter}
              onChange={(e) => { setAccountFilter(e.target.value); setLoading(true); }}
              className="t-input"
              style={{ border: '1px solid var(--border)', padding: '6px 10px', fontSize: 12 }}
            >
              <option value="">All Accounts</option>
              {accounts.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </>
        )}

        {/* Date filter */}
        <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => { setDateFrom(e.target.value); setLoading(true); }}
          className="t-input"
          style={{ border: '1px solid var(--border)', padding: '6px 10px', fontSize: 12, width: 130 }}
          title="From date"
        />
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>to</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => { setDateTo(e.target.value); setLoading(true); }}
          className="t-input"
          style={{ border: '1px solid var(--border)', padding: '6px 10px', fontSize: 12, width: 130 }}
          title="To date"
        />
        {(dateFrom || dateTo) && (
          <button
            onClick={() => { setDateFrom(''); setDateTo(''); setLoading(true); }}
            className="btn btn-ghost"
            style={{ padding: '4px 8px', fontSize: 11 }}
          >
            Clear dates
          </button>
        )}

        {/* Right side: view toggle + check results */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ display: 'flex', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
            <button
              onClick={() => setViewMode('table')}
              className="btn-ghost"
              style={{
                padding: '5px 8px', borderRadius: 'var(--radius) 0 0 var(--radius)',
                background: viewMode === 'table' ? 'var(--secondary)' : 'transparent',
              }}
              title="Table view"
            >
              <List size={14} style={{ color: viewMode === 'table' ? 'var(--text-primary)' : 'var(--text-muted)' }} />
            </button>
            <button
              onClick={() => setViewMode('card')}
              className="btn-ghost"
              style={{
                padding: '5px 8px', borderRadius: '0 var(--radius) var(--radius) 0',
                background: viewMode === 'card' ? 'var(--secondary)' : 'transparent',
              }}
              title="Card view"
            >
              <LayoutGrid size={14} style={{ color: viewMode === 'card' ? 'var(--text-primary)' : 'var(--text-muted)' }} />
            </button>
          </div>
          <button
            onClick={handleSyncManualBets}
            disabled={syncing}
            className="btn btn-secondary"
            style={{ padding: '6px 14px', fontSize: 12 }}
          >
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {syncing ? 'Syncing...' : 'Sync Manual Bets'}
          </button>
          <button
            onClick={handleCheckResults}
            disabled={checking}
            className="btn btn-secondary"
            style={{ padding: '6px 14px', fontSize: 12 }}
          >
            {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {checking ? 'Checking...' : 'Check Results'}
          </button>
        </div>
      </div>

      {/* Check results feedback */}
      {checkResult && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 16, fontSize: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
              {checkResult.updated > 0 ? (
                <><CheckCircle size={14} style={{ color: 'var(--success)', verticalAlign: 'middle', marginRight: 4 }} />{checkResult.updated} bet{checkResult.updated !== 1 ? 's' : ''} updated</>
              ) : (
                'No updates found'
              )}
            </span>
            {checkResult.accounts_checked?.length > 0 && (
              <span style={{ color: 'var(--text-muted)' }}>
                Checked: {checkResult.accounts_checked.join(', ')}
              </span>
            )}
            {checkResult.accounts_failed?.length > 0 && (
              <span style={{ color: 'var(--warning)' }}>
                Failed: {checkResult.accounts_failed.map((a) => `${a.account} (${a.error})`).join(', ')}
              </span>
            )}
            {checkResult.pending_remaining > 0 && (
              <span style={{ color: 'var(--text-muted)' }}>
                {checkResult.pending_remaining} still pending
              </span>
            )}
          </div>
        </div>
      )}

      {/* Sync manual bets feedback */}
      {syncResult && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 16, fontSize: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
              {syncResult.imported > 0 ? (
                <><CheckCircle size={14} style={{ color: 'var(--success)', verticalAlign: 'middle', marginRight: 4 }} />{syncResult.imported} manual bet{syncResult.imported !== 1 ? 's' : ''} imported</>
              ) : (
                'No new manual bets found'
              )}
            </span>
            {syncResult.accounts_checked?.length > 0 && (
              <span style={{ color: 'var(--text-muted)' }}>
                Checked: {syncResult.accounts_checked.join(', ')}
              </span>
            )}
            {syncResult.accounts_failed?.length > 0 && (
              <span style={{ color: 'var(--warning)' }}>
                Failed: {syncResult.accounts_failed.map((a) => `${a.account} (${a.error})`).join(', ')}
              </span>
            )}
          </div>
        </div>
      )}

      {error && (
        <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13, marginBottom: 16 }}>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-secondary)', padding: '48px 0' }}>
          <Loader2 size={16} className="animate-spin" />
          Loading bets...
        </div>
      ) : bets.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}>
          No bets found.
        </div>
      ) : viewMode === 'table' ? (
        /* ─── Table View ─── */
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Account</th>
                <th>Type</th>
                <th>Legs</th>
                <th style={{ textAlign: 'right' }}>Odds</th>
                <th style={{ textAlign: 'right' }}>Stake</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Payout</th>
              </tr>
            </thead>
            <tbody>
              {bets.map((bet) => (
                <tr key={bet.id}>
                  <td style={{ color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                    <div>{new Date(bet.placed_at).toLocaleDateString()}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{new Date(bet.placed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                  </td>
                  <td>
                    <span className="badge badge-neutral">{bet.account_label || bet.account_number || '-'}</span>
                  </td>
                  <td>
                    <span className={`badge ${bet.bet_type === 'SGM' ? 'badge-purple' : 'badge-accent'}`}>
                      {bet.bet_type || 'SGM'}
                    </span>
                  </td>
                  <td><LegsCell legs={bet.legs} /></td>
                  <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500 }}>{parseMoney(bet.combined_odds).toFixed(2)}</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>${parseMoney(bet.stake).toFixed(2)}</td>
                  <td><StatusBadge status={bet.status} /></td>
                  <td style={{ textAlign: 'right', color: bet.status === 'Won' || bet.status === 'won' ? 'var(--success)' : 'var(--text-secondary)', fontWeight: bet.payout ? 600 : 400 }}>
                    {bet.payout ? `$${parseMoney(bet.payout).toFixed(2)}` : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        /* ─── Card View ─── */
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
          {bets.map((bet) => {
            const odds = parseMoney(bet.combined_odds);
            const stake = parseMoney(bet.stake);
            const payout = parseMoney(bet.payout);
            const isWon = bet.status === 'Won' || bet.status === 'won';
            const isLost = bet.status === 'Lost' || bet.status === 'lost';
            const isPending = bet.status === 'Pending' || bet.status === 'pending';

            return (
              <div
                key={bet.id}
                className="card"
                style={{
                  padding: 0,
                  overflow: 'hidden',
                  borderLeft: `3px solid ${isWon ? 'var(--success)' : isLost ? 'var(--destructive)' : 'var(--warning)'}`,
                }}
              >
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="badge badge-neutral" style={{ fontSize: 11 }}>{bet.account_label || bet.account_number}</span>
                    <span className={`badge ${bet.bet_type === 'SGM' ? 'badge-purple' : 'badge-accent'}`} style={{ fontSize: 11 }}>
                      {bet.bet_type || 'SGM'}
                    </span>
                  </div>
                  <StatusBadge status={bet.status} />
                </div>

                {/* Legs */}
                <div style={{ padding: '12px 16px' }}>
                  {bet.legs && bet.legs.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {bet.legs.map((leg, i) => (
                        <div key={i} style={{ fontSize: 13, color: 'var(--text-primary)' }}>
                          {typeof leg === 'string' ? leg : leg.name || leg.eventName || `Leg ${i + 1}`}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>No leg details</span>
                  )}
                </div>

                {/* Footer */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderTop: '1px solid var(--border)', background: 'var(--secondary)' }}>
                  <div style={{ fontSize: 12 }}>
                    <span style={{ color: 'var(--text-muted)' }}>Odds </span>
                    <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{odds.toFixed(2)}</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: 12 }}>Stake </span>
                    <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>${stake.toFixed(2)}</span>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    {isWon && (
                      <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--success)' }}>+${payout.toFixed(2)}</span>
                    )}
                    {isLost && (
                      <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--destructive)' }}>-${stake.toFixed(2)}</span>
                    )}
                    {isPending && (
                      <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--warning)' }}>Pot. ${(odds * stake).toFixed(2)}</span>
                    )}
                  </div>
                </div>

                {/* Date */}
                <div style={{ padding: '6px 16px 8px', fontSize: 11, color: 'var(--text-muted)' }}>
                  {new Date(bet.placed_at).toLocaleString()}
                  {bet.tsn && <span style={{ marginLeft: 8 }}>TSN: {bet.tsn}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
