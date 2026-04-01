import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import {
  Users,
  Plus,
  X,
  Trash2,
  Edit3,
  Loader2,
  CheckCircle,
  XCircle,
  Wifi,
  WifiOff,
  LogIn,
} from 'lucide-react';

// Single flat list — user just picks the bookie, platform is auto-detected
const BOOKMAKERS = [
  { name: 'Crownbet', platform: 'betmakers' },
  { name: 'TerryBet', platform: 'betmakers' },
  { name: 'PonyBet', platform: 'betmakers' },
  { name: 'BetIt', platform: 'betmakers' },
  { name: 'DiamondBet', platform: 'betmakers' },
  { name: 'BetDash', platform: 'betmakers' },
  { name: 'SwiftBet', platform: 'betmakers' },
  { name: 'BetDeluxe', platform: 'amused' },
  { name: 'BetNation', platform: 'amused' },
  { name: 'Surge', platform: 'amused' },
  { name: 'PulseBet', platform: 'amused' },
  { name: 'BigBet', platform: 'amused' },
  { name: 'YesBet', platform: 'amused' },
  { name: 'MightyBet', platform: 'amused' },
  { name: 'Sportsbet', platform: 'sportsbet' },
  { name: 'TAB', platform: 'tab' },
];

const PLATFORM_LABELS = { betmakers: 'BetMakers', amused: 'Amused', sportsbet: 'Sportsbet', tab: 'TAB' };

function AccountModal({ account, onClose, onSaved }) {
  const isEdit = !!account;
  const defaultBookie = account ? BOOKMAKERS.find(b => b.name.toLowerCase() === (account.brand || '').toLowerCase()) : BOOKMAKERS[0];
  const [form, setForm] = useState({
    label: account?.label || '',
    initials: account?.initials || '',
    owner_name: account?.owner_name || '',
    bookmaker: defaultBookie?.name || 'Crownbet',
    email: account?.email || '',
    password: account?.password || '',
    proxy_base: account?.proxy_base || '',
    account_number: account?.account_number || '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Auto-derive platform and brand from bookmaker selection
  const selectedBookie = BOOKMAKERS.find(b => b.name === form.bookmaker) || BOOKMAKERS[0];
  const platform = selectedBookie.platform;
  const brand = selectedBookie.name;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const payload = {
        ...form,
        platform,
        brand,
      };
      delete payload.bookmaker;  // not a backend field
      if (isEdit) {
        await api.put(`/api/multi/accounts/${account.id}`, payload);
      } else {
        await api.post('/api/multi/accounts', payload);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
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
        style={{ width: '100%', maxWidth: 480, padding: 24 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
            {isEdit ? 'Edit Account' : 'Add Account'}
          </h2>
          <button onClick={onClose} className="btn-ghost" style={{ padding: 4, borderRadius: 'var(--radius-sm)' }} aria-label="Close">
            <X size={18} style={{ color: 'var(--text-muted)' }} />
          </button>
        </div>

        <form onSubmit={handleSubmit} autoComplete="off" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Hidden fields to block browser autofill */}
          <input type="text" name="prevent_autofill" id="prevent_autofill" style={{ display: 'none' }} tabIndex={-1} />
          <input type="password" name="prevent_autofill_pw" id="prevent_autofill_pw" style={{ display: 'none' }} tabIndex={-1} />

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Label</label>
            <input
              type="text"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              required
              placeholder="GEW-JV - BetDeluxe"
              autoComplete="off"
            />
            <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3, display: 'block' }}>
              e.g. "GEW-JV - BetDeluxe" or "SML - Crownbet"
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Initials</label>
              <input
                type="text"
                value={form.initials}
                onChange={(e) => setForm({ ...form, initials: e.target.value.toUpperCase() })}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
                required
                placeholder="GEW"
                autoComplete="off"
              />
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3, display: 'block' }}>
                Must match CSV "Initials" column
              </span>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Owner Name</label>
              <input
                type="text"
                value={form.owner_name}
                onChange={(e) => setForm({ ...form, owner_name: e.target.value })}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
                required
                placeholder="Gene Wiggins"
                autoComplete="off"
              />
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Bookmaker</label>
            <select
              value={form.bookmaker}
              onChange={(e) => setForm({ ...form, bookmaker: e.target.value })}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
            >
              {BOOKMAKERS.map((b) => (
                <option key={b.name} value={b.name}>{b.name}</option>
              ))}
            </select>
            <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3, display: 'block' }}>
              Platform: {PLATFORM_LABELS[platform] || platform}
            </span>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Login Email</label>
            <input
              type="text"
              name="bookie_email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              required
              placeholder="user@example.com"
              autoComplete="new-password"
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Login Password</label>
            <input
              type="password"
              name="bookie_password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              required={!isEdit}
              placeholder={isEdit ? 'Leave blank to keep current' : 'Bookie login password'}
              autoComplete="new-password"
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Proxy Base <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optional)</span></label>
            <input
              type="text"
              value={form.proxy_base}
              onChange={(e) => setForm({ ...form, proxy_base: e.target.value })}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              placeholder="http://customer-marolete_86olc-cc-au"
              autoComplete="off"
            />
            <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3, display: 'block' }}>
              Oxylabs base URL (sessid added automatically)
            </span>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Account Number <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optional)</span></label>
            <input
              type="text"
              value={form.account_number}
              onChange={(e) => setForm({ ...form, account_number: e.target.value })}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              placeholder="Auto-detected on login (required for TAB)"
              autoComplete="off"
            />
          </div>

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

