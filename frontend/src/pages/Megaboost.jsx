import { useState, useEffect } from 'react';
import { api } from '../api';
import {
  Zap,
  Loader2,
  Search,
  CheckCircle,
  CheckCircle2,
  XCircle,
  TrendingUp,
  Users,
  DollarSign,
  Rocket,
  Gift,
} from 'lucide-react';

const SPORTS = [
  { value: 'HOME', label: 'Homepage (Racing/Featured)' },
  { value: 'NRL', label: 'NRL' },
  { value: 'AFL', label: 'AFL' },
  { value: 'NBA', label: 'NBA' },
  { value: 'Soccer', label: 'Soccer' },
  { value: 'Rugby Union', label: 'Rugby Union' },
  { value: 'Cricket', label: 'Cricket' },
];

function BoostCard({ boost, selected, onClick }) {
  const isMega = boost.type === 'MEGA_BOOST';
  return (
    <div
      onClick={onClick}
      style={{
        background: selected ? 'var(--accent-bg, rgba(0,200,150,0.12))' : 'var(--bg-card)',
        border: selected ? '2px solid var(--accent)' : '2px solid var(--border)',
        borderRadius: 10,
        padding: '14px 16px',
        cursor: 'pointer',
        transition: 'all 0.15s',
        minWidth: 220,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{
          padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700,
          background: isMega ? '#ff4d4d22' : '#fbbf2422',
          color: isMega ? '#ff6b6b' : '#fbbf24',
          letterSpacing: 0.5,
        }}>
          {isMega ? 'MEGA BOOST' : 'BET BOOST'}
        </span>
      </div>
      <div style={{ fontWeight: 700, fontSize: 15, color: 'var(--text-primary)', marginBottom: 4 }}>
        {boost.title}
      </div>
      {boost.description && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, lineHeight: 1.4 }}>
          {boost.description.split(' | ').slice(1, 4).join(' \u2022 ')}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
        {boost.original_odds && (
          <span style={{ fontSize: 13, color: 'var(--text-muted)', textDecoration: 'line-through', opacity: 0.6 }}>
            {boost.original_odds}
          </span>
        )}
        {boost.original_odds && <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{'\u2192'}</span>}
        <span style={{
          fontSize: 18, fontWeight: 800, color: '#000',
          background: 'var(--accent)', borderRadius: 4, padding: '2px 8px',
        }}>
          {boost.boosted_odds || boost.odds}
        </span>
        {boost.returns && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
            {boost.returns}
          </span>
        )}
      </div>
    </div>
  );
}

function ResultRow({ r }) {
  const ok = r.success;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 14px', borderRadius: 8,
      background: ok ? 'rgba(46,204,113,0.08)' : 'rgba(239,68,68,0.08)',
      border: `1px solid ${ok ? 'rgba(46,204,113,0.25)' : 'rgba(239,68,68,0.25)'}`,
    }}>
      {ok ? <CheckCircle2 size={18} color="#2ecc71" /> : <XCircle size={18} color="#ef4444" />}
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>
          {r.username}
        </div>
        {r.error && !ok && (
          <div style={{ fontSize: 11, color: '#ef4444', marginTop: 2 }}>{r.error}</div>
        )}
      </div>
      {r.balance_before != null && (
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Bal: ${r.balance_before?.toFixed(2)}
        </span>
      )}
      {ok && r.odds && (
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--accent)' }}>
          @{r.odds}
        </span>
      )}
      <span style={{
        padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
        background: ok ? '#2ecc7122' : '#ef444422',
        color: ok ? '#2ecc71' : '#ef4444',
      }}>
        {ok ? 'PLACED' : 'FAILED'}
      </span>
    </div>
  );
}

