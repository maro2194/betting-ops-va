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

const PLATFORMS = [
  { value: 'betmakers', label: 'Betmakers' },
  { value: 'amused', label: 'Amused' },
  { value: 'tab', label: 'TAB' },
];

const BRANDS_BY_PLATFORM = {
  betmakers: ['Crownbet', 'TerryBet', 'PonyBet', 'BetIt', 'DiamondBet', 'BetDash', 'SwiftBet'],
  amused: ['BetDeluxe', 'BetNation', 'Surge', 'PulseBet', 'BigBet', 'YesBet', 'MightyBet'],
  tab: ['TAB'],
};

function AccountModal({ account, onClose, onSaved }) {
  const isEdit = !!account;
  const [form, setForm] = useState({
    label: account?.label || '',
    initials: account?.initials || '',
    owner_name: account?.owner_name || '',
    platform: account?.platform || 'betmakers',
    brand: account?.brand || 'Crownbet',
    email: account?.email || '',
    password: account?.password || '',
    proxy_base: account?.proxy_base || '',
    account_number: account?.account_number || '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const brands = BRANDS_BY_PLATFORM[form.platform] || [];

  // Reset brand when platform changes
  const handlePlatformChange = (platform) => {
    const newBrands = BRANDS_BY_PLATFORM[platform] || [];
    setForm({ ...form, platform, brand: newBrands[0] || '' });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      if (isEdit) {
        await api.put(`/api/multi/accounts/${account.id}`, form);
      } else {
        await api.post('/api/multi/accounts', form);
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

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Platform</label>
              <select
                value={form.platform}
                onChange={(e) => handlePlatformChange(e.target.value)}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              >
                {PLATFORMS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>Brand</label>
              <select
                value={form.brand}
                onChange={(e) => setForm({ ...form, brand: e.target.value })}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '9px 12px', fontSize: 14 }}
              >
                {brands.map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
              </select>
            </div>
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
      await api.post(`/api/multi/accounts/${id}/test-login`);
      setTestResult((prev) => ({ ...prev, [id]: { ok: true } }));
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
                <th>Platform / Brand</th>
                <th>Email</th>
                <th>Status</th>
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
                      <span className="badge badge-accent" style={{ marginRight: 4 }}>{acc.platform}</span>
                      <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{acc.brand}</span>
                    </td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{acc.email}</td>
                    <td>
                      {test === undefined || test === null ? (
                        <span className="badge badge-neutral">Unknown</span>
                      ) : test.ok ? (
                        <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          <Wifi size={10} /> OK
                        </span>
                      ) : (
                        <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }} title={test.error}>
                          <WifiOff size={10} /> Failed
                        </span>
                      )}
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