export default function BookieAccounts() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editAccount, setEditAccount] = useState(null);
  const [testingId, setTestingId] = useState(null);
  const [testResult, setTestResult] = useState({});

  const fetchAccounts = useCallback(async () => {
    try {
      const data = await api.get('/api/multi/accounts');
      setAccounts(data.accounts || data || []);
    } catch {
      // Non-critical
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  const handleDelete = async (id) => {
    if (!confirm('Delete this bookie account?')) return;
    try {
      await api.delete(`/api/multi/accounts/${id}`);
      fetchAccounts();
    } catch (err) {
      alert(err.message);
    }
  };

  const handleEdit = (account) => {
    setEditAccount(account);
    setShowModal(true);
  };

  const handleAdd = () => {
    setEditAccount(null);
    setShowModal(true);
  };

  const handleTestLogin = async (id) => {
    setTestingId(id);
    setTestResult((prev) => ({ ...prev, [id]: null }));
    try {
      const data = await api.post(`/api/multi/accounts/${id}/test-login`);
      setTestResult((prev) => ({ ...prev, [id]: { ok: true, balance: data.balance, bonus: data.bonus, message: data.message } }));
    } catch (err) {
      setTestResult((prev) => ({ ...prev, [id]: { ok: false, error: err.message } }));
    } finally {
      setTestingId(null);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-secondary)' }}>
        Loading accounts...
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>Bookie Accounts</h2>
        <button onClick={handleAdd} className="btn btn-primary">
          <Plus size={16} />
          Add Account
        </button>
      </div>

      {accounts.length === 0 ? (
        <div
          className="card"
          style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}
        >
          <Users size={32} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
          No bookie accounts yet. Add one to get started.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Initials</th>
                <th>Bookmaker</th>
                <th>Email</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Balance</th>
                <th style={{ textAlign: 'right' }}>Bonus</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((acc) => {
                const test = testResult[acc.id];
                return (
                  <tr key={acc.id}>
                    <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{acc.label || `${acc.initials} - ${acc.brand}`}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{acc.initials}</td>
                    <td>
                      <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{acc.brand}</span>
                      <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>{PLATFORM_LABELS[acc.platform] || acc.platform}</span>
                    </td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{acc.email}</td>
                    <td>
                      {test === undefined || test === null ? (
                        <span className="badge badge-neutral">--</span>
                      ) : test.ok ? (
                        <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          <Wifi size={10} /> Online
                        </span>
                      ) : (
                        <div>
                          <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <WifiOff size={10} /> Failed
                          </span>
                          {test.error && <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4, maxWidth: 200 }}>{test.error}</div>}
                        </div>
                      )}
                    </td>
                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {test?.ok && test.balance != null ? `$${Number(test.balance).toFixed(2)}` : '--'}
                    </td>
                    <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>
                      {test?.ok && test.bonus != null ? `$${Number(test.bonus).toFixed(2)}` : '--'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                        <button
                          onClick={() => handleTestLogin(acc.id)}
                          disabled={testingId === acc.id}
                          className="btn btn-secondary"
                          style={{ padding: '4px 12px', fontSize: 12 }}
                          title="Test Login"
                        >
                          {testingId === acc.id ? <Loader2 size={12} className="animate-spin" /> : <LogIn size={12} />}
                          Test
                        </button>
                        <button
                          onClick={() => handleEdit(acc)}
                          className="btn btn-secondary"
                          style={{ padding: '4px 10px', fontSize: 12 }}
                          title="Edit"
                        >
                          <Edit3 size={12} />
                        </button>
                        <button
                          onClick={() => handleDelete(acc.id)}
                          className="btn btn-danger"
                          style={{ padding: '4px 10px', fontSize: 12 }}
                          title="Delete"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <AccountModal
          account={editAccount}
          onClose={() => { setShowModal(false); setEditAccount(null); }}
          onSaved={fetchAccounts}
        />
      )}
    </div>
  );
}