export default function Megaboost() {
  const [sport, setSport] = useState('NRL');
  const [matchTeam, setMatchTeam] = useState('');
  const [scanning, setScanning] = useState(false);
  const [scanStep, setScanStep] = useState('');
  const [boosts, setBoosts] = useState([]);
  const [scanBalance, setScanBalance] = useState(null);
  const [selectedBoost, setSelectedBoost] = useState(null);
  const [stake, setStake] = useState(20);
  const [allAccounts, setAllAccounts] = useState([]);
  const [selectedAccounts, setSelectedAccounts] = useState([]);
  const [placing, setPlacing] = useState(false);
  const [placeSteps, setPlaceSteps] = useState([]);
  const [results, setResults] = useState(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('megaboost'); // 'megaboost' | 'crack'

  // Fetch bet365 accounts from Bookie Accounts DB
  useEffect(() => {
    (async () => {
      try {
        const data = await api.get('/api/multi/accounts');
        const b365 = (data.accounts || [])
          .filter((a) => (a.platform || '').toLowerCase() === 'bet365' || (a.brand || '').toLowerCase() === 'bet365')
          .map((a) => a.email || a.label || a.account_number);
        if (b365.length > 0) {
          setAllAccounts(b365);
          setSelectedAccounts(b365);
        } else {
          // Fallback to hardcoded if no DB accounts
          const fallback = ['marobets', 'colinjhindle81@gmail.com', 'kanuvikmb@gmail.com', 'aaron.keogh1987@gmail.com', 'dustinaspray9@gmail.com'];
          setAllAccounts(fallback);
          setSelectedAccounts(fallback);
        }
      } catch {
        const fallback = ['marobets', 'colinjhindle81@gmail.com', 'kanuvikmb@gmail.com', 'aaron.keogh1987@gmail.com', 'dustinaspray9@gmail.com'];
        setAllAccounts(fallback);
        setSelectedAccounts(fallback);
      }
    })();
  }, []);

  const handleScan = async () => {
    setScanning(true);
    setError('');
    setBoosts([]);
    setSelectedBoost(null);
    setResults(null);
    setScanStep('Connecting to Token Farm (mini PC)...');

    // Animate progress steps while waiting
    const steps = [
      { at: 3000, msg: 'Opening Camoufox browser...' },
      { at: 8000, msg: 'Navigating to bet365.com.au...' },
      { at: 15000, msg: 'Waiting for page load...' },
      { at: 25000, msg: 'Logging into marobets account...' },
      { at: 40000, msg: 'Login submitted, waiting for balance...' },
      { at: 55000, msg: 'Logged in. Navigating to ' + (sport === 'HOME' ? 'homepage' : sport + ' ' + matchTeam) + '...' },
      { at: 65000, msg: 'Scanning page for boost cards...' },
      { at: 75000, msg: 'Almost done, processing results...' },
    ];
    const timers = steps.map(s => setTimeout(() => setScanStep(s.msg), s.at));

    try {
      // Start async scan job (avoids Cloudflare 524 timeout)
      const job = await api.bet365ScanBoostsStart(sport, matchTeam);
      const jobId = job.job_id;

      // Poll every 3s until done
      let data = null;
      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        const status = await api.bet365ScanBoostsStatus(jobId);
        if (status.status === 'done') {
          data = status.result;
          break;
        }
      }

      timers.forEach(clearTimeout);
      if (data.success) {
        setScanStep(`Found ${data.boosts?.length || 0} boosts!`);
        setBoosts(data.boosts || []);
        setScanBalance(data.balance);
        if (!data.boosts?.length) setError('No boosts found. Try a different sport/team or check the homepage.');
      } else {
        setScanStep('');
        setError(data.error || 'Scan failed');
      }
    } catch (e) {
      timers.forEach(clearTimeout);
      setScanStep('');
      setError(e.message);
    }
    setScanning(false);
  };

  const toggleAccount = (acc) => {
    setSelectedAccounts(prev =>
      prev.includes(acc) ? prev.filter(a => a !== acc) : [...prev, acc]
    );
  };

  const toggleAll = () => {
    setSelectedAccounts(prev => prev.length === allAccounts.length ? [] : [...allAccounts]);
  };

  const handlePlace = async () => {
    if (selectedBoost == null || !selectedAccounts.length) return;
    setPlacing(true);
    setError('');
    setResults(null);
    setPlaceSteps([]);

    const addStep = (msg) => setPlaceSteps(prev => [...prev, { time: new Date(), msg }]);
    const n = selectedAccounts.length;
    const isMaro = selectedAccounts.includes('marobets');

    addStep(`Starting placement on ${n} account${n > 1 ? 's' : ''} in parallel...`);

    // Detailed progress steps — sequential placement (~60s per account)
    const timers = [];
    const estPerAccount = 60;
    selectedAccounts.forEach((acc, idx) => {
      const base = idx * estPerAccount * 1000;
      const label = acc === 'marobets' ? 'marobets (no proxy, direct AU)' : acc.split('@')[0] + ' (via proxy)';
      timers.push(setTimeout(() => addStep(`[${idx + 1}/${n}] ${label}: Logging in...`), base + 3000));
      timers.push(setTimeout(() => addStep(`[${idx + 1}/${n}] ${label}: Navigating to boost...`), base + 30000));
      timers.push(setTimeout(() => addStep(`[${idx + 1}/${n}] ${label}: Placing bet...`), base + 45000));
    });
    timers.push(setTimeout(() => addStep('Finalising all accounts...'), n * estPerAccount * 1000 - 5000));

    try {
      // Start async job (returns immediately, avoids Cloudflare 524 timeout)
      const job = await api.bet365MegaboostAllStart(
        sport,
        matchTeam,
        stake,
        selectedBoost,
        selectedAccounts,
      );
      const jobId = job.job_id;
      addStep(`Job started (${jobId}). Polling for results...`);

      // Poll every 5s until done
      let data = null;
      while (true) {
        await new Promise(r => setTimeout(r, 5000));
        try {
          const status = await api.bet365MegaboostAllStatus(jobId);
          if (status.status === 'done') {
            data = status.result;
            break;
          } else if (status.status === 'error') {
            throw new Error(status.result?.error || 'Job failed');
          }
          // Still running — continue polling
        } catch (pollErr) {
          if (pollErr.message.includes('Job not found')) {
            throw new Error('Job lost — server may have restarted');
          }
          throw pollErr;
        }
      }

      timers.forEach(clearTimeout);
      if (data.log) {
        data.log.forEach(line => addStep(line));
      } else {
        addStep(`Done! ${data.succeeded}/${data.total} placed successfully.`);
      }
      setResults(data);
    } catch (e) {
      timers.forEach(clearTimeout);
      addStep(`Error: ${e.message}`);
      setError(e.message);
    }
    setPlacing(false);
  };

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <Rocket size={22} color="var(--accent)" />
        <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
          bet365
        </h1>
        {scanBalance != null && (
          <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--text-muted)' }}>
            Scout bal: ${scanBalance}
          </span>
        )}
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
        <button onClick={() => setTab('megaboost')} className={tab === 'megaboost' ? 'btn btn-primary' : 'btn btn-secondary'} style={{ padding: '8px 20px', fontSize: 13 }}>
          <Rocket size={14} /> Megaboost
        </button>
        <button onClick={() => setTab('crack')} className={tab === 'crack' ? 'btn btn-primary' : 'btn btn-secondary'} style={{ padding: '8px 20px', fontSize: 13 }}>
          <Gift size={14} /> Free Crack
        </button>
      </div>

      {/* Crack Bet Tab */}
      {tab === 'crack' && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <Gift size={40} style={{ color: 'var(--text-muted)', margin: '0 auto 12px', opacity: 0.4 }} />
          <h3 style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>Free Crack Bet</h3>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 16, maxWidth: 400, margin: '0 auto 16px' }}>
            Place custom SGM bets with Stake Back promos across all bet365 accounts. Automates bet selection, promo application, and placement.
          </p>
          <div className="card" style={{ padding: 16, textAlign: 'left', background: 'var(--bg-input)', maxWidth: 500, margin: '0 auto' }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>Status: In Development</div>
            <ul style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0, paddingLeft: 20, lineHeight: 2 }}>
              <li>Navigate to match + SGM tab <span className="badge badge-success" style={{ fontSize: 10 }}>Done</span></li>
              <li>Expand accordion markets <span className="badge badge-success" style={{ fontSize: 10 }}>Done</span></li>
              <li>Select player props <span className="badge badge-success" style={{ fontSize: 10 }}>Done</span></li>
              <li>Detect "USE STAKE BACK" promo <span className="badge badge-success" style={{ fontSize: 10 }}>Done</span></li>
              <li>Apply promo + enter stake <span className="badge badge-warning" style={{ fontSize: 10 }}>WIP</span></li>
              <li>Multi-account placement <span className="badge badge-neutral" style={{ fontSize: 10 }}>Pending</span></li>
            </ul>
          </div>
        </div>
      )}

      {/* Megaboost Tab */}
      {tab === 'megaboost' && <>

      {/* Scan Section */}
      <div className="card" style={{ padding: 20, marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Sport</label>
            <select
              className="t-input"
              value={sport}
              onChange={e => setSport(e.target.value)}
              style={{ width: 220, height: 38 }}
            >
              {SPORTS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          {sport !== 'HOME' && (
            <div>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Team</label>
              <input
                className="t-input"
                placeholder="e.g. Broncos, Carlton, Lakers"
                value={matchTeam}
                onChange={e => setMatchTeam(e.target.value)}
                style={{ width: 220, height: 38 }}
                onKeyDown={e => e.key === 'Enter' && handleScan()}
              />
            </div>
          )}
          <button
            className="btn btn-primary"
            onClick={handleScan}
            disabled={scanning || (sport !== 'HOME' && !matchTeam.trim())}
            style={{ height: 38, display: 'flex', alignItems: 'center', gap: 6 }}
          >
            {scanning ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
            {scanning ? 'Scanning...' : 'Scan Boosts'}
          </button>
        </div>
        {(scanning || scanStep) && (
          <div style={{ marginTop: 12, padding: '10px 14px', borderRadius: 8, background: 'rgba(0,200,150,0.06)', border: '1px solid rgba(0,200,150,0.15)', fontSize: 13, color: 'var(--text-secondary)' }}>
            {scanning && <Loader2 size={14} className="animate-spin" style={{ display: 'inline', verticalAlign: -2, marginRight: 6 }} />}
            {!scanning && scanStep && <CheckCircle2 size={14} style={{ display: 'inline', verticalAlign: -2, marginRight: 6, color: 'var(--accent)' }} />}
            {scanStep}
          </div>
        )}
      </div>

      {error && (
        <div style={{
          padding: '10px 14px', borderRadius: 8, marginBottom: 16,
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          color: '#ef4444', fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* Boost Cards */}
      {boosts.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
            <TrendingUp size={14} /> {boosts.length} boost{boosts.length !== 1 ? 's' : ''} found — click to select
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 10 }}>
            {boosts.map((b, i) => (
              <BoostCard
                key={i}
                boost={b}
                selected={selectedBoost === i}
                onClick={() => setSelectedBoost(i)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Place Section */}
      {selectedBoost != null && (
        <div className="card" style={{ padding: 20, marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Users size={16} /> Accounts & Stake
          </div>

          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 16 }}>
            <div>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 6 }}>
                <span onClick={toggleAll} style={{ cursor: 'pointer', textDecoration: 'underline' }}>
                  {selectedAccounts.length === allAccounts.length ? 'Deselect All' : 'Select All'}
                </span>
              </label>
              {allAccounts.map(acc => (
                <label key={acc} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '3px 0', fontSize: 13, color: 'var(--text-primary)' }}>
                  <input
                    type="checkbox"
                    checked={selectedAccounts.includes(acc)}
                    onChange={() => toggleAccount(acc)}
                  />
                  {acc}
                </label>
              ))}
            </div>
            <div>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                <DollarSign size={12} style={{ display: 'inline' }} /> Stake per account
              </label>
              <input
                className="t-input"
                type="number"
                value={stake}
                onChange={e => setStake(Number(e.target.value))}
                style={{ width: 100, height: 38 }}
                min={1}
              />
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                Total: ${stake * selectedAccounts.length} across {selectedAccounts.length} account{selectedAccounts.length !== 1 ? 's' : ''}
              </div>
            </div>
          </div>

          <button
            className="btn btn-primary"
            onClick={handlePlace}
            disabled={placing || !selectedAccounts.length}
            style={{ height: 42, fontSize: 15, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8, width: '100%', justifyContent: 'center' }}
          >
            {placing ? <Loader2 size={18} className="animate-spin" /> : <Rocket size={18} />}
            {placing ? `Placing on ${selectedAccounts.length} accounts...` : `Place on ${selectedAccounts.length} Account${selectedAccounts.length !== 1 ? 's' : ''}`}
          </button>
        </div>
      )}

      {/* Placement Log */}
      {placeSteps.length > 0 && (
        <div className="card" style={{ padding: '14px 16px', marginBottom: 20, fontSize: 12, fontFamily: 'var(--font-mono)', lineHeight: 1.8 }}>
          {placeSteps.map((s, i) => (
            <div key={i} style={{ color: i === placeSteps.length - 1 && placing ? 'var(--accent)' : 'var(--text-secondary)' }}>
              <span style={{ color: 'var(--text-muted)', marginRight: 8 }}>
                {s.time.toLocaleTimeString('en-AU', { hour12: false })}
              </span>
              {i === placeSteps.length - 1 && placing && (
                <Loader2 size={11} className="animate-spin" style={{ display: 'inline', verticalAlign: -1, marginRight: 4 }} />
              )}
              {s.msg}
            </div>
          ))}
        </div>
      )}

      {/* Results */}
      {results && (
        <div className="card" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Results</span>
            <span style={{
              padding: '2px 10px', borderRadius: 4, fontSize: 12, fontWeight: 700,
              background: results.succeeded === results.total ? '#2ecc7122' : '#fbbf2422',
              color: results.succeeded === results.total ? '#2ecc71' : '#fbbf24',
            }}>
              {results.succeeded}/{results.total} placed
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {results.results?.map((r, i) => <ResultRow key={i} r={r} />)}
          </div>
        </div>
      )}

      </>}
    </div>
  );
}
