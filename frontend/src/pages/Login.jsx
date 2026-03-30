import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      navigate('/');
    } catch (err) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--bg-primary)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
      }}
    >
      <div style={{ width: '100%', maxWidth: 400 }}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginBottom: 16 }}>
            <rect x="1.5" y="1.5" width="45" height="45" rx="10" fill="#0a0a0f" stroke="oklch(55% .22 165)" strokeWidth="2" />
            <text x="24" y="31" textAnchor="middle" fill="oklch(55% .22 165)" fontFamily="'JetBrains Mono', monospace" fontWeight="700" fontSize="18">BO</text>
          </svg>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
            BotOps
          </h1>
          <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>
            Sign in to BotOps
          </p>
        </div>

        {/* Card */}
        <div className="card" style={{ padding: 28 }}>
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <label
                htmlFor="username"
                style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}
              >
                Username
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '10px 12px', fontSize: 14 }}
                required
                autoFocus
                placeholder="Enter your username"
              />
            </div>
            <div>
              <label
                htmlFor="password"
                style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="t-input"
                style={{ width: '100%', border: '1px solid var(--border)', padding: '10px 12px', fontSize: 14 }}
                required
                placeholder="Enter your password"
              />
            </div>
            {error && (
              <div
                style={{
                  background: 'var(--danger-muted)',
                  color: 'var(--danger)',
                  padding: '10px 12px',
                  borderRadius: 'var(--radius)',
                  fontSize: 13,
                }}
              >
                {error}
              </div>
            )}
            <button
              type="submit"
              disabled={loading}
              className="btn btn-primary"
              style={{ width: '100%', padding: '10px 16px', marginTop: 4 }}
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
