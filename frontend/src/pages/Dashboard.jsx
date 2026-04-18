import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import { User, Wifi, WifiOff, RefreshCw, Trash2, LogIn, Plus, X, TrendingUp, TrendingDown, Clock, Activity, LayoutGrid, List, Pencil } from 'lucide-react';

function StatCard({ label, value, icon: Icon, trend, trendLabel, color }) {
  return (
    <div className="stat-card">
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {label}
        </span>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 'var(--radius)',
            background: color || 'var(--accent-muted)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Icon size={16} style={{ color: 'var(--primary)' }} />
        </div>
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.1 }}>
        {value}
      </div>
      {trend !== undefined && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 8, fontSize: 12 }}>
          {trend >= 0 ? (
            <TrendingUp size={14} style={{ color: 'var(--success)' }} />
          ) : (
            <TrendingDown size={14} style={{ color: 'var(--danger)' }} />
          )}
          <span style={{ color: trend >= 0 ? 'var(--success)' : 'var(--danger)', fontWeight: 500 }}>
            {trendLabel}
          </span>
        </div>
      )}
    </div>
  );
}

function AccountModal({ onClose, onSaved, editAccount }) {
  const isEdit = !!editAccount;
  const [form, setForm] = useState(
    isEdit
      ? { label: editAccount.label || '', email: editAccount.email || '', password: editAccount.password || '', proxy_url: editAccount.proxyUrl || '', account_number: editAccount.accountNumber || '' }
      : { label: '', email: '', password: '', proxy_url: '', account_number: '' }
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const payload = {
        label: form.label,
        email: form.email,
        password: form.password,
        proxy_url: form.proxy_url || undefined,
        account_number: form.account_number || undefined,
      };
      if (isEdit) payload.id = editAccount.id;
      await api.addAccount(payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const fieldLabels = {
    label: 'Label',
    email: 'Email',
    password: 'Password',
    proxy_url: 'Proxy URL',
    account_number: 'Account Number',
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 50,
        padding: 16,
        backdropFilter: 'blur(4px)',
      }}
      onClick={onClose}
    >
      <div
        className="card animate-fade-in"
        style={{ width: '100%', maxWidth: 420, padding: 24 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>{isEdit ? 'Edit Account' : 'Add Account'}</h2>
          <button onClick={onClose} className="btn-ghost" style={{ padding: 4, borderRadius: 'var(--radius-sm)' }} aria-label="Close">
            <X size={18} style={{ color: 'var(--text-muted)' }} />
          </button>
        </div>
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {['label', 'email', 'password', 'proxy_url', 'account_number'].map((field) => (
            <div key={field}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>
                {fieldLabels[field]}
              </label>
              <input
                type={field === 'password' ? 'password' : 'text'}
                value={form[field]}
                onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
                required={['label', 'email', 'password'].includes(field)}
                placeholder={field === 'proxy_url' ? 'http://user:pass@host:port' : field === 'account_number' ? 'Auto-detected on login' : ''}
              />
            </div>
          ))}
          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}
          <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
            <button type="button" onClick={onClose} className="btn btn-secondary" style={{ flex: 1 }}>
              Cancel
            </button>
            <button type="submit" disabled={loading} className="btn btn-primary" style={{ flex: 1 }}>
              {loading ? 'Saving...' : isEdit ? 'Save Changes' : 'Add Account'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AccountCard({ account, session, onLogin, onDelete, onLogout, onEdit }) {
  const [loginLoading, setLoginLoading] = useState(false);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balance, setBalance] = useState(null);
  const [error, setError] = useState('');

  const isOnline = !!session?.session_id && (!session.token_exp || Date.now() / 1000 < session.token_exp - 300);
  const autoFetched = useRef(false);

  // Auto-fetch balance when session exists
  useEffect(() => {
    if (session?.session_id && !balance && !autoFetched.current) {
      autoFetched.current = true;
      setBalanceLoading(true);
      api.getBalance(session.session_id)
        .then((bal) => setBalance(bal))
        .catch(() => {})
        .finally(() => setBalanceLoading(false));
    }
  }, [session?.session_id]);

  const handleLogin = async () => {
    setLoginLoading(true);
    setError('');
    try {
      const result = await onLogin(account);
      if (result.balance !== undefined) {
        setBalance({ account_balance: result.balance });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoginLoading(false);
    }
  };

  const handleRefreshBalance = async () => {
    if (!session) return;
    setBalanceLoading(true);
    try {
      const bal = await api.getBalance(session.session_id);
      setBalance(bal);
    } catch (err) {
      setError(err.message);
    } finally {
      setBalanceLoading(false);
    }
  };

  return (
    <div className="card card-interactive">
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              background: 'var(--bg-input)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <User size={16} style={{ color: 'var(--text-secondary)' }} />
          </div>
          <div>
            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>{account.label}</h3>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0 }}>{account.email}</p>
            {(session?.account_number || account.accountNumber) && (
              <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>#{session?.account_number || account.accountNumber}</p>
            )}
          </div>
        </div>
        <span className={`badge ${isOnline ? 'badge-success' : 'badge-neutral'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {isOnline ? <Wifi size={11} /> : <WifiOff size={11} />}
          {isOnline ? 'Online' : 'Offline'}
        </span>
      </div>

      {balance && (
        <div
          style={{
            background: 'var(--bg-input)',
            borderRadius: 'var(--radius)',
            padding: '10px 12px',
            marginBottom: 14,
          }}
        >
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Balance: </span>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{balance.account_balance || '$0.00'}</span>
          {balance.withdrawal_balance !== undefined && (
            <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>(Withdrawable: {balance.withdrawal_balance || '$0.00'})</span>
          )}
        </div>
      )}

      {error && (
        <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '8px 10px', borderRadius: 'var(--radius)', fontSize: 12, marginBottom: 12 }}>
          {error}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        {!isOnline ? (
          <button
            onClick={handleLogin}
            disabled={loginLoading}
            className="btn btn-primary"
            style={{ flex: 1 }}
          >
            <LogIn size={14} />
            {loginLoading ? 'Logging in...' : 'Login'}
          </button>
        ) : (
          <>
            <button
              onClick={handleRefreshBalance}
              disabled={balanceLoading}
              className="btn btn-secondary"
              style={{ flex: 1 }}
            >
              <RefreshCw size={14} className={balanceLoading ? 'animate-spin' : ''} />
              {balanceLoading ? 'Loading...' : 'Refresh'}
            </button>
            <button
              onClick={() => onLogout(account.id)}
              className="btn"
              style={{ background: 'var(--warning-muted)', color: 'var(--warning)' }}
            >
              <LogIn size={14} style={{ transform: 'rotate(180deg)' }} />
              <span className="hidden sm:inline">Logout</span>
            </button>
          </>
        )}
        <button
          onClick={() => onEdit(account)}
          className="btn btn-secondary"
          aria-label="Edit account"
        >
          <Pencil size={14} />
        </button>
        <button
          onClick={() => onDelete(account.id)}
          className="btn btn-danger"
          aria-label="Delete account"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}

function AccountTableRow({ account, session, isOnline, onLogin, onDelete, onLogout }) {
  const [loginLoading, setLoginLoading] = useState(false);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balance, setBalance] = useState(null);
  const [error, setError] = useState('');

  // Auto-fetch balance when online
  const balanceFetched = useRef(false);
  useEffect(() => {
    if (isOnline && session?.session_id && !balanceFetched.current) {
      balanceFetched.current = true;
      api.getBalance(session.session_id).then((b) => setBalance(b)).catch(() => {});
    }
  }, [isOnline, session?.session_id]);

  const handleLogin = async () => {
    setLoginLoading(true);
    setError('');
    try {
      const result = await onLogin(account);
      if (result.balance) setBalance({ account_balance: result.balance });
    } catch (err) { setError(err.message); }
    finally { setLoginLoading(false); }
  };

  const handleRefresh = async () => {
    if (!session) return;
    setBalanceLoading(true);
    try {
      const b = await api.getBalance(session.session_id);
      setBalance(b);
    } catch (err) { setError(err.message); }
    finally { setBalanceLoading(false); }
  };

  return (
    <tr>
      <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{account.label}</td>
      <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{account.email}</td>
      <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>#{account.accountNumber || '-'}</td>
      <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
        {balance ? (balance.account_balance || '$0.00') : (isOnline ? '...' : '-')}
      </td>
      <td>
        <span className={`badge ${isOnline ? 'badge-success' : 'badge-neutral'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {isOnline ? <Wifi size={10} /> : <WifiOff size={10} />}
          {isOnline ? 'Online' : 'Offline'}
        </span>
      </td>
      <td style={{ textAlign: 'right' }}>
        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
          {!isOnline ? (
            <button onClick={handleLogin} disabled={loginLoading} className="btn btn-primary" style={{ padding: '4px 12px', fontSize: 12 }}>
              <LogIn size={12} />
              {loginLoading ? '...' : 'Login'}
            </button>
          ) : (
            <>
              <button onClick={handleRefresh} disabled={balanceLoading} className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: 12 }}>
                <RefreshCw size={12} className={balanceLoading ? 'animate-spin' : ''} />
              </button>
              <button onClick={() => onLogout(account.id)} className="btn" style={{ padding: '4px 10px', fontSize: 12, background: 'var(--warning-muted)', color: 'var(--warning)' }}>
                <LogIn size={12} style={{ transform: 'rotate(180deg)' }} />
              </button>
            </>
          )}
          <button onClick={() => { if (confirm('Delete this account?')) onDelete(account.id); }} className="btn btn-danger" style={{ padding: '4px 10px', fontSize: 12 }}>
            <Trash2 size={12} />
          </button>
        </div>
        {error && <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4 }}>{error}</div>}
      </td>
    </tr>
  );
}

