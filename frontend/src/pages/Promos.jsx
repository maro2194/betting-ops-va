import { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import {
  Gift,
  Loader2,
  Ticket,
  AlertCircle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Zap,
  ArrowLeftRight,
  Search,
  Terminal,
} from 'lucide-react';

const PROMO_TYPE_COLORS = {
  Boost: { bg: '#d2afff22', color: '#d2afff', label: 'Boost' },
  BonusBack: { bg: '#4af2d422', color: '#4af2d4', label: 'Bonus Back' },
  DepositMatch: { bg: '#fcc43222', color: '#fcc432', label: 'Deposit Match' },
};

function PromoTypeBadge({ type }) {
  const style = PROMO_TYPE_COLORS[type] || { bg: 'var(--border)', color: 'var(--text-muted)', label: type };
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
      background: style.bg, color: style.color,
    }}>
      {style.label}
    </span>
  );
}

function TokenBadges({ a }) {
  const boosts = a.boost_tokens || 0;
  const bb = a.bonus_back_tokens || 0;
  const dm = a.deposit_match_tokens || 0;
  if (!boosts && !bb && !dm) return <span style={{ color: 'var(--text-muted)' }}>-</span>;
  return (
    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
      {boosts > 0 && (
        <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#d2afff', fontWeight: 600, fontSize: 13 }}>
          <Zap size={13} /> {boosts}
        </span>
      )}
      {bb > 0 && (
        <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#4af2d4', fontWeight: 600, fontSize: 13 }}>
          <ArrowLeftRight size={13} /> {bb}
        </span>
      )}
      {dm > 0 && (
        <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#fcc432', fontWeight: 600, fontSize: 13 }}>
          <Ticket size={13} /> {dm}
        </span>
      )}
    </div>
  );
}

