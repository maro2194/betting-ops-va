import { useState, useRef, useCallback, useEffect } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import {
  Play,
  CheckCircle,
  XCircle,
  Loader2,
  ArrowUp,
  ArrowDown,
  RotateCcw,
  ClipboardPaste,
  AlertTriangle,
  Search,
  StopCircle,
  Zap,
  RefreshCw,
  ListChecks,
} from 'lucide-react';

/* ─── Constants ─── */
const CSB_SPORTS = [
  { label: 'AFL', sport: 'AFL Football', competition: 'AFL' },
  { label: 'NRL', sport: 'Rugby League', competition: 'NRL' },
  { label: 'NBA', sport: 'Basketball', competition: 'NBA' },
];

/* ─── CSV Parsing ─── */
function parseCsvLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') { inQuotes = !inQuotes; }
    else if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; }
    else { current += ch; }
  }
  result.push(current.trim());
  return result;
}

function detectAndParse(csvText) {
  const lines = csvText.trim().split('\n').filter((l) => l.trim());
  if (lines.length < 2) return [];
  const header = lines[0].toLowerCase();
  const isSgm = header.includes('game id');
  const bets = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = parseCsvLine(lines[i]);
    if (cols.length < 6) continue;
    if (isSgm) {
      const legOdds = [];
      for (let j = 7; j < cols.length; j++) { const v = parseFloat(cols[j]); if (!isNaN(v) && v > 0) legOdds.push(v); }
      bets.push({ bet_type: (cols[0] || 'SGM').trim(), game_id: (cols[1] || '').trim(), bet: (cols[2] || '').trim(), odds: parseFloat(cols[3]) || 0, min_odds: parseFloat(cols[4]) || 0, ev_pct: parseFloat((cols[5] || '').replace('%', '')) || 0, units: parseFloat(cols[6]) || 1, leg_odds: legOdds, teams: '' });
    } else {
      const legOdds = [];
      for (let j = 7; j < cols.length; j++) { const v = parseFloat(cols[j]); if (!isNaN(v) && v > 0) legOdds.push(v); }
      bets.push({ bet_type: (cols[0] || 'Multi').trim(), game_id: '', bet: (cols[1] || '').trim(), odds: parseFloat(cols[2]) || 0, min_odds: parseFloat(cols[3]) || 0, ev_pct: parseFloat((cols[4] || '').replace('%', '')) || 0, units: parseFloat(cols[5]) || 1, leg_odds: legOdds, teams: (cols[6] || '').trim() });
    }
  }
  return bets;
}

/* ─── Helpers ─── */
function humanRoundDown(amount) {
  if (amount < 1) return 1;
  if (amount < 10) return Math.floor(amount);
  if (amount < 100) return Math.floor(amount / 5) * 5;
  return Math.floor(amount / 10) * 10;
}

function randomDelay(min = 2000, max = 6000) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Classify placement error: retryable (transient network/server) vs terminal (structural).
// Allowlist approach — only retry on keywords we *know* are transient. Everything else
// defaults terminal, so a TAB copy change ("Under minimum" vs "below minimum") can't
// accidentally reopen the retry loop on a structural failure.
const RETRYABLE_KEYWORDS = [
  'failed to fetch', 'networkerror', 'network error',
  'timeout', 'timed out', 'econnreset', 'aborted',
  '502', '503', '504', 'gateway', 'upstream',
  'tunnel', 'proxy', 'ssl', 'boringssl', 'connect',
  'temporarily unavailable', 'service unavailable',
];
function isRetryableError(message) {
  if (!message) return true; // unknown empty error — let it retry once
  const m = String(message).toLowerCase();
  return RETRYABLE_KEYWORDS.some((kw) => m.includes(kw));
}

// Subset of retryable errors that indicate TAB-side upstream trouble (as opposed to
// e.g. local ssl/proxy). Used by the per-account circuit breaker to decide when to
// pause an account on a 5xx storm rather than slamming TAB harder.
const FIVE_XX_KEYWORDS = ['502', '503', '504', 'gateway', 'upstream', 'service unavailable'];
function is5xxError(message) {
  if (!message) return false;
  const m = String(message).toLowerCase();
  return FIVE_XX_KEYWORDS.some((kw) => m.includes(kw));
}

// Circuit breaker tuning. Per account: after this many consecutive 5xx errors, pause
// the account's placement loop for the cooldown. Gives TAB's gateway room to recover
// instead of the frontend hammering it through an outage (which is what the original
// Shadow 502 cascade looked like).
const CIRCUIT_BREAKER_THRESHOLD = 3;
const CIRCUIT_BREAKER_COOLDOWN_MS = 60000;

// Pre-flight balance check tuning. A single TLS/proxy blip shouldn't knock an entire
// account out of the run (Shadow 2026-04-20: WRONG_VERSION_NUMBER on one packet caused
// JV258 TRL to be skipped entirely, leaving JV260 HM to absorb a $3.6K queue on a $682
// balance). Retry transient failures a few times before declaring unhealthy.
const PREFLIGHT_MAX_ATTEMPTS = 3;
const PREFLIGHT_RETRY_DELAY_MS = 1500;

/* ─── Status Components ─── */
function StatusIcon({ status }) {
  if (status === 'placed') return <CheckCircle size={16} style={{ color: 'var(--success)' }} />;
  if (status === 'resolved') return <CheckCircle size={16} style={{ color: 'var(--primary)' }} />;
  if (status === 'below_min') return <XCircle size={16} style={{ color: 'var(--danger)' }} />;
  if (status === 'failed') return <XCircle size={16} style={{ color: 'var(--danger)' }} />;
  if (status === 'placing') return <Loader2 size={16} style={{ color: 'var(--primary)' }} className="animate-spin" />;
  if (status === 'resolving') return <Loader2 size={16} style={{ color: 'var(--text-secondary)' }} className="animate-spin" />;
  if (status === 'skipped') return <AlertTriangle size={16} style={{ color: 'var(--warning)' }} />;
  return null;
}

function statusRowBg(status) {
  if (status === 'placed') return 'var(--success-muted)';
  if (status === 'resolved') return 'var(--accent-muted)';
  if (status === 'below_min' || status === 'skipped') return 'var(--danger-muted)';
  if (status === 'failed') return 'var(--danger-muted)';
  if (status === 'placing') return 'var(--accent-muted)';
  return 'transparent';
}

/* ─══════════════════════════════════════════════════════════════════╗
 *  CSB V4 — Parallel multi-account placement engine
 * ╚══════════════════════════════════════════════════════════════════*/