export default function Dashboard() {
  const [accounts, setAccounts] = useState([]);
  const [betStats, setBetStats] = useState({ total: 0, pending: 0, won: 0, lost: 0, pl: 0, staked: 0 });
  const [showAdd, setShowAdd] = useState(false);
  const [editAccount, setEditAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState('card');
  const { sessions, addSession, removeSession } = useSessions();

  const fetchAccounts = useCallback(async () => {
    try {
      const data = await api.getAccounts();
      setAccounts(data.accounts || []);
    } catch (err) {
      console.error('Failed to fetch accounts:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchBetStats = useCallback(async () => {
    try {
      const today = new Date();
      const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      const data = await api.getBetHistory(undefined, undefined, 500, todayStr, todayStr);
      const bets = data.bets || [];
      const parseMoney = (v) => parseFloat(String(v || '0').replace(/[$,]/g, '')) || 0;
      const won = bets.filter((b) => b.status === 'Won' || b.status === 'won').length;
      const lost = bets.filter((b) => b.status === 'Lost' || b.status === 'lost').length;
      const pending = bets.filter((b) => b.status === 'Pending' || b.status === 'pending').length;
      // P/L only from settled bets (Won + Lost), not pending
      const settledBets = bets.filter((b) => b.status === 'Won' || b.status === 'won' || b.status === 'Lost' || b.status === 'lost');
      const settledStake = settledBets.reduce((s, b) => s + parseMoney(b.stake), 0);
      const settledPayout = settledBets.reduce((s, b) => s + parseMoney(b.payout), 0);
      const totalStake = bets.reduce((s, b) => s + parseMoney(b.stake), 0);
      setBetStats({
        total: bets.length,
        pending,
        won,
        lost,
        pl: settledPayout - settledStake,
        staked: totalStake,
        settledStake,
      });
    } catch {
      // Stats are non-critical
    }
  }, []);

  useEffect(() => {
    fetchAccounts();
    fetchBetStats();
    Promise.all([api.getActiveSessions(), api.getAccounts()]).then(([sessData, acctData]) => {
      const acctMap = {};
      for (const a of acctData.accounts || []) {
        acctMap[a.id] = a;
        if (a.account_number) acctMap[a.account_number] = a;
        if (a.email) acctMap[a.email.toLowerCase()] = a;
      }
      for (const s of sessData.sessions || []) {
        const acct = acctMap[s.account_id] || acctMap[s.account_number] || acctMap[s.email?.toLowerCase()] || {};
        addSession(s.account_id, {
          session_id: s.session_id,
          email: s.email,
          account_number: s.account_number,
          customer_id: s.customer_id,
          accountLabel: acct.label || '',
          token_exp: s.token_exp || null,
        });
      }
    }).catch(() => {});
  }, [fetchAccounts, fetchBetStats, addSession]);

  const [loginAllRunning, setLoginAllRunning] = useState(false);
  const [loginAllProgress, setLoginAllProgress] = useState('');

  const handleLogin = async (account) => {
    const result = await api.tabLogin(account.email, account.password, account.proxyUrl, account.accountNumber);
    addSession(account.id, { ...result, accountLabel: account.label });
    return result;
  };

  const handleLoginAll = async () => {
    const offlineAccounts = accounts.filter((a) => !sessions[a.id]?.session_id);
    if (offlineAccounts.length === 0) return;
    setLoginAllRunning(true);
    for (let i = 0; i < offlineAccounts.length; i++) {
      const acct = offlineAccounts[i];
      setLoginAllProgress(`${acct.label} (${i + 1}/${offlineAccounts.length})`);
      try {
        await handleLogin(acct);
      } catch (err) {
        console.error(`Login failed for ${acct.label}:`, err);
      }
      // Staggered delay: 3-5s between logins (anti-detection)
      if (i < offlineAccounts.length - 1) {
        await new Promise((r) => setTimeout(r, 3000 + Math.random() * 2000));
      }
    }
    setLoginAllRunning(false);
    setLoginAllProgress('');
  };

  const handleLogout = async (accountId) => {
    const session = sessions[accountId];
    if (session?.session_id) {
      try {
        await api.deleteSession(session.session_id);
      } catch (err) {
        console.error('Failed to delete session on server:', err);
      }
    }
    removeSession(accountId);
  };

  const handleDelete = async (id) => {
    if (!confirm('Delete this account?')) return;
    await api.deleteAccount(id);
    fetchAccounts();
  };

  const winRate = betStats.won + betStats.lost > 0
    ? ((betStats.won / (betStats.won + betStats.lost)) * 100).toFixed(1)
    : '0.0';

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-secondary)' }}>
        Loading accounts...
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Stat Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 32 }}>
        <StatCard
          label="Today's Bets"
          value={betStats.total}
          icon={Activity}
          color="var(--accent-muted)"
        />
        <StatCard
          label="Pending"
          value={betStats.pending}
          icon={Clock}
          color="var(--warning-muted)"
        />
        <StatCard
          label="Win Rate (Today)"
          value={`${winRate}%`}
          icon={TrendingUp}
          trend={parseFloat(winRate) >= 50 ? 1 : -1}
          trendLabel={`${betStats.won}W / ${betStats.lost}L`}
          color="var(--success-muted)"
        />
        <StatCard
          label="P/L (Today)"
          value={`${betStats.pl >= 0 ? '+' : ''}$${betStats.pl.toFixed(2)}`}
          icon={betStats.pl >= 0 ? TrendingUp : TrendingDown}
          trend={betStats.pl}
          trendLabel={`$${(betStats.settledStake || 0).toFixed(2)} settled`}
          color={betStats.pl >= 0 ? 'var(--success-muted)' : 'var(--danger-muted)'}
        />
      </div>

      {/* Accounts header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>Accounts</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ display: 'flex', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
            <button
              onClick={() => setViewMode('card')}
              className="btn-ghost"
              style={{
                padding: '5px 8px', borderRadius: 'var(--radius) 0 0 var(--radius)',
                background: viewMode === 'card' ? 'var(--secondary)' : 'transparent',
              }}
              title="Card view"
            >
              <LayoutGrid size={14} style={{ color: viewMode === 'card' ? 'var(--text-primary)' : 'var(--text-muted)' }} />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className="btn-ghost"
              style={{
                padding: '5px 8px', borderRadius: '0 var(--radius) var(--radius) 0',
                background: viewMode === 'table' ? 'var(--secondary)' : 'transparent',
              }}
              title="Table view"
            >
              <List size={14} style={{ color: viewMode === 'table' ? 'var(--text-primary)' : 'var(--text-muted)' }} />
            </button>
          </div>
          {accounts.some((a) => !sessions[a.id]?.session_id) && (
            <button
              onClick={handleLoginAll}
              disabled={loginAllRunning}
              className="btn btn-success"
            >
              <LogIn size={14} />
              {loginAllRunning ? loginAllProgress : 'Login All'}
            </button>
          )}
          <button
            onClick={() => setShowAdd(true)}
            className="btn btn-primary"
          >
            <Plus size={16} />
            Add Account
          </button>
        </div>
      </div>

      {accounts.length === 0 ? (
        <div
          className="card"
          style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}
        >
          <User size={32} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
          No accounts yet. Add one to get started.
        </div>
      ) : viewMode === 'card' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
          {accounts.map((acc) => (
            <AccountCard
              key={acc.id}
              account={acc}
              session={sessions[acc.id]}
              onLogin={handleLogin}
              onDelete={handleDelete}
              onLogout={handleLogout}
              onEdit={(a) => setEditAccount(a)}
            />
          ))}
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Email</th>
                <th>Account #</th>
                <th>Balance</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((acc) => {
                const session = sessions[acc.id];
                const isOnline = !!session?.session_id && (!session.token_exp || Date.now() / 1000 < session.token_exp - 300);
                return (
                  <AccountTableRow
                    key={acc.id}
                    account={acc}
                    session={session}
                    isOnline={isOnline}
                    onLogin={handleLogin}
                    onDelete={handleDelete}
                    onLogout={handleLogout}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && <AccountModal onClose={() => setShowAdd(false)} onSaved={fetchAccounts} />}
      {editAccount && <AccountModal onClose={() => setEditAccount(null)} onSaved={fetchAccounts} editAccount={editAccount} />}
    </div>
  );
}
