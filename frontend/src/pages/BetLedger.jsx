import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import {
  BookOpen,
  Filter,
  TrendingUp,
  TrendingDown,
  Loader2,
  RefreshCw,
  CheckCircle,
  XCircle,
  Clock,
} from 'lucide-react';

const METHOD_OPTIONS = [
  { label: 'All', value: '' },
  { label: 'Allocation', value: 'allocation' },
  { label: 'Expload', value: 'expload' },
  { label: 'CSB', value: 'csb' },
  { label: 'Manual', value: 'manual' },
];

const STATUS_OPTIONS = [
  { label: 'All', value: '' },
  { label: 'Placed', value: 'placed' },
  { label: 'Won', value: 'won' },
  { label: 'Lost', value: 'lost' },
  { label: 'Void', value: 'void' },
];

function StatusBadge({ status }) {
  if (status === 'won')
    return <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><CheckCircle size={10} /> Won</span>;
  if (status === 'lost')
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> Lost</span>;
  if (status === 'placed')
    return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Clock size={10} /> Placed</span>;
  if (status === 'void')
    return <span className="badge badge-neutral" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>Void</span>;
  return <span className="badge badge-neutral">{status || 'pending'}</span>;
}

function MethodBadge({ method }) {
  const colors = {
    allocation: 'badge-accent',
    expload: 'badge-purple',
    csb: 'badge-success',
    manual: 'badge-neutral',
  };
  return (
    <span className={`badge ${colors[method] || 'badge-neutral'}`} style={{ fontSize: 11 }}>
      {method || '-'}
    </span>
  );
}