export default function CsbV4() {
  const { sessions } = useSessions();

  // ─── Phase tracking ───
  // 'input' → 'resolved' → 'planned' → 'placing' → 'done'
  const [phase, setPhase] = useState('input');

  // CSV
  const [csvText, setCsvText] = useState('');
  const [parsedBets, setParsedBets] = useState([]);
  const [error, setError] = useState('');

  // Config (persisted with v4_ prefix)
  const [sport, setSport] = useState(CSB_SPORTS[0]);
  const [unitSize, setUnitSize] = useState(() => localStorage.getItem('v4_unitSize') || '10');
  const [stakingMode, setStakingMode] = useState(() => localStorage.getItem('v4_stakingMode') || 'units');
  const [fixedStake, setFixedStake] = useState(() => localStorage.getItem('v4_fixedStake') || '10');
  const [maxLiability, setMaxLiability] = useState(() => localStorage.getItem('v4_maxLiability') || '500');
  const [liabilityCap, setLiabilityCap] = useState(() => localStorage.getItem('v4_liabCap') || '600');
  const [oddsDriftEnabled, setOddsDriftEnabled] = useState(() => localStorage.getItem('v4_oddsDrift') !== 'false');

  // Persist
  useEffect(() => { localStorage.setItem('v4_unitSize', unitSize); }, [unitSize]);
  useEffect(() => { localStorage.setItem('v4_stakingMode', stakingMode); }, [stakingMode]);
  useEffect(() => { localStorage.setItem('v4_fixedStake', fixedStake); }, [fixedStake]);
  useEffect(() => { localStorage.setItem('v4_maxLiability', maxLiability); }, [maxLiability]);
  useEffect(() => { localStorage.setItem('v4_liabCap', liabilityCap); }, [liabilityCap]);
  useEffect(() => { localStorage.setItem('v4_oddsDrift', oddsDriftEnabled); }, [oddsDriftEnabled]);

  // Account queue
  const [accountQueue, setAccountQueue] = useState([]);
  const [accountBalances, setAccountBalances] = useState({});

  // Warmup
  const [warmupStatus, setWarmupStatus] = useState(null);

  // Selection
  const [selectedBets, setSelectedBets] = useState({});

  // Resolve results: { [betIdx]: { status, combined_odds, matched_odds, message } }
  const [betStatuses, setBetStatuses] = useState({});
  const [resolving, setResolving] = useState(false);

  // Allocation matrix: { [accountId]: [{ betIdx, stake, bet, resolvedOdds }] }
  const [allocationMatrix, setAllocationMatrix] = useState(null);

  // Placement results: [{ betIdx, accountId, accountLabel, stake, tippedOdds, resolvedOdds, placedOdds, status, message }]
  const [placementResults, setPlacementResults] = useState([]);

  // Per-account live progress: { [accountId]: { currentBetLabel, currentIdx, total, placing } }
  const [accountProgress, setAccountProgress] = useState({});

  const [v4Log, setV4Log] = useState([]);
  const abortRef = useRef(false);

  // Timestamp of last successful handleResolveAll completion. Used to skip the
  // pre-place recheck when allocation-time odds are still fresh (< window).
  const lastResolveTsRef = useRef(0);
  const RECHECK_SKIP_WINDOW_MS = 45000;

  // Ref indirection so the auto-retry useEffect can call the latest
  // handleRetryFailed without capturing a stale closure.
  const handleRetryFailedRef = useRef(null);

  // Auto-retry: when Execute finishes with retryable failures, fire one retry pass.
  const [autoRetryEnabled, setAutoRetryEnabled] = useState(() => localStorage.getItem('v4_autoRetry') !== 'false');
  useEffect(() => { localStorage.setItem('v4_autoRetry', autoRetryEnabled); }, [autoRetryEnabled]);
  const [autoRetryPending, setAutoRetryPending] = useState(false);

  const sessionEntries = Object.entries(sessions).filter(([, s]) => s.session_id);

  // Init account queue
  useEffect(() => {
    if (accountQueue.length === 0 && sessionEntries.length > 0) {
      setAccountQueue(sessionEntries.map(([id]) => ({ id, enabled: true })));
    }
  }, [sessionEntries.length]);

  // Fetch balances
  const fetchBalances = useCallback(async () => {
    const balances = {};
    for (const [id, s] of sessionEntries) {
      try {
        const res = await api.getBalance(s.session_id);
        const raw = res.account_balance || res.balance || '0';
        balances[id] = parseFloat(String(raw).replace(/[$,]/g, '')) || null;
      } catch { balances[id] = null; }
    }
    setAccountBalances(balances);
  }, [sessions]);

  useEffect(() => { if (sessionEntries.length > 0) fetchBalances(); }, [sessionEntries.length]);

  const enabledAccounts = accountQueue.filter((a) => a.enabled);

  const getAccountLabel = (accId) => {
    const s = sessions[accId];
    return s?.accountLabel || s?.email || accId;
  };

  /* ─── Staking Logic ─── */
  const roundToTab = (amount) => amount < 10 ? 10 : Math.round(amount / 10) * 10;

  const applyOddsDrift = (units, tippedOdds, actualOdds, minOdds) => {
    if (!oddsDriftEnabled || !actualOdds || !tippedOdds) return units;
    if (actualOdds >= tippedOdds) return units;
    if (!minOdds || minOdds <= 1) return units;
    if (actualOdds <= minOdds) return 0;
    const p = 1 / minOdds;
    const evTarget = (p * tippedOdds) - 1;
    const evActual = (p * actualOdds) - 1;
    if (evTarget <= 0 || evActual <= 0) return 0;
    const kellyTarget = evTarget / (tippedOdds - 1);
    const kellyActual = evActual / (actualOdds - 1);
    return Math.max(0, units * (kellyActual / kellyTarget));
  };

  const getStakeForBet = (bet, resolvedOdds) => {
    let units = bet.units || 1;
    if (oddsDriftEnabled && resolvedOdds && bet.odds) {
      units = applyOddsDrift(units, bet.odds, resolvedOdds, bet.min_odds || 0);
      if (units <= 0) return 0;
    }
    let stake;
    if (stakingMode === 'units') stake = units * (parseFloat(unitSize) || 10);
    else if (stakingMode === 'fixed') stake = parseFloat(fixedStake) || 10;
    else if (stakingMode === 'liability') {
      const liab = parseFloat(maxLiability) || 500;
      const odds = resolvedOdds || bet.odds || 2;
      stake = humanRoundDown(liab / odds);
    } else stake = 10;
    return Math.max(0.5, roundToTab(stake));
  };

  const totalStake = parsedBets.reduce((sum, b) => sum + getStakeForBet(b), 0);
  const selectedCount = Object.values(selectedBets).filter(Boolean).length;

  /* ─── Warmup ─── */
  const runWarmup = useCallback(async (selectedSport) => {
    const firstEnabled = enabledAccounts[0] || accountQueue.find((a) => a.enabled);
    const sid = firstEnabled ? sessions[firstEnabled.id]?.session_id : null;
    if (!sid) return;
    setWarmupStatus('warming');
    try {
      const result = await api.csbWarmup(sid, [{ sport: selectedSport.sport, competition: selectedSport.competition }]);
      setWarmupStatus(result);
    } catch (err) { setWarmupStatus({ error: err.message }); }
  }, [sessions, enabledAccounts, accountQueue]);

  const handleWarmupAll = async () => {
    const firstEnabled = enabledAccounts[0] || accountQueue.find((a) => a.enabled);
    const sid = firstEnabled ? sessions[firstEnabled.id]?.session_id : null;
    if (!sid) { setError('Login to a TAB account first.'); return; }
    setWarmupStatus('warming');
    try {
      const result = await api.csbWarmup(sid, CSB_SPORTS.map((s) => ({ sport: s.sport, competition: s.competition })));
      setWarmupStatus(result);
    } catch (err) { setWarmupStatus({ error: err.message }); }
  };

  /* ─── Parse ─── */
  const handleParse = () => {
    setError('');
    setBetStatuses({});
    setAllocationMatrix(null);
    setPlacementResults([]);
    setPhase('input');
    const bets = detectAndParse(csvText);
    if (bets.length === 0) { setError('No valid bets found. Check CSV format.'); return; }
    setParsedBets(bets);
    const sel = {};
    bets.forEach((_, idx) => { sel[idx] = true; });
    setSelectedBets(sel);
    runWarmup(sport);
  };

  const toggleBetSelection = (idx) => setSelectedBets((prev) => ({ ...prev, [idx]: !prev[idx] }));
  const toggleSelectAll = () => {
    const allSelected = parsedBets.every((_, i) => selectedBets[i]);
    const sel = {};
    parsedBets.forEach((_, i) => { sel[i] = !allSelected; });
    setSelectedBets(sel);
  };

  const handleReset = () => {
    setCsvText(''); setParsedBets([]); setBetStatuses({}); setSelectedBets({});
    setError(''); setV4Log([]); setAllocationMatrix(null); setPlacementResults([]);
    setAccountProgress({}); setPhase('input'); abortRef.current = false;
    setAutoRetryPending(false);
    lastResolveTsRef.current = 0;
  };

  /* ─── Account Queue ─── */
  const toggleAccount = (id) => setAccountQueue((prev) => prev.map((a) => a.id === id ? { ...a, enabled: !a.enabled } : a));
  const moveAccountUp = (idx) => { if (idx === 0) return; setAccountQueue((prev) => { const q = [...prev]; [q[idx - 1], q[idx]] = [q[idx], q[idx - 1]]; return q; }); };
  const moveAccountDown = (idx) => { setAccountQueue((prev) => { if (idx >= prev.length - 1) return prev; const q = [...prev]; [q[idx], q[idx + 1]] = [q[idx + 1], q[idx]]; return q; }); };

  /* ═══════════════════════════════════════════════════════════════
   *  PHASE 1: Resolve All — get live TAB odds for every selected bet
   * ═══════════════════════════════════════════════════════════════ */
  const handleResolveAll = async () => {
    setResolving(true);
    setAllocationMatrix(null);
    setPlacementResults([]);
    const firstEnabled = enabledAccounts[0];
    const sid = firstEnabled ? sessions[firstEnabled.id]?.session_id : null;
    if (!sid) { setError('Login to a TAB account first.'); setResolving(false); return; }

    const toResolve = [];
    for (let i = 0; i < parsedBets.length; i++) {
      if (selectedBets[i]) toResolve.push({ i, bet: parsedBets[i] });
    }

    // Mark all as "resolving" upfront so the UI doesn't look stuck on the later items.
    setBetStatuses((prev) => {
      const next = { ...prev };
      for (const { i } of toResolve) next[i] = { status: 'resolving' };
      return next;
    });

    // Concurrency pool. The resolve endpoint is a read-only price check, not a bet
    // placement, so parallel calls don't raise detection risk. 5-wide cuts 30s of
    // serial wait to ~6s on a 26-bet queue, which is the main pre-execute bottleneck.
    const CONCURRENCY = 5;
    let cursor = 0;

    const worker = async () => {
      while (!abortRef.current) {
        const idx = cursor++;
        if (idx >= toResolve.length) return;
        const { i, bet } = toResolve[idx];
        let statusEntry;
        try {
          const res = await api.csbResolveOne(sid, { ...bet, sport: sport.sport, competition: sport.competition });
          const combined = res.combined_odds || res.odds;
          const minOk = !bet.min_odds || combined >= bet.min_odds;
          statusEntry = {
            status: minOk ? 'resolved' : 'below_min',
            combined_odds: combined,
            matched_odds: res.matched_odds || res.leg_odds,
            message: res.message || '',
          };
        } catch (err) {
          statusEntry = { status: 'failed', message: err.message };
        }
        setBetStatuses((prev) => ({ ...prev, [i]: statusEntry }));
      }
    };

    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, toResolve.length) }, worker)
    );

    lastResolveTsRef.current = Date.now();
    setResolving(false);
    setPhase('resolved');
  };

  /* ═══════════════════════════════════════════════════════════════
   *  PHASE 2: Build Allocation Matrix
   *  Assigns bets → accounts based on liability cap + round-robin
   * ═══════════════════════════════════════════════════════════════ */
  const buildAllocationMatrix = () => {
    const cap = parseFloat(liabilityCap) || 600;
    const numAccounts = enabledAccounts.length;
    if (numAccounts === 0) { setError('Enable at least one account.'); return; }

    // matrix[accountId] = [{ slotId, accountId, betIdx, stake, bet, resolvedOdds, tippedOdds, liability }]
    // slotId is unique per allocation slot so retry can dedupe even when the same bet
    // is split across multiple slots on the same account.
    const matrix = {};
    enabledAccounts.forEach((a) => { matrix[a.id] = []; });

    let rrIdx = 0; // round-robin pointer
    // slotIds are prefixed with the allocation run timestamp so a rebuilt matrix can't
    // produce IDs that collide with stale placementResults from a previous build.
    const slotPrefix = `a${Date.now().toString(36)}`;
    let slotCounter = 0;

    const selectedIdxs = parsedBets.map((_, i) => i).filter((i) => selectedBets[i]);

    for (const i of selectedIdxs) {
      const bet = parsedBets[i];
      const st = betStatuses[i];

      // Skip unresolved or failed bets
      if (!st || st.status === 'failed') continue;

      const resolvedOdds = st.combined_odds ? parseFloat(st.combined_odds) : bet.odds;

      // Skip below min odds
      if (bet.min_odds && resolvedOdds < bet.min_odds) continue;

      const totalStakeForBet = getStakeForBet(bet, resolvedOdds);
      if (totalStakeForBet <= 0) continue;

      const odds = resolvedOdds || bet.odds || 2;
      const totalLiability = (odds - 1) * totalStakeForBet;

      if (totalLiability <= cap || numAccounts === 1) {
        // Fits on one account — assign to next in round-robin
        const accId = enabledAccounts[rrIdx % numAccounts].id;
        matrix[accId].push({
          slotId: `${slotPrefix}-${slotCounter++}`, accountId: accId,
          betIdx: i, stake: totalStakeForBet, bet, resolvedOdds, tippedOdds: bet.odds,
          liability: totalLiability, matched_odds: st.matched_odds,
        });
        rrIdx++;
      } else {
        // Needs splitting — use $5 increments so we can actually fit within the cap
        const maxStakePerSlot = Math.max(5, Math.floor((cap / (odds - 1)) / 5) * 5);

        let remaining = totalStakeForBet;
        let splitCount = 0;
        while (remaining > 0) {
          const slotStake = Math.max(5, Math.min(Math.ceil(remaining / 5) * 5, maxStakePerSlot));
          const accId = enabledAccounts[(rrIdx + splitCount) % numAccounts].id;
          matrix[accId].push({
            slotId: `${slotPrefix}-${slotCounter++}`, accountId: accId,
            betIdx: i, stake: slotStake, bet, resolvedOdds, tippedOdds: bet.odds,
            liability: (odds - 1) * slotStake, matched_odds: st.matched_odds,
          });
          remaining -= slotStake;
          splitCount++;
          if (splitCount > numAccounts * 5) break; // safety
        }
        rrIdx += splitCount;
      }
    }

    // Shuffle each account's queue independently (anti-detection)
    for (const accId of Object.keys(matrix)) {
      matrix[accId] = shuffle(matrix[accId]);
    }

    setAllocationMatrix(matrix);
    setPhase('planned');

    // Log the allocation plan
    const addLog = (msg) => setV4Log((prev) => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);
    setV4Log([]);
    addLog('═══ ALLOCATION PLAN ═══');
    let totalBetsAllocated = 0;
    let totalStakeAllocated = 0;
    for (const acc of enabledAccounts) {
      const queue = matrix[acc.id] || [];
      if (queue.length === 0) continue;
      const accLabel = getAccountLabel(acc.id);
      const accStake = queue.reduce((s, q) => s + q.stake, 0);
      const accLiab = queue.reduce((s, q) => s + q.liability, 0);
      totalBetsAllocated += queue.length;
      totalStakeAllocated += accStake;
      addLog(`${accLabel}: ${queue.length} bets — $${accStake.toFixed(0)} stake — $${accLiab.toFixed(0)} liability`);
      queue.forEach((item, qi) => {
        const legs = item.bet.bet.split('/').map((l) => l.trim());
        const shortLegs = legs.length > 2 ? `${legs[0]} + ${legs.length - 1} more` : legs.join(' / ');
        addLog(`  ${qi + 1}. Bet #${item.betIdx + 1}: $${item.stake} @ ${item.resolvedOdds?.toFixed(2)} — ${shortLegs}`);
      });
    }
    addLog(`───────────────────────`);
    addLog(`Total: ${totalBetsAllocated} placements across ${enabledAccounts.filter((a) => (matrix[a.id] || []).length > 0).length} accounts — $${totalStakeAllocated.toFixed(0)} total stake`);
    addLog('Ready to execute. Click "3. Execute" to start parallel placement.');
  };

  /* ═══════════════════════════════════════════════════════════════
   *  PHASE 3: Execute — parallel placement across all accounts
   * ═══════════════════════════════════════════════════════════════ */
  const handleExecute = async () => {
    if (!allocationMatrix) return;
    setPhase('placing');
    abortRef.current = false;
    setPlacementResults([]);
    setV4Log([]);

    const addLog = (msg) => setV4Log((prev) => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);

    // Pre-flight: refresh balances to confirm each enabled account has a healthy
    // session + reachable proxy BEFORE firing real placements. An account whose
    // balance fetch fails here is almost certainly going to 5xx on every placement,
    // so we skip it entirely rather than grinding through its queue producing losses.
    const preflightAccountIds = enabledAccounts
      .map((a) => a.id)
      .filter((id) => (allocationMatrix[id] || []).length > 0 && sessions[id]?.session_id);

    const healthy = new Set(preflightAccountIds);
    if (preflightAccountIds.length > 0) {
      addLog('Pre-flight: checking account health...');
      const results = await Promise.all(preflightAccountIds.map(async (id) => {
        const s = sessions[id];
        const lbl = getAccountLabel(id);
        let lastErr = null;
        for (let attempt = 1; attempt <= PREFLIGHT_MAX_ATTEMPTS; attempt++) {
          try {
            const res = await api.getBalance(s.session_id);
            const raw = res.account_balance || res.balance;
            if (raw != null) return { id, ok: true };
            lastErr = 'balance endpoint returned no value';
          } catch (e) {
            lastErr = e?.message || 'unknown';
            // Permanent errors (auth, session dead) — no point retrying.
            if (!isRetryableError(lastErr)) break;
          }
          if (attempt < PREFLIGHT_MAX_ATTEMPTS) {
            addLog(`  ${lbl}: pre-flight attempt ${attempt}/${PREFLIGHT_MAX_ATTEMPTS} failed (${lastErr}) — retrying...`);
            await new Promise((r) => setTimeout(r, PREFLIGHT_RETRY_DELAY_MS * attempt));
          }
        }
        return { id, ok: false, err: lastErr };
      }));
      const unhealthy = results.filter((r) => !r.ok);
      unhealthy.forEach(({ id, err }) => {
        const lbl = getAccountLabel(id);
        healthy.delete(id);
        const droppedQueue = allocationMatrix[id] || [];
        const droppedStake = droppedQueue.reduce((s, q) => s + (q.stake || 0), 0);
        addLog(`⚠ ${lbl}: pre-flight failed after ${PREFLIGHT_MAX_ATTEMPTS} attempts${err ? ` (${err})` : ''} — dropping ${droppedQueue.length} bets / $${droppedStake.toFixed(0)} stake`);
      });
      if (healthy.size === 0) {
        addLog('✗ All enabled accounts failed pre-flight. Check sessions/proxies and retry.');
        setPhase('done');
        return;
      }
      if (unhealthy.length > 0 && healthy.size > 0) {
        const droppedTotal = unhealthy.reduce((sum, r) => {
          const q = allocationMatrix[r.id] || [];
          return sum + q.reduce((s, it) => s + (it.stake || 0), 0);
        }, 0);
        addLog(`⚠ ${unhealthy.length} account(s) skipped, ${healthy.size} account(s) proceeding — $${droppedTotal.toFixed(0)} of allocation NOT placed. Fix sessions and re-run to cover the gap.`);
      }
    }

    // Build per-account async placement functions (healthy accounts only)
    const accountTasks = enabledAccounts.map((acc) => {
      const queue = allocationMatrix[acc.id] || [];
      if (queue.length === 0) return null;
      const sid = sessions[acc.id]?.session_id;
      if (!sid) return null;
      if (!healthy.has(acc.id)) return null;
      const label = getAccountLabel(acc.id);

      return async () => {
        addLog(`▶ ${label}: placing ${queue.length} bets — $${queue.reduce((s, q) => s + q.stake, 0).toFixed(0)} total`);
        setAccountProgress((prev) => ({ ...prev, [acc.id]: { currentIdx: 0, total: queue.length, placing: true, currentBetLabel: '' } }));

        // Per-account circuit breaker state: consecutive 5xx count + whether we're
        // currently in the cooldown window. Reset on any successful placement.
        let consecutive5xx = 0;

        let betCount = 0;
        for (let qi = 0; qi < queue.length; qi++) {
          if (abortRef.current) {
            addLog(`⏹ ${label}: aborted at bet ${qi + 1}/${queue.length}`);
            break;
          }

          const item = queue[qi];
          const bet = item.bet;
          const legs = bet.bet.split('/').map((l) => l.trim()).join(' / ');
          const shortLegs = legs.length > 60 ? legs.substring(0, 57) + '...' : legs;

          setAccountProgress((prev) => ({
            ...prev,
            [acc.id]: { ...prev[acc.id], currentIdx: qi, currentBetLabel: shortLegs },
          }));

          // Live recheck: re-resolve odds and Kelly-scale the stake right before placement.
          // Skipped when allocation-time odds are <45s old (rare for meaningful drift on
          // pre-game markets) — saves ~1–3s per bet. Always runs if drift is disabled? No:
          // the toggle controls both the stake math AND the extra round-trip.
          let freshOdds = item.resolvedOdds;
          let recheckOk = false;
          const recheckFresh = Date.now() - lastResolveTsRef.current < RECHECK_SKIP_WINDOW_MS;
          if (oddsDriftEnabled && !recheckFresh) {
            try {
              const rres = await api.csbResolveOne(sid, { ...bet, sport: sport.sport, competition: sport.competition });
              const c = rres.combined_odds || rres.odds;
              const parsed = c != null && c !== '' ? parseFloat(c) : NaN;
              // Sanity bounds: a valid TAB decimal price is > 1.01 and sub-1000; anything
              // else (0, negative, NaN, absurdly high) is a bad response, not drift.
              if (Number.isFinite(parsed) && parsed > 1.01 && parsed < 1000) {
                freshOdds = parsed;
                recheckOk = true;
              } else {
                addLog(`${label}: \u26A0 Bet #${item.betIdx + 1} recheck returned invalid odds (${c ?? 'null'}); using allocation odds`);
              }
            } catch (e) {
              addLog(`${label}: \u26A0 Bet #${item.betIdx + 1} recheck failed (${e?.message || 'unknown'}); using allocation odds`);
            }
          }

          let placeStake = item.stake;
          // Only reprice when we actually have fresh odds AND drift is enabled.
          // When recheckOk is false, we placed at item.stake at item.resolvedOdds as before —
          // visible via the ⚠ log above, so the operator knows this slot wasn't shielded.
          if (oddsDriftEnabled && recheckOk && bet.odds && bet.min_odds) {
            if (freshOdds <= bet.min_odds) {
              addLog(`${label}: \u2717 Bet #${item.betIdx + 1} skipped — odds ${freshOdds.toFixed(2)} below min ${bet.min_odds}`);
              setPlacementResults((prev) => [...prev, {
                slotId: item.slotId, betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
                stake: 0, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds, placedOdds: null,
                status: 'skipped', message: `Odds ${freshOdds.toFixed(2)} below minimum ${bet.min_odds}`,
                legs: bet.bet, game_id: bet.game_id,
              }]);
              setBetStatuses((prev) => ({ ...prev, [item.betIdx]: { ...prev[item.betIdx], status: 'below_min', combined_odds: freshOdds } }));
              betCount++;
              continue;
            }
            const origWhole = getStakeForBet(bet, item.resolvedOdds);
            const freshWhole = getStakeForBet(bet, freshOdds);
            if (origWhole > 0 && freshWhole > 0) {
              const scaled = roundToTab(item.stake * (freshWhole / origWhole));
              placeStake = Math.min(item.stake, scaled);
            }
          }

          // $5 split slots (from buildAllocationMatrix splitting large stakes into $5
          // increments) fall below TAB's $10 minimum — skip rather than fire a guaranteed fail.
          if (placeStake < 10) {
            addLog(`${label}: \u2717 Bet #${item.betIdx + 1} skipped — stake $${placeStake} below $10 min`);
            betCount++;
            continue;
          }

          const repricedTag = placeStake < item.stake ? ` (repriced from $${item.stake})` : '';
          addLog(`${label}: [${qi + 1}/${queue.length}] placing Bet #${item.betIdx + 1} — $${placeStake} @ ${freshOdds?.toFixed(2)} — ${shortLegs}${repricedTag}`);

          // Update bets table — mark as "placing" for this bet
          setBetStatuses((prev) => ({ ...prev, [item.betIdx]: { ...prev[item.betIdx], status: 'placing' } }));

          try {
            const res = await api.csbPlaceOne(sid, {
              ...bet,
              stake: placeStake,
              sport: sport.sport,
              competition: sport.competition,
            });

            const placedOdds = res.combined_odds || res.odds || freshOdds;
            const status = res.success ? 'placed' : 'failed';
            const errMsg = res.error || res.message || '';

            setPlacementResults((prev) => [...prev, {
              slotId: item.slotId,
              betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
              stake: res.stake || placeStake, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds,
              placedOdds, status, message: errMsg,
              legs: bet.bet, game_id: bet.game_id,
            }]);

            // Update main bet status
            setBetStatuses((prev) => ({
              ...prev,
              [item.betIdx]: {
                ...prev[item.betIdx],
                status: status === 'placed' ? 'placed' : 'failed',
                combined_odds: placedOdds,
                message: errMsg,
              },
            }));

            const icon = status === 'placed' ? '\u2713' : '\u2717';
            const oddsStr = placedOdds ? parseFloat(placedOdds).toFixed(2) : '?';
            addLog(`${label}: ${icon} Bet #${item.betIdx + 1} — $${res.stake || placeStake} @ ${oddsStr} — ${status}${errMsg ? ` — ${errMsg}` : ''}`);

            // Circuit-breaker bookkeeping on the success branch.
            if (status === 'placed') {
              consecutive5xx = 0;
            } else if (is5xxError(errMsg)) {
              consecutive5xx += 1;
            }
          } catch (err) {
            setPlacementResults((prev) => [...prev, {
              slotId: item.slotId,
              betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
              stake: placeStake, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds,
              placedOdds: null, status: 'failed', message: err.message,
              legs: bet.bet, game_id: bet.game_id,
            }]);
            setBetStatuses((prev) => ({
              ...prev,
              [item.betIdx]: { ...prev[item.betIdx], status: 'failed', message: err.message },
            }));
            addLog(`${label}: \u2717 Bet ${item.betIdx + 1} — FAILED: ${err.message}`);

            // Network exceptions (fetch throws) count against the circuit breaker too —
            // they indicate the same upstream-trouble class as explicit 5xx responses.
            if (is5xxError(err?.message) || /failed to fetch|network/i.test(err?.message || '')) {
              consecutive5xx += 1;
            }
          }

          // Circuit breaker: if we've hit the threshold, pause this account before the
          // next placement. The parallel account workers keep running — only THIS account
          // pauses. Gives the upstream time to recover without hammering it.
          if (consecutive5xx >= CIRCUIT_BREAKER_THRESHOLD && !abortRef.current) {
            const cooldownSec = (CIRCUIT_BREAKER_COOLDOWN_MS / 1000).toFixed(0);
            addLog(`${label}: ⏸ circuit breaker tripped (${consecutive5xx} upstream failures) — pausing ${cooldownSec}s`);
            await new Promise((r) => setTimeout(r, CIRCUIT_BREAKER_COOLDOWN_MS));
            consecutive5xx = 0;
            addLog(`${label}: ▶ resuming after cooldown`);
          }

          betCount++;

          // Random delay between bets (2-6s)
          if (qi < queue.length - 1 && !abortRef.current) {
            const breakInterval = 15 + Math.floor(Math.random() * 11);
            const isBreak = betCount % breakInterval === 0;
            const delay = isBreak ? randomDelay(30000, 60000) : randomDelay(1000, 2500);
            if (isBreak) {
              addLog(`${label}: ☕ human break (${(delay / 1000).toFixed(0)}s)...`);
            } else {
              addLog(`${label}: waiting ${(delay / 1000).toFixed(1)}s...`);
            }
            await new Promise((r) => setTimeout(r, delay));
          }
        }

        setAccountProgress((prev) => ({ ...prev, [acc.id]: { ...prev[acc.id], placing: false, currentIdx: queue.length } }));
        addLog(`${label}: done (${queue.length} bets processed)`);
      };
    }).filter(Boolean);

    if (accountTasks.length === 0) {
      addLog('No bets to place.');
      setPhase('done');
      return;
    }

    // Launch all accounts in parallel
    addLog(`Launching ${accountTasks.length} account streams in parallel...`);
    await Promise.all(accountTasks.map((fn) => fn()));
    addLog('All accounts finished.');
    setPhase('done');
    // Signal the auto-retry effect. Whether it actually fires is decided there
    // based on retryableFailedCount + autoRetryEnabled — so the flag can be set
    // unconditionally here.
    if (autoRetryEnabled) setAutoRetryPending(true);
  };

  /* ─── Retry Failed ───
   * Safe retry:
   *   1. Drop terminal failures (missing market, below-min, insufficient funds) — retrying
   *      won't change the outcome and historically caused the same bet to loop 4× in a row.
   *   2. Dedupe retryable failures by slotId so a split-across-slots bet isn't queued twice
   *      onto the same account (the old `find(q.betIdx === fr.betIdx)` path did exactly that).
   *   3. Re-resolve live odds and recompute a Kelly-scaled stake, capped at the original slot
   *      stake — protects against over-staking when odds drift further between main run and retry.
   *   4. Track live balance locally (initial − already-placed) and skip any slot that would
   *      exceed it, so we don't fire guaranteed-failure "Insufficient funds" placements.
   */
  const handleRetryFailed = async () => {
    if (!allocationMatrix) return;
    const addLog = (msg) => setV4Log((prev) => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);

    const allFailed = placementResults.filter((r) => r.status === 'failed');
    if (allFailed.length === 0) return;

    const terminal = [];
    const retryableRaw = [];
    for (const fr of allFailed) {
      if (isRetryableError(fr.message)) retryableRaw.push(fr);
      else terminal.push(fr);
    }

    // Dedupe retryable failures by slotId (one retry per original slot)
    const seenSlots = new Set();
    const slotsToRetry = [];
    for (const fr of retryableRaw) {
      const key = fr.slotId || `${fr.betIdx}:${fr.accountId}:${fr.stake}`;
      if (seenSlots.has(key)) continue;
      seenSlots.add(key);
      const origQueue = allocationMatrix[fr.accountId] || [];
      const origItem = fr.slotId
        ? origQueue.find((q) => q.slotId === fr.slotId)
        : origQueue.find((q) => q.betIdx === fr.betIdx && q.stake === fr.stake);
      if (origItem) slotsToRetry.push(origItem);
    }

    if (terminal.length > 0) {
      addLog(`═══ ${terminal.length} terminal failures NOT retried ═══`);
      terminal.forEach((fr) => {
        addLog(`  ✗ Bet #${fr.betIdx + 1} on ${fr.accountLabel}: ${fr.message || 'unknown error'}`);
      });
    }

    if (slotsToRetry.length === 0) {
      addLog('No retryable failures remain.');
      setPhase('done');
      return;
    }

    // Group retry slots by account + shuffle for anti-detection
    const retryMatrix = {};
    for (const item of slotsToRetry) {
      if (!retryMatrix[item.accountId]) retryMatrix[item.accountId] = [];
      retryMatrix[item.accountId].push(item);
    }
    for (const accId of Object.keys(retryMatrix)) {
      retryMatrix[accId] = shuffle(retryMatrix[accId]);
    }

    // Remove ONLY the retryable failed results so they can be replaced.
    // Terminal failures stay in placementResults with their original error.
    const retrySlotIds = new Set(slotsToRetry.map((s) => s.slotId).filter(Boolean));
    setPlacementResults((prev) => prev.filter((r) => {
      if (r.status !== 'failed') return true;
      if (r.slotId && retrySlotIds.has(r.slotId)) return false;
      if (!r.slotId && isRetryableError(r.message)) return false;
      return true;
    }));

    // Refresh balances before retry — the snapshot from page load may be minutes/hours
    // old, and during that window other activity (another browser tab, manual bets) could
    // have changed the available funds. Non-blocking: if it fails we fall back to stale.
    addLog('Refreshing account balances before retry...');
    try { await fetchBalances(); } catch (_) { /* keep stale values */ }

    // Idempotency check against TAB's actual bet history. If a slot's placement
    // succeeded at TAB but the response to us was lost (502 cascade, dropped proxy
    // connection), retrying would double-place. Before firing any retry, ask the
    // backend to look each signature up in TAB's ledger for the relevant accounts.
    // Anything that already exists there is silently reclassified as 'placed' and
    // removed from the retry queue — this is the single biggest safety net for the
    // "$1,100 of duplicate stakes" scenario.
    const retrySigs = slotsToRetry.map((s, idx) => {
      const legTokens = s.bet.bet.split('/').map((l) => l.trim().split(/\s+/)[0]).filter(Boolean);
      return {
        index: idx,
        legs: legTokens,
        combined_odds: String(s.resolvedOdds ?? s.tippedOdds ?? ''),
      };
    });
    const retryAccountNumbers = Array.from(new Set(slotsToRetry.map((s) => s.accountId)));
    let alreadyPlacedByIdx = {};
    if (retryAccountNumbers.length > 0 && retrySigs.length > 0) {
      addLog('Checking TAB history for bets that already went through...');
      try {
        const chk = await api.csbCheckPlaced(retryAccountNumbers, retrySigs, 30);
        alreadyPlacedByIdx = chk.matches || {};
      } catch (e) {
        addLog(`⚠ Idempotency check failed (${e?.message || 'unknown'}); proceeding without it`);
      }
    }

    // Promote any already-placed slots straight into placementResults and drop them
    // from the retry set. They keep their original slotId so downstream counts line up.
    const filteredSlotsToRetry = [];
    for (let i = 0; i < slotsToRetry.length; i++) {
      const match = alreadyPlacedByIdx[String(i)];
      if (match) {
        const s = slotsToRetry[i];
        const label = getAccountLabel(s.accountId);
        const stakeN = parseFloat(String(match.stake || '').replace(/[$,]/g, '')) || s.stake;
        const oddsN = parseFloat(String(match.odds || '')) || s.resolvedOdds;
        setPlacementResults((prev) => [...prev, {
          slotId: s.slotId, betIdx: s.betIdx, accountId: s.accountId, accountLabel: label,
          stake: stakeN, tippedOdds: s.tippedOdds, resolvedOdds: s.resolvedOdds,
          placedOdds: oddsN, status: 'placed', message: `recovered via TAB history (tsn ${match.tsn || '?'})`,
          legs: s.bet.bet, game_id: s.bet.game_id,
        }]);
        setBetStatuses((prev) => ({
          ...prev,
          [s.betIdx]: { ...prev[s.betIdx], status: 'placed', combined_odds: oddsN, message: 'recovered via TAB history' },
        }));
        addLog(`↩ recovered Bet #${s.betIdx + 1} on ${label} — already placed at TAB (tsn ${match.tsn || '?'}); skipping retry`);
      } else {
        filteredSlotsToRetry.push(slotsToRetry[i]);
      }
    }

    // If every slot was already placed at TAB, we're done — no retry needed.
    if (filteredSlotsToRetry.length === 0) {
      addLog('All retry candidates were recovered from TAB history. Nothing to retry.');
      setPhase('done');
      return;
    }

    // Rebuild per-account retry matrix from the surviving slots.
    for (const accId of Object.keys(retryMatrix)) retryMatrix[accId] = [];
    for (const item of filteredSlotsToRetry) {
      if (!retryMatrix[item.accountId]) retryMatrix[item.accountId] = [];
      retryMatrix[item.accountId].push(item);
    }
    for (const accId of Object.keys(retryMatrix)) {
      retryMatrix[accId] = shuffle(retryMatrix[accId]);
    }

    // Seed live balance tracking: initial − already-successfully-placed
    const placedPerAccount = {};
    for (const r of placementResults) {
      if (r.status === 'placed') {
        placedPerAccount[r.accountId] = (placedPerAccount[r.accountId] || 0) + (r.stake || 0);
      }
    }

    setPhase('placing');
    abortRef.current = false;
    addLog(`═══ RETRYING ${slotsToRetry.length} FAILED BETS ═══`);

    const accountTasks = enabledAccounts.map((acc) => {
      const queue = retryMatrix[acc.id] || [];
      if (queue.length === 0) return null;
      const sid = sessions[acc.id]?.session_id;
      if (!sid) return null;
      const label = getAccountLabel(acc.id);
      const initBal = accountBalances[acc.id];
      let liveBalance = (initBal != null)
        ? initBal - (placedPerAccount[acc.id] || 0)
        : null;

      return async () => {
        const balTag = liveBalance != null ? ` (bal ~$${liveBalance.toFixed(0)})` : '';
        addLog(`▶ ${label}: retrying ${queue.length} bets${balTag}`);
        setAccountProgress((prev) => ({ ...prev, [acc.id]: { currentIdx: 0, total: queue.length, placing: true, currentBetLabel: '' } }));

        // Circuit breaker — same semantics as main execute. Resets on every placed bet.
        let consecutive5xx = 0;

        for (let qi = 0; qi < queue.length; qi++) {
          if (abortRef.current) { addLog(`⏹ ${label}: aborted at bet ${qi + 1}/${queue.length}`); break; }
          const item = queue[qi];
          const bet = item.bet;
          const legs = bet.bet.split('/').map((l) => l.trim()).join(' / ');
          const shortLegs = legs.length > 60 ? legs.substring(0, 57) + '...' : legs;

          setAccountProgress((prev) => ({
            ...prev,
            [acc.id]: { ...prev[acc.id], currentIdx: qi, currentBetLabel: shortLegs },
          }));

          // Step 1: re-resolve for fresh odds. Skipped when allocation-time odds are
          // still fresh (<45s) — typical for an auto-retry fired immediately after Execute.
          let freshOdds = item.resolvedOdds;
          let recheckOk = false;
          const recheckFresh = Date.now() - lastResolveTsRef.current < RECHECK_SKIP_WINDOW_MS;
          if (oddsDriftEnabled && !recheckFresh) {
            try {
              const rres = await api.csbResolveOne(sid, { ...bet, sport: sport.sport, competition: sport.competition });
              const c = rres.combined_odds || rres.odds;
              const parsed = c != null && c !== '' ? parseFloat(c) : NaN;
              if (Number.isFinite(parsed) && parsed > 1.01 && parsed < 1000) {
                freshOdds = parsed;
                recheckOk = true;
              } else {
                addLog(`${label}: \u26A0 Bet #${item.betIdx + 1} recheck returned invalid odds (${c ?? 'null'}); using allocation odds`);
              }
            } catch (e) {
              addLog(`${label}: \u26A0 Bet #${item.betIdx + 1} recheck failed (${e?.message || 'unknown'}); using allocation odds`);
            }
          }
          if (abortRef.current) { addLog(`⏹ ${label}: aborted at bet ${qi + 1}/${queue.length}`); break; }

          // Step 2: Kelly-scale the slot stake based on fresh odds, capped at original.
          // Only reprices when recheck succeeded — stale-odds fallback keeps item.stake.
          let retryStake = item.stake;
          if (oddsDriftEnabled && recheckOk && bet.odds && bet.min_odds) {
            if (freshOdds <= bet.min_odds) {
              addLog(`${label}: \u2717 Bet #${item.betIdx + 1} skipped — odds ${freshOdds.toFixed(2)} below min ${bet.min_odds}`);
              setPlacementResults((prev) => [...prev, {
                slotId: item.slotId, betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
                stake: 0, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds, placedOdds: null,
                status: 'skipped', message: `Odds ${freshOdds.toFixed(2)} below minimum ${bet.min_odds}`,
                legs: bet.bet, game_id: bet.game_id,
              }]);
              setBetStatuses((prev) => ({ ...prev, [item.betIdx]: { ...prev[item.betIdx], status: 'below_min', combined_odds: freshOdds } }));
              continue;
            }
            const origWhole = getStakeForBet(bet, item.resolvedOdds);
            const freshWhole = getStakeForBet(bet, freshOdds);
            if (origWhole > 0 && freshWhole > 0) {
              const scaled = roundToTab(item.stake * (freshWhole / origWhole));
              retryStake = Math.min(item.stake, scaled);
            }
          }

          if (retryStake < 10) {
            addLog(`${label}: \u2717 Bet #${item.betIdx + 1} skipped — stake $${retryStake} below $10 min`);
            continue;
          }

          // Step 3: balance check
          if (liveBalance != null && retryStake > liveBalance) {
            addLog(`${label}: \u2717 Bet #${item.betIdx + 1} skipped — est balance $${liveBalance.toFixed(0)} < stake $${retryStake}`);
            setPlacementResults((prev) => [...prev, {
              slotId: item.slotId, betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
              stake: retryStake, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds, placedOdds: null,
              status: 'skipped', message: 'Insufficient balance for retry',
              legs: bet.bet, game_id: bet.game_id,
            }]);
            continue;
          }

          const repricedTag = retryStake < item.stake ? ` (repriced from $${item.stake})` : '';
          addLog(`${label}: [${qi + 1}/${queue.length}] retrying Bet #${item.betIdx + 1} — $${retryStake} @ ${freshOdds?.toFixed(2)} — ${shortLegs}${repricedTag}`);
          setBetStatuses((prev) => ({ ...prev, [item.betIdx]: { ...prev[item.betIdx], status: 'placing' } }));

          try {
            const res = await api.csbPlaceOne(sid, { ...bet, stake: retryStake, sport: sport.sport, competition: sport.competition });
            const placedOdds = res.combined_odds || res.odds || freshOdds;
            const status = res.success ? 'placed' : 'failed';
            const errMsg = res.error || res.message || '';

            setPlacementResults((prev) => [...prev, {
              slotId: item.slotId, betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
              stake: res.stake || retryStake, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds,
              placedOdds, status, message: errMsg, legs: bet.bet, game_id: bet.game_id,
            }]);
            setBetStatuses((prev) => ({
              ...prev,
              [item.betIdx]: { ...prev[item.betIdx], status: status === 'placed' ? 'placed' : 'failed', combined_odds: placedOdds, message: errMsg },
            }));

            if (status === 'placed' && liveBalance != null) {
              liveBalance -= (res.stake || retryStake);
            }

            const icon = status === 'placed' ? '\u2713' : '\u2717';
            addLog(`${label}: ${icon} Bet #${item.betIdx + 1} — $${res.stake || retryStake} @ ${placedOdds ? parseFloat(placedOdds).toFixed(2) : '?'} — ${status}${errMsg ? ` — ${errMsg}` : ''}`);

            if (status === 'placed') consecutive5xx = 0;
            else if (is5xxError(errMsg)) consecutive5xx += 1;
          } catch (err) {
            setPlacementResults((prev) => [...prev, {
              slotId: item.slotId, betIdx: item.betIdx, accountId: acc.id, accountLabel: label,
              stake: retryStake, tippedOdds: item.tippedOdds, resolvedOdds: freshOdds,
              placedOdds: null, status: 'failed', message: err.message, legs: bet.bet, game_id: bet.game_id,
            }]);
            setBetStatuses((prev) => ({ ...prev, [item.betIdx]: { ...prev[item.betIdx], status: 'failed', message: err.message } }));
            addLog(`${label}: \u2717 Bet #${item.betIdx + 1} — FAILED: ${err.message}`);

            if (is5xxError(err?.message) || /failed to fetch|network/i.test(err?.message || '')) {
              consecutive5xx += 1;
            }
          }

          // Circuit breaker — pause this account after a run of upstream failures.
          if (consecutive5xx >= CIRCUIT_BREAKER_THRESHOLD && !abortRef.current) {
            const cooldownSec = (CIRCUIT_BREAKER_COOLDOWN_MS / 1000).toFixed(0);
            addLog(`${label}: ⏸ circuit breaker tripped (${consecutive5xx} upstream failures) — pausing ${cooldownSec}s`);
            await new Promise((r) => setTimeout(r, CIRCUIT_BREAKER_COOLDOWN_MS));
            consecutive5xx = 0;
            addLog(`${label}: ▶ resuming after cooldown`);
          }

          if (qi < queue.length - 1 && !abortRef.current) {
            const delay = randomDelay(1000, 2500);
            addLog(`${label}: waiting ${(delay / 1000).toFixed(1)}s...`);
            await new Promise((r) => setTimeout(r, delay));
          }
        }
        setAccountProgress((prev) => ({ ...prev, [acc.id]: { ...prev[acc.id], placing: false, currentIdx: queue.length } }));
        addLog(`${label}: retry done`);
      };
    }).filter(Boolean);

    if (accountTasks.length === 0) { addLog('No failed bets to retry.'); setPhase('done'); return; }
    await Promise.all(accountTasks.map((fn) => fn()));
    // Mark that fresh resolves just ran so any *follow-up* action (user clicks Retry
    // again manually) can still leverage the freshness window.
    lastResolveTsRef.current = Date.now();
    addLog('Retry finished.');
    setPhase('done');
  };

  /* ─── Derived counts ─── */
  const placedCount = placementResults.filter((r) => r.status === 'placed').length;
  const failedPlacementCount = placementResults.filter((r) => r.status === 'failed').length;
  const retryableFailedCount = placementResults.filter((r) => r.status === 'failed' && isRetryableError(r.message)).length;
  const terminalFailedCount = failedPlacementCount - retryableFailedCount;
  const skippedCount = placementResults.filter((r) => r.status === 'skipped').length;

  // Keep the ref pointing at the latest handleRetryFailed so the auto-retry effect
  // doesn't fire a stale closure (which would read pre-run placementResults).
  useEffect(() => { handleRetryFailedRef.current = handleRetryFailed; });

  // Auto-retry trigger: when Execute completes with retryable failures, fire one
  // retry pass after a short delay (lets React flush the final state updates).
  useEffect(() => {
    if (!autoRetryPending || phase !== 'done') return;
    if (retryableFailedCount === 0) { setAutoRetryPending(false); return; }
    const t = setTimeout(() => {
      setAutoRetryPending(false);
      handleRetryFailedRef.current?.();
    }, 1500);
    return () => clearTimeout(t);
  }, [autoRetryPending, phase, retryableFailedCount]);
  const totalAllocated = allocationMatrix ? Object.values(allocationMatrix).reduce((s, q) => s + q.length, 0) : 0;

  /* ═══════════════════════════════════════════════════════════════
   *  RENDER
   * ═══════════════════════════════════════════════════════════════ */
  return (
    <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* ─── Warmup Panel ─── */}
      <div className="card" style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>Prop Cache</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {CSB_SPORTS.map((s) => {
              const warmResult = warmupStatus?.results?.find?.((r) => r.competition === s.competition);
              const isWarming = warmupStatus === 'warming' && sport.label === s.label;
              return (
                <button key={s.label} onClick={() => { setSport(s); runWarmup(s); }} disabled={warmupStatus === 'warming'}
                  className="btn btn-secondary" style={{ padding: '6px 14px', fontSize: 12, borderColor: warmResult ? 'var(--success)' : undefined, color: warmResult ? 'var(--success)' : undefined }}>
                  {isWarming && <Loader2 size={12} className="animate-spin" style={{ marginRight: 4 }} />}
                  {warmResult && <CheckCircle size={12} style={{ marginRight: 4, color: 'var(--success)' }} />}
                  {s.label}
                </button>
              );
            })}
            <button onClick={handleWarmupAll} disabled={warmupStatus === 'warming' || sessionEntries.length === 0}
              className="btn btn-primary" style={{ padding: '6px 14px', fontSize: 12 }}>
              {warmupStatus === 'warming' ? <><Loader2 size={12} className="animate-spin" /> Warming...</> : <><Zap size={12} /> Warm All</>}
            </button>
          </div>
          {sessionEntries.length === 0 && <span style={{ fontSize: 12, color: 'var(--warning)' }}>Login to a TAB account first</span>}
        </div>
        {warmupStatus && warmupStatus !== 'warming' && warmupStatus.results && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
            {warmupStatus.results.map((r) => (
              <div key={r.competition} style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{r.competition}</span>
                {r.error ? <span style={{ color: 'var(--danger)', marginLeft: 6 }}>{r.error}</span> : (
                  <><span style={{ marginLeft: 6 }}>{r.matches_count} matches</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{r.props_count.toLocaleString()} props</span>
                    <span style={{ color: r.cached ? 'var(--success)' : 'var(--text-muted)', marginLeft: 4 }}>{r.cached ? 'cached' : `${r.seconds}s`}</span></>
                )}
              </div>
            ))}
          </div>
        )}
        {warmupStatus?.error && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--danger)' }}>{warmupStatus.error}</div>}
      </div>

      {parsedBets.length === 0 ? (
        /* ─── CSV Input ─── */
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 8 }}>
              Paste CSV data below (SGM or Multi format)
            </label>
            <textarea value={csvText} onChange={(e) => setCsvText(e.target.value)}
              placeholder={`SGM format:\nBet Type,Game ID,Bet,Odds,SGM Min Odds,EV %,Units,Leg 1 TAB Odds,Leg 2 TAB Odds\nSGM,20260328_NME_ESS,Harry Sheezel 30+ Disposals/Nate Caddy 15+ Disposals,5.5,3.87,41.73%,1.5,1.33,3.1\n\nMulti format:\nBet Type,Bet,Odds,Min Odds,EV %,Units,Teams,Leg 1 TAB Odds,Leg 2 TAB Odds\nMulti,Luke Davies-Uniacke 30+ Disposals/Campbell Chesser 15+ Disposals,3.89,3.262,19.10%,1.7,,2.1,1.85`}
              rows={12} className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '12px 16px', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }} />
          </div>
          {error && <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13, whiteSpace: 'pre-wrap' }}>{error}</div>}
          <button onClick={handleParse} disabled={!csvText.trim()} className="btn btn-primary" style={{ alignSelf: 'flex-start' }}>
            <ClipboardPaste size={16} /> Parse CSV
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

          {/* ─── Summary bar ─── */}
          <div className="card" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 24, padding: '14px 20px' }}>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Bets: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{parsedBets.length}</span></div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Selected: <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{selectedCount}</span></div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Total Stake: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>${totalStake.toFixed(2)}</span></div>
            {allocationMatrix && <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Allocated: <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{totalAllocated} placements</span></div>}
            {placedCount > 0 && <div style={{ fontSize: 13 }}>Placed: <span style={{ color: 'var(--success)', fontWeight: 600 }}>{placedCount}</span></div>}
            {failedPlacementCount > 0 && <div style={{ fontSize: 13 }}>Failed: <span style={{ color: 'var(--danger)', fontWeight: 600 }}>{failedPlacementCount}</span></div>}
            {skippedCount > 0 && <div style={{ fontSize: 13 }}>Skipped: <span style={{ color: 'var(--warning)', fontWeight: 600 }}>{skippedCount}</span></div>}
          </div>

          {/* ─── Config Panel ─── */}
          {phase !== 'placing' && phase !== 'done' && (
            <div className="card">
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24 }}>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Sport</label>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {CSB_SPORTS.map((s) => (
                      <button key={s.label} onClick={() => { setSport(s); runWarmup(s); }}
                        className={sport.label === s.label ? 'btn btn-primary' : 'btn btn-secondary'} style={{ padding: '6px 14px', fontSize: 12 }}>
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Staking</label>
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                    {[{ key: 'units', label: 'By Units' }, { key: 'fixed', label: 'Fixed Stake' }, { key: 'liability', label: 'Max Liability' }].map((mode) => (
                      <button key={mode.key} onClick={() => setStakingMode(mode.key)}
                        className={stakingMode === mode.key ? 'btn btn-primary' : 'btn btn-secondary'} style={{ padding: '6px 14px', fontSize: 12 }}>
                        {mode.label}
                      </button>
                    ))}
                    {stakingMode === 'units' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Unit $</span>
                        <input type="number" value={unitSize} onChange={(e) => setUnitSize(e.target.value)} min="1"
                          className="t-input" style={{ width: 72, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }} />
                      </div>
                    )}
                    {stakingMode === 'fixed' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>$</span>
                        <input type="number" value={fixedStake} onChange={(e) => setFixedStake(e.target.value)} min="1"
                          className="t-input" style={{ width: 72, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }} />
                      </div>
                    )}
                    {stakingMode === 'liability' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Max $</span>
                        <input type="number" value={maxLiability} onChange={(e) => setMaxLiability(e.target.value)} min="10"
                          className="t-input" style={{ width: 80, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }} />
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>stake = max / odds</span>
                      </div>
                    )}
                  </div>
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Odds Drift</label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                    <input type="checkbox" checked={oddsDriftEnabled} onChange={() => setOddsDriftEnabled((v) => !v)} />
                    Scale stake <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Reduce units when TAB odds &lt; tipped</span>
                  </label>
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Auto-Retry</label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                    <input type="checkbox" checked={autoRetryEnabled} onChange={() => setAutoRetryEnabled((v) => !v)} />
                    Auto-retry failures <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>One pass after Execute finishes</span>
                  </label>
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Liability Cap</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>$</span>
                    <input type="number" value={liabilityCap} onChange={(e) => setLiabilityCap(e.target.value)} min="100" step="100"
                      className="t-input" style={{ width: 80, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }} />
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Max liability per account per bet</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ─── Account Queue ─── */}
          <div>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12 }}>Account Queue</h2>
            {sessionEntries.length === 0 ? (
              <p style={{ color: 'var(--warning)', fontSize: 13 }}>Login to a TAB account on the Dashboard first.</p>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                {accountQueue.map((acc, idx) => {
                  const s = sessions[acc.id];
                  if (!s) return null;
                  const label = s.accountLabel || s.email || acc.id;
                  const bal = accountBalances[acc.id];
                  const enabledIdx = enabledAccounts.findIndex((a) => a.id === acc.id);
                  const prog = accountProgress[acc.id];
                  const queueLen = allocationMatrix?.[acc.id]?.length || 0;
                  return (
                    <div key={acc.id} className="card" style={{
                      minWidth: 200, opacity: acc.enabled ? 1 : 0.4, transition: 'all 0.2s',
                      outline: prog?.placing ? '2px solid var(--primary)' : 'none', outlineOffset: -1,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <input type="checkbox" checked={acc.enabled} onChange={() => toggleAccount(acc.id)} disabled={phase === 'placing'} />
                          {acc.enabled && enabledIdx >= 0 && (
                            <span style={{ background: 'var(--primary)', color: '#fff', fontSize: 11, fontWeight: 700, width: 20, height: 20, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                              {enabledIdx + 1}
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button onClick={() => moveAccountUp(idx)} disabled={phase === 'placing' || idx === 0} className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)', opacity: phase === 'placing' || idx === 0 ? 0.3 : 1 }}>
                            <ArrowUp size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                          <button onClick={() => moveAccountDown(idx)} disabled={phase === 'placing' || idx === accountQueue.length - 1} className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)', opacity: phase === 'placing' || idx === accountQueue.length - 1 ? 0.3 : 1 }}>
                            <ArrowDown size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                        </div>
                      </div>
                      <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</p>
                      {s.account_number && <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>#{s.account_number}</p>}
                      <p style={{ fontSize: 13, marginTop: 6, marginBottom: 0 }}>
                        {bal != null ? <span style={{ color: bal > 0 ? 'var(--success)' : 'var(--danger)' }}>${bal.toFixed(2)}</span> : <span style={{ color: 'var(--text-muted)' }}>Balance unknown</span>}
                      </p>
                      {queueLen > 0 && <p style={{ fontSize: 11, color: 'var(--primary)', marginTop: 4, marginBottom: 0 }}>{queueLen} bets queued</p>}
                      {prog?.placing && (
                        <div style={{ marginTop: 6 }}>
                          <div style={{ width: '100%', background: 'var(--bg-input)', borderRadius: 9999, height: 4, overflow: 'hidden' }}>
                            <div style={{ background: 'var(--primary)', height: '100%', borderRadius: 9999, transition: 'width 0.3s', width: `${((prog.currentIdx + 1) / prog.total) * 100}%` }} />
                          </div>
                          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '4px 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {prog.currentIdx + 1}/{prog.total}: {prog.currentBetLabel}
                          </p>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* ─── Action Buttons ─── */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
            {phase === 'input' && (
              <button onClick={handleResolveAll} disabled={resolving || selectedCount === 0} className="btn btn-secondary">
                {resolving ? <><Loader2 size={16} className="animate-spin" /> Resolving...</> : <><Search size={16} /> 1. Resolve All</>}
              </button>
            )}
            {phase === 'resolved' && (
              <>
                <button onClick={handleResolveAll} disabled={resolving || selectedCount === 0} className="btn btn-secondary">
                  {resolving ? <><Loader2 size={16} className="animate-spin" /> Resolving...</> : <><Search size={16} /> Re-Resolve</>}
                </button>
                <button onClick={buildAllocationMatrix} disabled={enabledAccounts.length === 0} className="btn btn-primary">
                  <ListChecks size={16} /> 2. Plan Placement
                </button>
              </>
            )}
            {phase === 'planned' && (
              <>
                <button onClick={buildAllocationMatrix} className="btn btn-secondary">
                  <ListChecks size={16} /> Re-Plan
                </button>
                <button onClick={handleExecute} className="btn btn-primary" style={{ background: 'var(--success)', borderColor: 'var(--success)' }}>
                  <Play size={16} /> 3. Execute
                </button>
              </>
            )}
            {phase === 'placing' && (
              <button onClick={() => { abortRef.current = true; }} className="btn btn-danger">
                <StopCircle size={16} /> Abort
              </button>
            )}
            {phase === 'done' && failedPlacementCount > 0 && (
              <button
                onClick={handleRetryFailed}
                disabled={retryableFailedCount === 0}
                title={terminalFailedCount > 0 ? `${terminalFailedCount} terminal failure(s) will be skipped` : ''}
                className="btn btn-primary"
                style={{
                  background: retryableFailedCount > 0 ? 'var(--warning)' : 'var(--bg-card)',
                  borderColor: 'var(--warning)',
                  opacity: retryableFailedCount > 0 ? 1 : 0.5,
                }}
              >
                <RotateCcw size={16} /> Retry {retryableFailedCount} Failed{terminalFailedCount > 0 ? ` (${terminalFailedCount} terminal)` : ''}
              </button>
            )}
            <button onClick={handleReset} className="btn btn-secondary" disabled={phase === 'placing'}>
              <RotateCcw size={16} /> Reset
            </button>
            <button onClick={fetchBalances} className="btn btn-secondary" style={{ marginLeft: 'auto' }}>
              <RefreshCw size={16} /> Refresh Balances
            </button>
          </div>

          {/* ─── Allocation Matrix Preview ─── */}
          {allocationMatrix && phase !== 'input' && (
            <div className="card" style={{ padding: '16px 20px' }}>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12 }}>Allocation Matrix</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {enabledAccounts.map((acc) => {
                  const queue = allocationMatrix[acc.id] || [];
                  if (queue.length === 0) return null;
                  const label = getAccountLabel(acc.id);
                  const totalAccStake = queue.reduce((s, q) => s + q.stake, 0);
                  const totalAccLiab = queue.reduce((s, q) => s + q.liability, 0);
                  return (
                    <div key={acc.id}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{label}</span>
                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{queue.length} bets</span>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Stake: ${totalAccStake.toFixed(0)}</span>
                        <span style={{ fontSize: 12, color: 'var(--warning)' }}>Liability: ${totalAccLiab.toFixed(0)}</span>
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {queue.map((item, qi) => (
                          <span key={qi} style={{
                            display: 'inline-block', padding: '3px 8px', borderRadius: 'var(--radius-sm)', fontSize: 11,
                            background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border)',
                          }}>
                            #{item.betIdx + 1} ${item.stake} @{item.resolvedOdds?.toFixed(2)}
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* ─── Engine Log ─── */}
          {v4Log.length > 0 && (
            <div className="card" style={{ padding: '12px 16px', maxHeight: 240, overflowY: 'auto', fontFamily: 'monospace', fontSize: 12 }}>
              {v4Log.map((line, i) => (
                <div key={i} style={{
                  color: line.includes('\u2713') ? 'var(--success)' : line.includes('\u2717') ? 'var(--danger)' : line.includes('\u2298') ? 'var(--warning)' : 'var(--text-secondary)',
                  padding: '2px 0',
                }}>{line}</div>
              ))}
            </div>
          )}

          {/* ─── Bets Table ─── */}
          <div style={{ overflowX: 'auto' }}>
            <table className="t-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border)' }}>
                  <th style={{ padding: '8px 6px', width: 32 }}>
                    <input type="checkbox" checked={selectedCount === parsedBets.length && parsedBets.length > 0} onChange={toggleSelectAll} disabled={phase === 'placing' || phase === 'done'} />
                  </th>
                  <th style={{ padding: '8px 6px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 600 }}>#</th>
                  <th style={{ padding: '8px 6px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 600 }}>Type</th>
                  <th style={{ padding: '8px 6px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 600 }}>Game</th>
                  <th style={{ padding: '8px 6px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 600 }}>Legs</th>
                  <th style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>Tipped</th>
                  <th style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>TAB</th>
                  <th style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>Min</th>
                  <th style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>EV%</th>
                  <th style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>Stake</th>
                  <th style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>Liability</th>
                  <th style={{ padding: '8px 6px', textAlign: 'center', color: 'var(--text-secondary)', fontWeight: 600 }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {parsedBets.map((bet, idx) => {
                  const st = betStatuses[idx];
                  const resolvedOdds = st?.combined_odds ? parseFloat(st.combined_odds) : null;
                  const stake = getStakeForBet(bet, resolvedOdds);
                  const legs = bet.bet.split('/');
                  return (
                    <tr key={idx} style={{
                      background: st ? (
                        st.status === 'placed' ? 'var(--success-muted)'
                        : st.status === 'below_min' ? 'var(--danger-muted)'
                        : st.status === 'failed' ? 'var(--danger-muted)'
                        : st.status === 'placing' ? 'var(--accent-muted)'
                        : st.status === 'resolved' && resolvedOdds && resolvedOdds < bet.odds ? 'var(--warning-muted)'
                        : st.status === 'resolved' ? 'var(--success-muted)'
                        : 'transparent'
                      ) : 'transparent',
                      borderBottom: '1px solid var(--border)',
                      opacity: selectedBets[idx] ? 1 : 0.4,
                      transition: 'all 0.2s',
                    }}>
                      <td style={{ padding: '8px 6px' }}>
                        <input type="checkbox" checked={!!selectedBets[idx]} onChange={() => toggleBetSelection(idx)} disabled={phase === 'placing' || phase === 'done'} />
                      </td>
                      <td style={{ padding: '8px 6px', color: 'var(--text-muted)' }}>{idx + 1}</td>
                      <td style={{ padding: '8px 6px' }}>
                        <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 600, background: bet.bet_type === 'SGM' ? 'var(--primary)' : 'var(--accent)', color: '#fff' }}>
                          {bet.bet_type}
                        </span>
                      </td>
                      <td style={{ padding: '8px 6px', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                        {bet.game_id || bet.teams || '\u2014'}
                      </td>
                      <td style={{ padding: '8px 6px', maxWidth: 300 }}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          {legs.map((leg, li) => (
                            <span key={li} style={{ fontSize: 12, color: 'var(--text-primary)' }}>
                              {leg.trim()}
                              {st?.matched_odds?.[li] != null && (
                                <span style={{ color: 'var(--text-muted)', marginLeft: 4, fontSize: 11 }}>@{parseFloat(st.matched_odds[li]).toFixed(2)}</span>
                              )}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-muted)' }}>{bet.odds.toFixed(2)}</td>
                      <td style={{ padding: '8px 6px', textAlign: 'right', fontWeight: 500 }}>
                        {resolvedOdds ? (
                          <span style={{ color: resolvedOdds >= (bet.min_odds || 0) ? 'var(--success)' : 'var(--warning)' }}>
                            {resolvedOdds.toFixed(2)}
                          </span>
                        ) : '\u2014'}
                      </td>
                      <td style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--text-muted)' }}>{bet.min_odds.toFixed(2)}</td>
                      <td style={{ padding: '8px 6px', textAlign: 'right' }}>
                        <span style={{ color: bet.ev_pct >= 10 ? 'var(--success)' : bet.ev_pct >= 5 ? 'var(--warning)' : 'var(--text-muted)', fontWeight: 500 }}>
                          {bet.ev_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td style={{ padding: '8px 6px', textAlign: 'right', fontWeight: 600 }}>${stake.toFixed(0)}</td>
                      <td style={{ padding: '8px 6px', textAlign: 'right', color: 'var(--warning)', fontWeight: 500 }}>
                        {(() => {
                          const odds = resolvedOdds || bet.odds || 2;
                          const liab = (odds - 1) * stake;
                          const cap = parseFloat(liabilityCap) || 600;
                          return <span style={{ color: liab > cap ? 'var(--danger)' : 'var(--warning)' }}>${liab.toFixed(0)}</span>;
                        })()}
                      </td>
                      <td style={{ padding: '8px 6px', textAlign: 'center' }}>
                        {st ? (
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                            <StatusIcon status={st.status} />
                          </div>
                        ) : '\u2014'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* ═══════════════════════════════════════════════════════════
           *  FINAL REPORT — bet-centric view of what got placed where
           * ═══════════════════════════════════════════════════════════ */}
          {phase === 'done' && placementResults.length > 0 && (
            <div className="card" style={{ padding: '20px' }}>
              <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 16 }}>Final Report</h3>

              {/* Group results by betIdx */}
              {(() => {
                const grouped = {};
                placementResults.forEach((r) => {
                  if (!grouped[r.betIdx]) grouped[r.betIdx] = [];
                  grouped[r.betIdx].push(r);
                });

                const betIdxs = Object.keys(grouped).map(Number).sort((a, b) => a - b);
                const grandTotalStake = placementResults.filter((r) => r.status === 'placed').reduce((s, r) => s + r.stake, 0);
                const grandTotalLiab = placementResults.filter((r) => r.status === 'placed').reduce((s, r) => s + (((r.placedOdds || r.resolvedOdds || 2) - 1) * r.stake), 0);

                return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                    {betIdxs.map((betIdx) => {
                      const results = grouped[betIdx];
                      const bet = parsedBets[betIdx];
                      if (!bet) return null;
                      const legs = bet.bet.split('/').map((l) => l.trim()).join(' / ');

                      return (
                        <div key={betIdx} style={{ borderBottom: '1px solid var(--border)', paddingBottom: 16 }}>
                          <div style={{ marginBottom: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                              Bet {betIdx + 1}: {bet.bet_type}
                            </span>
                            {bet.game_id && <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>{bet.game_id}</span>}
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.5 }}>
                            {legs}
                          </div>
                          {(() => {
                            // Tipped stake = what you'd stake at tipped odds (no drift)
                            const tippedStake = getStakeForBet(bet, bet.odds);
                            // Actual total across accounts
                            const actualTotal = results.filter((r) => r.status === 'placed').reduce((s, r) => s + r.stake, 0);
                            return (
                              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>
                                Tipped: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{bet.odds.toFixed(2)}</span>
                                <span style={{ marginLeft: 4, color: 'var(--text-secondary)' }}>(${tippedStake.toFixed(0)} × {bet.units}u)</span>
                                <span style={{ margin: '0 8px' }}>|</span>
                                Min: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{bet.min_odds.toFixed(2)}</span>
                                <span style={{ margin: '0 8px' }}>|</span>
                                EV: <span style={{ color: bet.ev_pct >= 10 ? 'var(--success)' : 'var(--warning)', fontWeight: 600 }}>{bet.ev_pct.toFixed(1)}%</span>
                                {actualTotal > 0 && (
                                  <>
                                    <span style={{ margin: '0 8px' }}>|</span>
                                    Actual: <span style={{ color: 'var(--success)', fontWeight: 600 }}>${actualTotal.toFixed(0)}</span>
                                  </>
                                )}
                              </div>
                            );
                          })()}

                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                            <thead>
                              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                                <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Account</th>
                                <th style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontWeight: 600 }}>Stake</th>
                                <th style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontWeight: 600 }}>Odds</th>
                                <th style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontWeight: 600 }}>Liability</th>
                                <th style={{ padding: '6px 8px', textAlign: 'center', color: 'var(--text-muted)', fontWeight: 600 }}>Status</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Note</th>
                              </tr>
                            </thead>
                            <tbody>
                              {results.map((r, ri) => {
                                const odds = r.placedOdds || r.resolvedOdds || 0;
                                const liab = r.status === 'placed' ? (odds - 1) * r.stake : 0;
                                return (
                                  <tr key={ri} style={{
                                    background: r.status === 'placed' ? 'var(--success-muted)' : r.status === 'failed' ? 'var(--danger-muted)' : 'var(--warning-muted)',
                                    borderBottom: '1px solid var(--border)',
                                  }}>
                                    <td style={{ padding: '6px 8px', fontWeight: 500 }}>{r.accountLabel}</td>
                                    <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>${r.stake}</td>
                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                                      {odds > 0 ? (
                                        <span style={{ color: odds >= bet.odds ? 'var(--success)' : odds >= bet.min_odds ? 'var(--text-primary)' : 'var(--warning)' }}>
                                          {parseFloat(odds).toFixed(2)}
                                        </span>
                                      ) : '\u2014'}
                                    </td>
                                    <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--warning)' }}>
                                      {liab > 0 ? `$${liab.toFixed(0)}` : '\u2014'}
                                    </td>
                                    <td style={{ padding: '6px 8px', textAlign: 'center' }}><StatusIcon status={r.status} /></td>
                                    <td style={{ padding: '6px 8px', fontSize: 11, color: 'var(--text-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.message}>
                                      {r.message || ''}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      );
                    })}

                    {/* Grand totals */}
                    <div style={{ display: 'flex', gap: 24, padding: '12px 0', borderTop: '2px solid var(--border)' }}>
                      <div style={{ fontSize: 14, fontWeight: 700 }}>
                        Total Placed: <span style={{ color: 'var(--success)' }}>{placedCount}</span> / {placementResults.length}
                      </div>
                      <div style={{ fontSize: 14, fontWeight: 700 }}>
                        Total Staked: <span style={{ color: 'var(--text-primary)' }}>${grandTotalStake.toFixed(0)}</span>
                      </div>
                      <div style={{ fontSize: 14, fontWeight: 700 }}>
                        Total Liability: <span style={{ color: 'var(--warning)' }}>${grandTotalLiab.toFixed(0)}</span>
                      </div>
                      {failedPlacementCount > 0 && (
                        <div style={{ fontSize: 14, fontWeight: 700 }}>
                          Failed: <span style={{ color: 'var(--danger)' }}>{failedPlacementCount}</span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