export default function Promos() {
  const [scanning, setScanning] = useState(false);
  const [scanningBrand, setScanningBrand] = useState(null);
  const [accounts, setAccounts] = useState(null);
  const [promotions, setPromotions] = useState(null);
  const [error, setError] = useState(null);
  const [expandedBrand, setExpandedBrand] = useState({});
  const [expandedAccount, setExpandedAccount] = useState({});
  const [logs, setLogs] = useState([]);
  const [scanProgress, setScanProgress] = useState(null); // {index, total}
  const logRef = useRef(null);
  const abortRef = useRef(null);

  // Account selection for filtered scan
  const [allBrands, setAllBrands] = useState([]);
  const [allAccounts, setAllAccounts] = useState([]);
  const [selectedAccounts, setSelectedAccounts] = useState(new Set());
  const [expandedSelector, setExpandedSelector] = useState({});

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  // Platforms that don't support promo scanning
  const SKIP_PLATFORMS = new Set(['bet365']);

  // Load registered accounts on mount
  useEffect(() => {
    api.get('/api/multi/accounts').then((data) => {
      const accts = (data.accounts || []).filter((a) => !SKIP_PLATFORMS.has(a.platform));
      setAllAccounts(accts);

      const brandMap = {};
      for (const a of accts) {
        const key = a.brand.toLowerCase();
        if (!brandMap[key]) brandMap[key] = { brand: a.brand, platform: a.platform, accounts: [] };
        brandMap[key].accounts.push(a);
      }
      const brands = Object.values(brandMap).sort((a, b) => a.brand.localeCompare(b.brand));
      setAllBrands(brands);
      setSelectedAccounts(new Set(accts.map((a) => a.id)));
    }).catch(() => {});
  }, []);

  // Derived: which brands have any selected accounts
  const selectedBrands = new Set();
  for (const a of allAccounts) {
    if (selectedAccounts.has(a.id)) selectedBrands.add(a.brand.toLowerCase());
  }

  const toggleAccountSelection = (id) => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleBrandSelection = (brand) => {
    const brandAccounts = allAccounts.filter((a) => a.brand.toLowerCase() === brand);
    const allSelected = brandAccounts.every((a) => selectedAccounts.has(a.id));
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      for (const a of brandAccounts) {
        if (allSelected) next.delete(a.id);
        else next.add(a.id);
      }
      return next;
    });
  };

  const selectAll = () => setSelectedAccounts(new Set(allAccounts.map((a) => a.id)));
  const selectNone = () => setSelectedAccounts(new Set());

  const addLog = (msg, type = 'info') => {
    const ts = new Date().toLocaleTimeString('en-AU', { hour12: false });
    setLogs((prev) => [...prev, { ts, msg, type }]);
  };

  const scan = async (brandsFilter) => {
    const isSingle = typeof brandsFilter === 'string';
    if (isSingle) {
      setScanningBrand(brandsFilter);
    } else {
      setScanning(true);
    }
    setError(null);
    setLogs([]);
    setScanProgress(null);

    // Build account IDs to scan
    let accountIds;
    if (isSingle) {
      accountIds = allAccounts.filter((a) => a.brand.toLowerCase() === brandsFilter).map((a) => a.id);
    } else {
      accountIds = [...selectedAccounts];
    }
    const brandCount = new Set(allAccounts.filter((a) => accountIds.includes(a.id)).map((a) => a.brand)).size;
    addLog(`Starting scan for ${accountIds.length} account${accountIds.length !== 1 ? 's' : ''} across ${brandCount} brand${brandCount !== 1 ? 's' : ''}...`);

    // Collect results from stream
    const streamAccounts = [];
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const token = localStorage.getItem('app_token');
      const resp = await fetch('/api/multi/scan-promos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ account_ids: accountIds }),
        signal: controller.signal,
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));

            if (evt.type === 'start') {
              addLog(`Found ${evt.total} account${evt.total !== 1 ? 's' : ''} to scan`);
              setScanProgress({ index: 0, total: evt.total });
            } else if (evt.type === 'scanning') {
              addLog(`[${evt.index + 1}/${evt.total}] Scanning ${evt.brand} (${evt.initials})...`, 'pending');
              setScanProgress({ index: evt.index, total: evt.total });
            } else if (evt.type === 'account') {
              const a = evt.account;
              streamAccounts.push(a);
              const boosts = (a.boost_tokens || 0);
              const bb = (a.bonus_back_tokens || 0);
              if (a.status === 'ok') {
                const parts = [`$${a.cash.toFixed(2)}`];
                if (boosts) parts.push(`${boosts} boosts`);
                if (bb) parts.push(`${bb} bonus back`);
                addLog(`[${evt.index + 1}/${evt.total}] ${a.brand} (${a.initials}) — ${parts.join(', ')}`, 'success');
              } else {
                addLog(`[${evt.index + 1}/${evt.total}] ${a.brand} (${a.initials}) — ${a.error || a.status}`, 'error');
              }

              // Update accounts progressively
              if (isSingle) {
                setAccounts((prev) => {
                  const existing = (prev || []).filter((x) => x.account_id !== a.account_id);
                  return [...existing, a];
                });
              } else {
                setAccounts([...streamAccounts]);
              }
              setScanProgress({ index: evt.index + 1, total: evt.total });
            } else if (evt.type === 'done') {
              if (isSingle) {
                setPromotions((prev) => ({ ...(prev || {}), ...(evt.promotions || {}) }));
              } else {
                setAccounts(evt.accounts || []);
                setPromotions(evt.promotions || {});
              }
              const ok = (evt.accounts || []).filter((a) => a.status === 'ok').length;
              const fail = (evt.accounts || []).length - ok;
              addLog(`Done — ${ok} OK${fail ? `, ${fail} failed` : ''}`, 'success');
            }
          } catch (e) { /* skip malformed SSE lines */ }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        addLog('Scan cancelled', 'error');
      } else {
        setError(e.message);
        addLog(`Error: ${e.message}`, 'error');
      }
    } finally {
      setScanning(false);
      setScanningBrand(null);
      setScanProgress(null);
      abortRef.current = null;
    }
  };

  const cancelScan = () => {
    if (abortRef.current) abortRef.current.abort();
  };

  const toggleBrand = (brand) => {
    setExpandedBrand((prev) => ({ ...prev, [brand]: !prev[brand] }));
  };

  const toggleAccount = (id) => {
    setExpandedAccount((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // Group accounts by brand
  const accountsByBrand = {};
  if (accounts) {
    for (const a of accounts) {
      const key = a.brand.toLowerCase();
      if (!accountsByBrand[key]) accountsByBrand[key] = [];
      accountsByBrand[key].push(a);
    }
  }

  const totalBoostTokens = accounts ? accounts.reduce((s, a) => s + (a.boost_tokens || 0), 0) : 0;
  const totalBBTokens = accounts ? accounts.reduce((s, a) => s + (a.bonus_back_tokens || 0), 0) : 0;
  const totalCash = accounts ? accounts.reduce((s, a) => s + (a.cash || 0), 0) : 0;
  const totalBonus = accounts ? accounts.reduce((s, a) => s + (a.bonus || 0), 0) : 0;

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1100 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
            <Gift size={22} style={{ marginRight: 8, verticalAlign: 'middle', color: 'var(--primary)' }} />
            Promotions Scanner
          </h1>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '6px 0 0' }}>
            Scan accounts for tokens, bonus bets, and available promos
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {(scanning || scanningBrand) && (
            <button
              className="btn btn-danger"
              onClick={cancelScan}
              style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 16px' }}
            >
              Cancel
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={() => scan()}
            disabled={scanning || scanningBrand || selectedAccounts.size === 0}
            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 20px' }}
          >
            {scanning ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            {scanning ? 'Scanning...' : selectedAccounts.size === allAccounts.length ? 'Scan All' : `Scan ${selectedAccounts.size} Account${selectedAccounts.size !== 1 ? 's' : ''}`}
          </button>
        </div>
      </div>

      {/* Brand + Account Selector */}
      {allBrands.length > 0 && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)' }}>
              Select Accounts ({selectedAccounts.size}/{allAccounts.length})
            </span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }} onClick={selectAll}>All</button>
              <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }} onClick={selectNone}>None</button>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {allBrands.map((b) => {
              const key = b.brand.toLowerCase();
              const brandAccts = b.accounts || [];
              const selectedCount = brandAccts.filter((a) => selectedAccounts.has(a.id)).length;
              const allSelected = selectedCount === brandAccts.length;
              const someSelected = selectedCount > 0 && !allSelected;
              const isExpanded = expandedSelector[key];

              return (
                <div key={key}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => { if (el) el.indeterminate = someSelected; }}
                      onChange={() => toggleBrandSelection(key)}
                      style={{ cursor: 'pointer' }}
                    />
                    <button
                      onClick={() => setExpandedSelector((p) => ({ ...p, [key]: !p[key] }))}
                      style={{
                        flex: 1, display: 'flex', alignItems: 'center', gap: 6,
                        padding: '5px 8px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                        border: 'none', background: 'transparent', cursor: 'pointer',
                        color: selectedCount > 0 ? 'var(--text-primary)' : 'var(--text-muted)',
                      }}
                    >
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      {b.brand}
                      <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6 }}>
                        {selectedCount}/{brandAccts.length}
                      </span>
                    </button>
                  </div>
                  {isExpanded && (
                    <div style={{ paddingLeft: 32, display: 'flex', flexDirection: 'column', gap: 2, marginBottom: 4 }}>
                      {brandAccts.map((a) => (
                        <label
                          key={a.id}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 8, padding: '3px 8px',
                            borderRadius: 4, cursor: 'pointer', fontSize: 12,
                            color: selectedAccounts.has(a.id) ? 'var(--text-primary)' : 'var(--text-muted)',
                            background: selectedAccounts.has(a.id) ? 'oklch(55% .22 165 / 0.05)' : 'transparent',
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={selectedAccounts.has(a.id)}
                            onChange={() => toggleAccountSelection(a.id)}
                            style={{ cursor: 'pointer' }}
                          />
                          <span style={{ fontWeight: 500 }}>{a.initials}</span>
                          <span style={{ opacity: 0.5 }}>{a.label || a.email}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {error && (
        <div className="card" style={{ padding: 14, marginBottom: 20, borderLeft: '3px solid var(--danger)', display: 'flex', gap: 10, alignItems: 'center' }}>
          <AlertCircle size={16} style={{ color: 'var(--danger)', flexShrink: 0 }} />
          <span style={{ color: 'var(--danger)', fontSize: 13 }}>{error}</span>
        </div>
      )}

      {/* Scan Log */}
      {logs.length > 0 && (
        <div className="card" style={{ marginBottom: 20, overflow: 'hidden' }}>
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Terminal size={14} style={{ color: 'var(--text-muted)' }} />
            <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)' }}>
              Scan Log
            </span>
            {scanProgress && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
                {scanProgress.index}/{scanProgress.total}
              </span>
            )}
            {scanProgress && (
              <div style={{ width: 120, height: 4, borderRadius: 2, background: 'var(--border)', overflow: 'hidden' }}>
                <div style={{ width: `${(scanProgress.index / scanProgress.total) * 100}%`, height: '100%', background: 'var(--primary)', borderRadius: 2, transition: 'width 0.3s' }} />
              </div>
            )}
          </div>
          <div
            ref={logRef}
            style={{
              maxHeight: 180, overflowY: 'auto', padding: '8px 16px',
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11, lineHeight: 1.7,
              background: 'var(--bg-primary)',
            }}
          >
            {logs.map((l, i) => (
              <div key={i} style={{ color: l.type === 'error' ? 'var(--danger)' : l.type === 'success' ? 'oklch(62% .2 145)' : l.type === 'pending' ? 'var(--text-muted)' : 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-muted)', marginRight: 8 }}>{l.ts}</span>
                {l.msg}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Summary Cards */}
      {accounts && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 28 }}>
          <div className="card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 6 }}>
              Boost Tokens
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: '#d2afff', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Zap size={22} /> {totalBoostTokens}
            </div>
          </div>
          <div className="card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 6 }}>
              Bonus Back Tokens
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: '#4af2d4', display: 'flex', alignItems: 'center', gap: 8 }}>
              <ArrowLeftRight size={22} /> {totalBBTokens}
            </div>
          </div>
          <div className="card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 6 }}>
              Total Cash
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)' }}>
              ${totalCash.toFixed(2)}
            </div>
          </div>
          <div className="card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 6 }}>
              Bonus Cash
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: totalBonus > 0 ? 'var(--primary)' : 'var(--text-muted)' }}>
              ${totalBonus.toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {/* Accounts by Brand */}
      {accounts && Object.keys(accountsByBrand).sort().map((brand) => {
        const accts = accountsByBrand[brand];
        const brandPromos = (promotions || {})[brand] || [];
        const brandBoosts = accts.reduce((s, a) => s + (a.boost_tokens || 0), 0);
        const brandBB = accts.reduce((s, a) => s + (a.bonus_back_tokens || 0), 0);
        const isExpanded = expandedBrand[brand] !== false;
        const isScanningThis = scanningBrand === brand;

        return (
          <div key={brand} className="card" style={{ marginBottom: 16, overflow: 'hidden' }}>
            {/* Brand Header */}
            <div
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '14px 18px',
                borderBottom: isExpanded ? '1px solid var(--border)' : 'none',
              }}
            >
              <div
                onClick={() => toggleBrand(brand)}
                style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', userSelect: 'none', flex: 1 }}
              >
                {isExpanded ? <ChevronDown size={16} style={{ color: 'var(--text-muted)' }} /> : <ChevronRight size={16} style={{ color: 'var(--text-muted)' }} />}
                <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                  {brand}
                </span>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {accts.length} account{accts.length !== 1 ? 's' : ''}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                {brandBoosts > 0 && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#d2afff', fontWeight: 600, fontSize: 13 }}>
                    <Zap size={14} /> {brandBoosts}
                  </span>
                )}
                {brandBB > 0 && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#4af2d4', fontWeight: 600, fontSize: 13 }}>
                    <ArrowLeftRight size={14} /> {brandBB}
                  </span>
                )}
                {brandPromos.length > 0 && (
                  <span className="badge badge-warning" style={{ fontSize: 11 }}>
                    {brandPromos.length} promo{brandPromos.length !== 1 ? 's' : ''}
                  </span>
                )}
                <button
                  className="btn btn-ghost"
                  onClick={(e) => { e.stopPropagation(); scan(brand); }}
                  disabled={scanning || scanningBrand !== null}
                  style={{ padding: '4px 10px', fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}
                  title={`Scan ${brand} only`}
                >
                  {isScanningThis ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                  {isScanningThis ? 'Scanning...' : 'Scan'}
                </button>
              </div>
            </div>

            {isExpanded && (
              <div style={{ padding: '0 18px 14px' }}>
                {/* Account Balances Table */}
                <table className="data-table" style={{ width: '100%', marginTop: 12 }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left', padding: '8px 10px' }}>Initials</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px' }}>Email</th>
                      <th style={{ textAlign: 'right', padding: '8px 10px' }}>Cash</th>
                      <th style={{ textAlign: 'right', padding: '8px 10px' }}>Bonus Cash</th>
                      <th style={{ textAlign: 'right', padding: '8px 10px' }}>Tokens</th>
                      <th style={{ textAlign: 'center', padding: '8px 10px' }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accts.map((a) => {
                      const hasPromos = a.user_promos && a.user_promos.length > 0;
                      const isOpen = expandedAccount[a.account_id];
                      return (
                        <>
                          <tr
                            key={a.account_id}
                            onClick={() => hasPromos && toggleAccount(a.account_id)}
                            style={{ cursor: hasPromos ? 'pointer' : 'default' }}
                          >
                            <td style={{ padding: '8px 10px', fontWeight: 600 }}>
                              {hasPromos && (isOpen ? <ChevronDown size={12} style={{ marginRight: 4 }} /> : <ChevronRight size={12} style={{ marginRight: 4 }} />)}
                              {a.initials}
                            </td>
                            <td style={{ padding: '8px 10px', color: 'var(--text-secondary)', fontSize: 12 }}>{a.email}</td>
                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>${a.cash.toFixed(2)}</td>
                            <td style={{ padding: '8px 10px', textAlign: 'right', color: a.bonus > 0 ? 'var(--primary)' : 'var(--text-muted)' }}>
                              {a.bonus > 0 ? `$${a.bonus.toFixed(2)}` : '-'}
                            </td>
                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                              <TokenBadges a={a} />
                            </td>
                            <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                              {a.status === 'ok' ? (
                                <span className="badge badge-success" style={{ fontSize: 11 }}>OK</span>
                              ) : (
                                <span className="badge badge-danger" style={{ fontSize: 11 }} title={a.error || ''}>
                                  {a.status === 'login_failed' ? 'Login Failed' : 'Error'}
                                </span>
                              )}
                            </td>
                          </tr>
                          {isOpen && a.user_promos.map((p) => {
                            // Extract max stake from description or bonus_back_data
                            const stakeMatch = p.description?.match(/\$[\d,]+/);
                            const maxStake = p.bonus_back_data?.max_deposit > 0
                              ? `$${(p.bonus_back_data.max_deposit / 100).toFixed(0)}`
                              : stakeMatch ? stakeMatch[0] : null;

                            return (
                            <tr key={p.promo_id} style={{ background: 'var(--bg-primary)' }}>
                              <td colSpan={6} style={{ padding: '8px 10px 8px 36px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                  <PromoTypeBadge type={p.type} />
                                  <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{p.description}</span>
                                  {maxStake && (
                                    <span style={{
                                      fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
                                      background: 'oklch(55% .22 165 / 0.1)', color: 'var(--primary)',
                                    }}>
                                      {p.type === 'Boost' ? `Max ${maxStake}` : `Up to ${maxStake}`}
                                    </span>
                                  )}
                                  <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
                                    {p.tokens_remaining}x
                                  </span>
                                  {p.expiry_date && (
                                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                      Exp {new Date(p.expiry_date).toLocaleDateString()}
                                    </span>
                                  )}
                                </div>
                                {p.bonus_back_data && (
                                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                                    {p.bonus_back_data.event_config_type === 'EventSpecific' && p.bonus_back_data.event_config?.length > 0 && (
                                      <span style={{ color: '#4af2d4', fontWeight: 600 }}>
                                        {p.bonus_back_data.event_config.map((e) =>
                                          `${e.venue} R${e.race_numbers?.join(',')} — Run ${p.bonus_back_data.criteria === 'BonusBack3rd' ? '2nd/3rd' : '2nd'}`
                                        ).join(' | ')}
                                      </span>
                                    )}
                                    {p.bonus_back_data.event_config_type === 'Global' && p.bonus_back_data.global_config?.length > 0 && (
                                      <span style={{ color: '#4af2d4', fontWeight: 600 }}>
                                        {p.bonus_back_data.global_config.map((g) => g.event_type).join(', ')}
                                        {' — Run '}
                                        {p.bonus_back_data.criteria === 'BonusBack3rd' ? '2nd/3rd' : '2nd'}
                                      </span>
                                    )}
                                    {p.bonus_back_data.field_size > 0 && (
                                      <span style={{ marginLeft: 6 }}>({p.bonus_back_data.field_size}+ runners)</span>
                                    )}
                                  </div>
                                )}
                                {p.boost_data && p.boost_data.boost_percentage > 0 && (
                                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                                    {p.boost_data.boost_type?.replace(/([A-Z])/g, ' $1').trim()} — +{p.boost_data.boost_percentage}%
                                  </div>
                                )}
                              </td>
                            </tr>
                            );
                          })}
                        </>
                      );
                    })}
                  </tbody>
                </table>

                {/* Public Promotions for this brand */}
                {brandPromos.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 10 }}>
                      Site Promotions
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {brandPromos.map((p) => (
                        <div
                          key={p.id}
                          style={{
                            display: 'flex', gap: 14, alignItems: 'flex-start',
                            padding: '12px 14px',
                            background: 'var(--bg-primary)',
                            borderRadius: 'var(--radius)',
                            border: '1px solid var(--border)',
                          }}
                        >
                          {p.image && (
                            <img
                              src={p.image}
                              alt=""
                              style={{ width: 80, height: 52, objectFit: 'cover', borderRadius: 6, flexShrink: 0 }}
                            />
                          )}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
                              {p.description}
                            </div>
                            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11, color: 'var(--text-muted)' }}>
                              <span style={{
                                padding: '2px 8px', borderRadius: 4,
                                background: 'oklch(55% .22 165 / 0.1)', color: 'var(--primary)',
                                fontWeight: 600,
                              }}>
                                {p.type.replace(/([A-Z])/g, ' $1').trim()}
                              </span>
                              {p.route && <span>{p.route}</span>}
                              {p.end_date && <span>Ends {p.end_date}</span>}
                            </div>
                            {p.terms && p.terms.length > 0 && (
                              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
                                {p.terms[0]}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Empty state */}
      {!accounts && !scanning && (
        <div className="card" style={{ padding: '48px 24px', textAlign: 'center' }}>
          <Gift size={40} style={{ color: 'var(--text-muted)', marginBottom: 12 }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
            No scan results yet
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            Select brands above and hit Scan, or scan individual brands
          </div>
        </div>
      )}
    </div>
  );
}