export default function BetLedger() {
  const [bets, setBets] = useState([]);
  const [stats, setStats] = useState(null);
  const [methodFilter, setMethodFilter] = useState('');
  const [brandFilter, setBrandFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getBetHistory(undefined, undefined, 2000);
      const allBets = (data.bets || []).map((b) => ({
        ...b,
        brand: b.account_label || b.account_number || '',
        method: b.source || (b.bet_type === 'Manual' ? 'manual' : ''),
        status: (b.status || '').toLowerCase(),
        _stake: parseFloat(String(b.stake || 0).replace(/[$,]/g, '')) || 0,
        _payout: parseFloat(String(b.payout || 0).replace(/[$,]/g, '')) || 0,
        _odds: parseFloat(String(b.combined_odds || 0).replace(/[$,]/g, '')) || 0,
      }));
      setBets(allBets);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Client-side filtering
  const filteredBets = bets.filter((b) => {
    if (statusFilter && b.status !== statusFilter) return false;
    if (brandFilter && b.brand !== brandFilter) return false;
    if (methodFilter && b.method !== methodFilter) return false;
    return true;
  });

  // Collect unique brands from ALL bets for filter dropdown
  const brands = [...new Set(bets.map((b) => b.brand).filter(Boolean))].sort();

  // Stats from filtered bets
  const totalStaked = filteredBets.reduce((s, b) => s + b._stake, 0);
  const wonBets = filteredBets.filter((b) => b.status === 'won');
  const lostBets = filteredBets.filter((b) => b.status === 'lost');
  const totalWon = wonBets.reduce((s, b) => s + b._payout, 0);
  const settledStake = [...wonBets, ...lostBets].reduce((s, b) => s + b._stake, 0);
  const pl = totalWon - settledStake;
  const totalBets = filteredBets.length;
  const wonCount = wonBets.length;
  const lostCount = lostBets.length;
  const pendingCount = filteredBets.filter((b) => b.status === 'pending').length;

  return (
    <div className="animate-fade-in">
      {/* Stats strip */}
      {totalBets > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 24 }}>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Total Bets</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>{totalBets}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              {wonCount}W / {lostCount}L / {pendingCount}P
            </div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Total Staked</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>${totalStaked.toFixed(2)}</div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>Total Won</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: totalWon > 0 ? 'var(--success)' : 'var(--text-primary)' }}>${totalWon.toFixed(2)}</div>
          </div>
          <div className="stat-card" style={{ padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>P/L</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {pl >= 0 ? <TrendingUp size={16} style={{ color: 'var(--success)' }} /> : <TrendingDown size={16} style={{ color: 'var(--danger)' }} />}
              <span style={{ fontSize: 22, fontWeight: 700, color: pl >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                {pl >= 0 ? '+' : ''}${pl.toFixed(2)}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Method breakdown */}

      {/* Filter bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 20 }}>
        <Filter size={14} style={{ color: 'var(--text-muted)' }} />

        {/* Method filter */}
        {METHOD_OPTIONS.map((f) => (
          <button
            key={f.value}
            onClick={() => setMethodFilter(f.value)}
            className={methodFilter === f.value ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}
          >
            {f.label}
          </button>
        ))}

        <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />

        {/* Status filter */}
        {STATUS_OPTIONS.map((f) => (
          <button
            key={f.value}
            onClick={() => setStatusFilter(f.value)}
            className={statusFilter === f.value ? 'btn btn-primary' : 'btn btn-secondary'}
            style={{ padding: '6px 14px', fontSize: 12 }}
          >
            {f.label}
          </button>
        ))}

        {/* Brand filter */}
        {brands.length > 1 && (
          <>
            <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 4px' }} />
            <select
              value={brandFilter}
              onChange={(e) => setBrandFilter(e.target.value)}
              className="t-input"
              style={{ border: '1px solid var(--border)', padding: '6px 10px', fontSize: 12 }}
            >
              <option value="">All Accounts</option>
              {brands.map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </>
        )}

        {/* Refresh */}
        <div style={{ marginLeft: 'auto' }}>
          <button onClick={fetchData} disabled={loading} className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12 }}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>
      </div>

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
      ) : filteredBets.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}>
          <BookOpen size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
          <div>No bets found.</div>
        </div>
      ) : (
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
              {filteredBets.map((bet) => {
                const stake = parseFloat(String(bet.stake || 0).replace(/[$,]/g, '')) || 0;
                const odds = parseFloat(String(bet.combined_odds || 0).replace(/[$,]/g, '')) || 0;
                const payout = parseFloat(String(bet.payout || 0).replace(/[$,]/g, '')) || 0;
                const legs = bet.legs || [];
                return (
                  <tr key={bet.id}>
                    <td style={{ color: 'var(--text-secondary)', whiteSpace: 'nowrap', fontSize: 12 }}>
                      <div>{bet.placed_at ? new Date(bet.placed_at).toLocaleDateString() : '-'}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{bet.placed_at ? new Date(bet.placed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}</div>
                    </td>
                    <td>
                      <span className="badge badge-neutral" style={{ fontSize: 11 }}>{bet.account_label || bet.brand || '-'}</span>
                    </td>
                    <td>
                      <span className={`badge ${bet.bet_type === 'SGM' ? 'badge-purple' : 'badge-accent'}`} style={{ fontSize: 11 }}>
                        {bet.bet_type || '-'}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        {legs.length > 0 ? legs.map((leg, j) => (
                          <span key={j} style={{ fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280, display: 'block' }}>
                            {typeof leg === 'string' ? leg : leg.name || `Leg ${j + 1}`}
                          </span>
                        )) : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>-</span>}
                      </div>
                    </td>
                    <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500 }}>{odds > 0 ? odds.toFixed(2) : '-'}</td>
                    <td style={{ textAlign: 'right', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>${stake.toFixed(2)}</td>
                    <td><StatusBadge status={bet.status} /></td>
                    <td style={{ textAlign: 'right', color: bet.status === 'won' ? 'var(--success)' : 'var(--text-secondary)', fontWeight: payout > 0 ? 600 : 400 }}>
                      {payout > 0 ? `$${payout.toFixed(2)}` : '-'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
