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
      const params = new URLSearchParams();
      if (methodFilter) params.set('method', methodFilter);
      if (brandFilter) params.set('brand', brandFilter);
      if (statusFilter) params.set('status', statusFilter);
      params.set('limit', '200');

      const statsParams = new URLSearchParams();
      if (methodFilter) statsParams.set('method', methodFilter);

      const [betsResp, statsResp] = await Promise.all([
        api.get(`/api/multi/bets?${params}`),
        api.get(`/api/multi/bets/stats?${statsParams}`),
      ]);
      setBets(betsResp.bets || []);
      setStats(statsResp);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [methodFilter, brandFilter, statusFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Collect unique brands from bets for filter dropdown
  const brands = [...new Set(bets.map((b) => b.brand).filter(Boolean))].sort();

  const totalStaked = stats?.total_staked || 0;
  const totalWon = stats?.total_won || 0;
  const pl = stats?.pl || 0;
  const totalBets = stats?.total_bets || 0;
  const wonCount = stats?.won_count || 0;
  const lostCount = stats?.lost_count || 0;
  const pendingCount = stats?.pending_count || 0;

  return (
    <div className="animate-fade-in">
      {/* Stats strip */}
      {stats && totalBets > 0 && (
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

      {/* Method breakdown when "All" is selected */}
      {stats && !methodFilter && stats.by_method && stats.by_method.length > 1 && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 20 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.04em' }}>By Method</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, fontSize: 13 }}>
            {stats.by_method.map((m) => (
              <div key={m.method} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <MethodBadge method={m.method} />
                <span style={{ color: 'var(--text-secondary)' }}>{m.total_bets} bets</span>
                <span style={{ color: 'var(--text-muted)' }}>${m.total_staked.toFixed(2)} staked</span>
                <span style={{ color: m.pl >= 0 ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                  {m.pl >= 0 ? '+' : ''}${m.pl.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

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
              <option value="">All Bookies</option>
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
      ) : bets.length === 0 ? (
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
                <th>Track</th>
                <th>Race</th>
                <th>Horse</th>
                <th>Bookie</th>
                <th>Initials</th>
                <th style={{ textAlign: 'right' }}>Stake</th>
                <th style={{ textAlign: 'right' }}>Odds</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Payout</th>
                <th>Method</th>
              </tr>
            </thead>
            <tbody>
              {bets.map((bet) => (
                <tr key={bet.id}>
                  <td style={{ color: 'var(--text-secondary)', whiteSpace: 'nowrap', fontSize: 12 }}>
                    {bet.placed_at ? new Date(bet.placed_at).toLocaleDateString() : '-'}
                  </td>
                  <td style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{bet.track || '-'}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{bet.race_number || '-'}</td>
                  <td style={{ color: 'var(--text-primary)' }}>{bet.horse || '-'}</td>
                  <td>
                    <span className="badge badge-neutral" style={{ fontSize: 11 }}>{bet.brand || '-'}</span>
                  </td>
                  <td style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{bet.initials || '-'}</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                    ${(bet.stake || 0).toFixed(2)}
                    {bet.stake_type && bet.stake_type !== 'cash' && (
                      <span style={{ fontSize: 10, color: 'var(--warning)', marginLeft: 4 }}>{bet.stake_type}</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500 }}>
                    {bet.odds ? bet.odds.toFixed(2) : '-'}
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <StatusBadge status={bet.status} />
                      {bet.bet_reference && (
                        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>#{bet.bet_reference}</span>
                      )}
                    </div>
                  </td>
                  <td style={{ textAlign: 'right', color: bet.status === 'won' ? 'var(--success)' : 'var(--text-secondary)', fontWeight: bet.payout ? 600 : 400 }}>
                    {bet.payout ? `$${bet.payout.toFixed(2)}` : '-'}
                  </td>
                  <td>
                    <MethodBadge method={bet.method} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
