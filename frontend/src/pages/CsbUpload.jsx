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
  Clock,
  Search,
  StopCircle,
  Zap,
  RefreshCw,
} from 'lucide-react';

const CSB_SPORTS = [
  { label: 'AFL', sport: 'AFL Football', competition: 'AFL' },
  { label: 'NRL', sport: 'Rugby League', competition: 'NRL' },
  { label: 'NBA', sport: 'Basketball', competition: 'NBA' },
];

function parseCsvLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
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
      for (let j = 7; j < cols.length; j++) {
        const v = parseFloat(cols[j]);
        if (!isNaN(v) && v > 0) legOdds.push(v);
      }
      bets.push({
        bet_type: (cols[0] || 'SGM').trim(),
        game_id: (cols[1] || '').trim(),
        bet: (cols[2] || '').trim(),
        odds: parseFloat(cols[3]) || 0,
        min_odds: parseFloat(cols[4]) || 0,
        ev_pct: parseFloat((cols[5] || '').replace('%', '')) || 0,
        units: parseFloat(cols[6]) || 1,
        leg_odds: legOdds,
        teams: '',
      });
    } else {
      const legOdds = [];
      for (let j = 7; j < cols.length; j++) {
        const v = parseFloat(cols[j]);
        if (!isNaN(v) && v > 0) legOdds.push(v);
      }
      bets.push({
        bet_type: (cols[0] || 'Multi').trim(),
        game_id: '',
        bet: (cols[1] || '').trim(),
        odds: parseFloat(cols[2]) || 0,
        min_odds: parseFloat(cols[3]) || 0,
        ev_pct: parseFloat((cols[4] || '').replace('%', '')) || 0,
        units: parseFloat(cols[5]) || 1,
        leg_odds: legOdds,
        teams: (cols[6] || '').trim(),
      });
    }
  }
  return bets;
}

function humanRoundDown(amount) {
  if (amount < 1) return 1;
  if (amount < 10) return Math.floor(amount);
  if (amount < 100) return Math.floor(amount / 5) * 5;
  return Math.floor(amount / 10) * 10;
}

function randomDelay(min = 2000, max = 6000) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function StatusIcon({ status }) {
  if (status === 'placed') return <CheckCircle size={16} style={{ color: 'var(--success)' }} />;
  if (status === 'resolved') return <CheckCircle size={16} style={{ color: 'var(--primary)' }} />;
  if (status === 'below_min') return <AlertTriangle size={16} style={{ color: 'var(--warning)' }} />;
  if (status === 'failed') return <XCircle size={16} style={{ color: 'var(--danger)' }} />;
  if (status === 'placing') return <Loader2 size={16} style={{ color: 'var(--primary)' }} className="animate-spin" />;
  if (status === 'resolving') return <Loader2 size={16} style={{ color: 'var(--text-secondary)' }} className="animate-spin" />;
  return null;
}

function statusRowBg(status) {
  if (status === 'placed') return 'var(--success-muted)';
  if (status === 'resolved') return 'var(--accent-muted)';
  if (status === 'below_min') return 'var(--warning-muted)';
  if (status === 'failed') return 'var(--danger-muted)';
  if (status === 'placing') return 'var(--accent-muted)';
  return 'transparent';
}

export default function CsbUpload() {
  const { sessions } = useSessions();

  // CSV input
  const [csvText, setCsvText] = useState('');
  const [parsedBets, setParsedBets] = useState([]);
  const [error, setError] = useState('');

  // Config
  const [sport, setSport] = useState(CSB_SPORTS[0]);
  const [unitSize, setUnitSize] = useState(() => localStorage.getItem('csb_unitSize') || '10');
  const [stakingMode, setStakingMode] = useState(() => localStorage.getItem('csb_stakingMode') || 'units');
  const [fixedStake, setFixedStake] = useState(() => localStorage.getItem('csb_fixedStake') || '10');
  const [maxLiability, setMaxLiability] = useState(() => localStorage.getItem('csb_maxLiability') || '500');

  // Account queue
  const [accountQueue, setAccountQueue] = useState([]);
  const [accountBalances, setAccountBalances] = useState({});

  // Warmup state
  const [warmupStatus, setWarmupStatus] = useState(null); // null | 'warming' | {results: [...]}

  // Selection state (checkboxes per bet)
  const [selectedBets, setSelectedBets] = useState({}); // {index: true/false}

  // Liability split config (persisted)
  const [liabilitySplitEnabled, setLiabilitySplitEnabled] = useState(() => localStorage.getItem('csb_liabSplit') !== 'false');
  const [liabilityCap, setLiabilityCap] = useState(() => localStorage.getItem('csb_liabCap') || '600');

  // Odds drift staking (persisted)
  const [oddsDriftEnabled, setOddsDriftEnabled] = useState(() => localStorage.getItem('csb_oddsDrift') !== 'false');


  // Persist settings to localStorage
  useEffect(() => { localStorage.setItem('csb_unitSize', unitSize); }, [unitSize]);
  useEffect(() => { localStorage.setItem('csb_stakingMode', stakingMode); }, [stakingMode]);
  useEffect(() => { localStorage.setItem('csb_fixedStake', fixedStake); }, [fixedStake]);
  useEffect(() => { localStorage.setItem('csb_maxLiability', maxLiability); }, [maxLiability]);
  useEffect(() => { localStorage.setItem('csb_liabSplit', liabilitySplitEnabled); }, [liabilitySplitEnabled]);
  useEffect(() => { localStorage.setItem('csb_liabCap', liabilityCap); }, [liabilityCap]);
  useEffect(() => { localStorage.setItem('csb_oddsDrift', oddsDriftEnabled); }, [oddsDriftEnabled]);

  // Placement state
  const [placing, setPlacing] = useState(false);
  const [hasPlaced, setHasPlaced] = useState(false); // Prevents double-placing via V1+V2
  const [v2Log, setV2Log] = useState([]);
  const [resolving, setResolving] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [betStatuses, setBetStatuses] = useState({});
  const [currentBetIndex, setCurrentBetIndex] = useState(-1);
  const [currentAccountIdx, setCurrentAccountIdx] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const abortRef = useRef(false);

  const sessionEntries = Object.entries(sessions).filter(([, s]) => s.session_id);

  // Init account queue from sessions
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
      } catch {
        balances[id] = null;
      }
    }
    setAccountBalances(balances);
  }, [sessions]);

  useEffect(() => {
    if (sessionEntries.length > 0) fetchBalances();
  }, [sessionEntries.length]);

  // Countdown timer
  useEffect(() => {
    if (countdown <= 0) return;
    const t = setInterval(() => setCountdown((c) => Math.max(0, c - 100)), 100);
    return () => clearInterval(t);
  }, [countdown > 0]);

  const enabledAccounts = accountQueue.filter((a) => a.enabled);

  // ─── Liability Split (JS-side) ───
  const splitBetsByLiability = (bets) => {
    const cap = parseFloat(liabilityCap) || 600;
    const increment = 10;
    const numAccounts = enabledAccounts.length;
    if (numAccounts < 1 || !liabilitySplitEnabled) return bets.map((b, i) => ({ ...b, _origIdx: b._preservedIdx ?? i, _splitOf: 1, _splitN: 0 }));

    const result = [];
    for (let i = 0; i < bets.length; i++) {
      const bet = bets[i];
      // Use resolved combined odds for drift adjustment if available
      const resolvedOdds = betStatuses[bet._preservedIdx ?? i]?.combined_odds
        ? parseFloat(betStatuses[bet._preservedIdx ?? i].combined_odds)
        : null;
      const stake = getStakeForBet(bet, resolvedOdds);
      const odds = resolvedOdds || bet.odds || 2;
      const liability = (odds - 1) * stake;

      const origIdx = bet._preservedIdx ?? i;

      if (liability <= cap || odds <= 1) {
        result.push({ ...bet, _origIdx: origIdx, _splitOf: 1, _splitN: 0 });
        continue;
      }

      // Need to split
      const maxStakePerBet = Math.floor((cap / (odds - 1)) / increment) * increment;
      if (maxStakePerBet <= 0) {
        result.push({ ...bet, _origIdx: origIdx, _splitOf: 1, _splitN: 0 });
        continue;
      }

      let numSplits = Math.max(1, Math.ceil(stake / maxStakePerBet));
      numSplits = Math.min(numSplits, numAccounts);

      const base = Math.floor(stake / numSplits / increment) * increment;
      const remainderChunks = Math.round((stake - base * numSplits) / increment);

      for (let j = 0; j < numSplits; j++) {
        const splitStake = base + (j < remainderChunks ? increment : 0);
        result.push({
          ...bet,
          _origIdx: origIdx,
          _splitOf: numSplits,
          _splitN: j,
          _splitStake: splitStake,
        });
      }
    }
    return result;
  };

  const roundToTab = (amount) => {
    // Anti-detection: all stakes must be $10 increments to look like a casual punter
    if (amount < 10) return 10;
    return Math.round(amount / 10) * 10;
  };

  // Dynamic Kelly odds drift — Shadow's formula
  // Reverse-engineers true probability from min_odds, calculates Kelly fraction at both
  // tipped and actual odds, scales units by the ratio of Kelly fractions.
  const applyOddsDrift = (units, tippedOdds, actualOdds, minOdds) => {
    if (!oddsDriftEnabled || !actualOdds || !tippedOdds) return units;
    if (actualOdds >= tippedOdds) return units; // Odds same or better — full units
    if (!minOdds || minOdds <= 1) return units; // No min odds — can't calculate
    if (actualOdds <= minOdds) return 0; // Below min — no edge, skip

    // Step 1: True probability from min odds (break-even point)
    const p = 1 / minOdds;

    // Step 2: EV at tipped and actual odds
    const evTarget = (p * tippedOdds) - 1;
    const evActual = (p * actualOdds) - 1;

    if (evTarget <= 0 || evActual <= 0) return 0; // No edge

    // Step 3: Kelly fractions
    const kellyTarget = evTarget / (tippedOdds - 1);
    const kellyActual = evActual / (actualOdds - 1);

    // Step 4: Multiplier
    const multiplier = kellyActual / kellyTarget;

    return Math.max(0, units * multiplier);
  };

  const getStakeForBet = (bet, resolvedOdds) => {
    let units = bet.units || 1;

    // Apply odds drift if we have resolved odds
    if (oddsDriftEnabled && resolvedOdds && bet.odds) {
      units = applyOddsDrift(units, bet.odds, resolvedOdds, bet.min_odds || 0);
      if (units <= 0) return 0; // Skip this bet
    }

    let stake;
    if (stakingMode === 'units') stake = units * (parseFloat(unitSize) || 10);
    else if (stakingMode === 'fixed') stake = parseFloat(fixedStake) || 10;
    else if (stakingMode === 'liability') {
      const liab = parseFloat(maxLiability) || 500;
      const odds = resolvedOdds || bet.odds || 2;
      stake = humanRoundDown(liab / odds);
    } else {
      stake = 10;
    }
    return Math.max(0.5, roundToTab(stake));
  };

  const totalStake = parsedBets.reduce((sum, b) => sum + getStakeForBet(b), 0);

  // ─── Warmup ───
  const runWarmup = useCallback(async (selectedSport) => {
    const firstEnabled = enabledAccounts[0] || accountQueue.find((a) => a.enabled);
    const sid = firstEnabled ? sessions[firstEnabled.id]?.session_id : null;
    if (!sid) return;

    setWarmupStatus('warming');
    try {
      const result = await api.csbWarmup(sid, [
        { sport: selectedSport.sport, competition: selectedSport.competition },
      ]);
      setWarmupStatus(result);
    } catch (err) {
      setWarmupStatus({ error: err.message });
    }
  }, [sessions, enabledAccounts, accountQueue]);

  const handleWarmupAll = async () => {
    const firstEnabled = enabledAccounts[0] || accountQueue.find((a) => a.enabled);
    const sid = firstEnabled ? sessions[firstEnabled.id]?.session_id : null;
    if (!sid) {
      setError('Login to a TAB account first.');
      return;
    }
    setWarmupStatus('warming');
    try {
      const result = await api.csbWarmup(sid, CSB_SPORTS.map((s) => ({ sport: s.sport, competition: s.competition })));
      setWarmupStatus(result);
    } catch (err) {
      setWarmupStatus({ error: err.message });
    }
  };

  // ─── Parse ───
  const handleParse = () => {
    setError('');
    setBetStatuses({});
    const bets = detectAndParse(csvText);
    if (bets.length === 0) {
      setError('No valid bets found. Check CSV format.');
      return;
    }
    setParsedBets(bets);
    // Select all by default
    const sel = {};
    bets.forEach((_, idx) => { sel[idx] = true; });
    setSelectedBets(sel);
    // Auto-warmup the selected sport after parsing
    runWarmup(sport);
  };

  const toggleBetSelection = (idx) => {
    setSelectedBets((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  const toggleSelectAll = () => {
    const allSelected = parsedBets.every((_, i) => selectedBets[i]);
    const sel = {};
    parsedBets.forEach((_, i) => { sel[i] = !allSelected; });
    setSelectedBets(sel);
  };

  const selectedCount = Object.values(selectedBets).filter(Boolean).length;

  const handleReset = () => {
    setCsvText('');
    setParsedBets([]);
    setBetStatuses({});
    setSelectedBets({});
    setError('');
    setCurrentBetIndex(-1);
    setPlacing(false);
    setResolving(false);
    setWarmupStatus(null);
    abortRef.current = true;
  };

  // ─── Account queue ───
  const toggleAccount = (id) => {
    setAccountQueue((prev) => prev.map((a) => (a.id === id ? { ...a, enabled: !a.enabled } : a)));
  };

  const moveAccountUp = (idx) => {
    if (idx <= 0) return;
    setAccountQueue((prev) => {
      const next = [...prev];
      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
      return next;
    });
  };

  const moveAccountDown = (idx) => {
    setAccountQueue((prev) => {
      if (idx >= prev.length - 1) return prev;
      const next = [...prev];
      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
      return next;
    });
  };

  // ─── Resolve All (preview only) ───
  const handleResolveAll = async () => {
    if (enabledAccounts.length === 0) {
      setError('Enable at least one account.');
      return;
    }
    const firstAcc = enabledAccounts[0];
    const sessionId = sessions[firstAcc.id]?.session_id;
    if (!sessionId) {
      setError('No active session for first account.');
      return;
    }

    setResolving(true);
    setError('');
    abortRef.current = false;

    for (let i = 0; i < parsedBets.length; i++) {
      if (abortRef.current) break;
      const bet = parsedBets[i];
      const stake = getStakeForBet(bet);

      setBetStatuses((prev) => ({ ...prev, [i]: { status: 'resolving' } }));

      try {
        const result = await api.csbResolveOne(sessionId, {
          bet_type: bet.bet_type,
          game_id: bet.game_id,
          bet: bet.bet,
          odds: bet.odds,
          min_odds: bet.min_odds,
          ev_pct: bet.ev_pct,
          units: bet.units,
          stake: stake,
          sport: sport.sport,
          competition: sport.competition,
        });

        if (result.resolved && result.meets_minimum) {
          setBetStatuses((prev) => ({
            ...prev,
            [i]: {
              status: 'resolved',
              combined_odds: result.combined_odds,
              matched_odds: result.matched_odds,
              meets_minimum: true,
            },
          }));
        } else if (result.resolved && !result.meets_minimum) {
          setBetStatuses((prev) => ({
            ...prev,
            [i]: {
              status: 'below_min',
              combined_odds: result.combined_odds,
              matched_odds: result.matched_odds,
              error: result.error,
            },
          }));
        } else {
          setBetStatuses((prev) => ({
            ...prev,
            [i]: { status: 'failed', error: result.error || 'Resolution failed' },
          }));
        }
      } catch (err) {
        setBetStatuses((prev) => ({
          ...prev,
          [i]: { status: 'failed', error: err.message },
        }));
      }
    }

    setResolving(false);
  };

  // ─── Place All ───
  // ─── Engine v2: Shadow's anti-detection execution ───
  const handlePlaceEngine = async () => {
    if (enabledAccounts.length === 0) {
      setError('Enable at least one account.');
      return;
    }
    setPlacing(true);
    setError('');
    setV2Log([]);
    const addLog = (msg) => setV2Log(prev => [...prev, { t: new Date().toLocaleTimeString('en-AU', {hour12: false}), msg }]);
    addLog(`Starting Engine v2 with ${enabledAccounts.length} accounts, unit=$${parseFloat(unitSize)||10}...`);

    // Collect selected bets (engine handles account distribution internally per spec)
    const selectedList = parsedBets
      .map((b, i) => selectedBets[i] ? b : null)
      .filter(Boolean);

    if (selectedList.length === 0) {
      setError('No bets selected.');
      setPlacing(false);
      return;
    }

    // Get enabled account IDs — try account_number first, fall back to label/email/id
    const accountIds = enabledAccounts
      .map((a) => {
        const s = sessions[a.id];
        return s?.account_number || a.accountNumber || a.account_number || s?.accountLabel || s?.email || a.id;
      })
      .filter(Boolean);

    // Progress updates while waiting for engine
    const acctLabels = enabledAccounts.map(a => sessions[a.id]?.accountLabel || sessions[a.id]?.email?.split('@')[0] || a.id);
    addLog(`Sending ${selectedList.length} bets to ${accountIds.length} accounts (${acctLabels.join(', ')})...`);
    const progressTimers = [];
    const estPerBet = 3; // ~3s per bet per account
    const totalEst = selectedList.length * accountIds.length * estPerBet;
    let betCounter = 0;
    selectedList.forEach((bet, bi) => {
      accountIds.forEach((acctId, ai) => {
        betCounter++;
        const label = acctLabels[ai] || acctId;
        const delay = betCounter * estPerBet * 1000;
        progressTimers.push(setTimeout(() => {
          addLog(`Resolving & pricing: ${bet.bet?.substring(0, 50)} on ${label}...`);
        }, delay));
      });
    });
    progressTimers.push(setTimeout(() => addLog('Finalising placements...'), Math.max(totalEst * 1000 - 3000, 5000)));

    try {
      // Start async job (returns immediately, avoids Cloudflare 524 timeout)
      const job = await api.csbExecuteEngine(
        selectedList,
        accountIds,
        sport.sport,
        sport.competition,
        parseFloat(unitSize) || 10,
      );
      const jobId = job.job_id;
      addLog(`Engine job started (${jobId}). Polling for results...`);

      // Poll every 3s until done
      let result = null;
      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        const status = await api.csbEngineStatus(jobId);
        if (status.status === 'done') {
          result = status.result;
          break;
        } else if (status.status === 'error') {
          throw new Error(status.result?.error || 'Engine job failed');
        }
      }
      progressTimers.forEach(clearTimeout);

      addLog(`Engine returned: ${result.placed} placed, ${result.failed} failed, ${result.skipped} skipped. $${result.total_staked?.toFixed(0)} staked.`);

      // Group results by bet (runner_id / bet_description) — multiple accounts per bet
      const betGroups = {};
      for (const r of (result.results || [])) {
        const key = r.runner_id || r.bet_description || r.game_id || JSON.stringify(r);
        if (!betGroups[key]) betGroups[key] = { description: r.bet_description, game_id: r.game_id, placements: [], skips: [], failures: [] };
        if (r.success) {
          betGroups[key].placements.push(r);
        } else if (r.error === 'Skipped' || r.error?.includes('below min') || r.error?.includes('already_placed') || r.error?.includes('duplicate')) {
          betGroups[key].skips.push(r);
        } else {
          betGroups[key].failures.push(r);
        }
      }

      // Animate through each unique bet
      const betKeys = Object.keys(betGroups);
      for (let gi = 0; gi < betKeys.length; gi++) {
        const group = betGroups[betKeys[gi]];
        const betIdx = parsedBets.findIndex(
          (b) => (b.game_id && b.game_id === group.game_id && b.bet === group.description) ||
                 (!b.game_id && b.bet === group.description)
        );
        if (betIdx < 0) continue;

        setCurrentBetIndex(betIdx);

        addLog(`[${gi+1}/${betKeys.length}] ${group.description || 'bet'}`);

        if (group.placements.length > 0) {
          const totalPlaced = group.placements.reduce((s, p) => s + (p.confirmed_amount || 0), 0);
          const accts = group.placements.map(p => `${p.account_label} $${p.confirmed_amount} @${p.actual_odds}`).join(', ');
          addLog(`  ✓ PLACED $${totalPlaced} — ${accts}`);
          setBetStatuses((prev) => ({ ...prev, [betIdx]: {
            status: 'placed',
            combined_odds: group.placements[0].actual_odds,
            account: group.placements.map(p => p.account_label).join(', '),
            confirmed_amount: totalPlaced,
            placements: group.placements.map(p => ({
              account: p.account,
              accountLabel: p.account_label,
              stake: p.confirmed_amount,
              combined_odds: p.actual_odds,
              ticket_number: p.bet_id,
            })),
          }}));
        } else if (group.failures.length > 0) {
          addLog(`  ✗ FAILED — ${group.failures.map(f => f.error).join('; ')}`);
          setBetStatuses((prev) => ({ ...prev, [betIdx]: {
            status: 'failed',
            error: group.failures.map(f => `${f.account_label}: ${f.error}`).join('; '),
            account: group.failures.map(f => f.account_label).join(', '),
          }}));
        } else {
          const reasons = [...new Set(group.skips.map(s => s.error).filter(Boolean))];
          addLog(`  ⊘ SKIPPED — ${reasons.join('; ') || 'below min / odds drift'}`);
          setBetStatuses((prev) => ({ ...prev, [betIdx]: {
            status: 'failed',
            error: reasons.join('; ') || 'Skipped (odds drift / below min)',
            account: group.skips.map(s => s.account_label).filter(Boolean).join(', '),
          }}));
        }

        await new Promise(res => setTimeout(res, 500));
      }
      setCurrentBetIndex(-1);

      // Show summary with ALL errors
      const errors = (result.results || [])
        .filter((r) => !r.success && r.error && r.error !== 'Skipped')
        .map((r) => `${r.account_label || r.account}: ${r.error}`);
      const skippedNote = result.skipped > 0 ? ` (${result.skipped} duplicate-account skips)` : '';
      const summary = `Engine v2: ${result.placed} placed, ${result.failed} failed. $${result.total_staked?.toFixed(0)} staked.${skippedNote}`;
      if (errors.length > 0) {
        setError(`${summary}\n\nErrors:\n${errors.join('\n')}`);
      } else {
        setError(summary);
      }

    } catch (err) {
      progressTimers.forEach(clearTimeout);
      addLog(`Error: ${err.message}`);
      setError(`Engine error: ${err.message}`);
    } finally {
      setPlacing(false);
      setHasPlaced(true);
    }
  };

  const handlePlaceAll = async () => {
    if (enabledAccounts.length === 0) {
      setError('Enable at least one account.');
      return;
    }

    setPlacing(true);
    setError('');
    abortRef.current = false;

    const initialStatuses = {};
    parsedBets.forEach((_, i) => {
      if (selectedBets[i]) {
        initialStatuses[i] = { status: 'pending' };
      }
    });
    setBetStatuses((prev) => ({ ...prev, ...initialStatuses }));

    // Build placement queue: apply liability splitting if enabled
    // IMPORTANT: preserve original indices so we place the RIGHT bets
    const selectedList = parsedBets
      .map((b, i) => selectedBets[i] ? { ...b, _preservedIdx: i } : null)
      .filter(Boolean);
    const placementQueue = splitBetsByLiability(selectedList);

    let accIdx = 0;
    const runningBalances = {};
    for (const acc of enabledAccounts) {
      runningBalances[acc.id] = accountBalances[acc.id] || 10000;
    }

    let betsSinceBreak = 0;
    let nextBreakAt = 15 + Math.floor(Math.random() * 11); // 15-25

    for (let qi = 0; qi < placementQueue.length; qi++) {
      if (abortRef.current) break;

      const qBet = placementQueue[qi];
      const i = qBet._origIdx; // Map back to original bet index for status
      const bet = parsedBets[i];
      // Use resolved combined odds for drift adjustment if available
      const resolvedOdds = betStatuses[i]?.combined_odds ? parseFloat(betStatuses[i].combined_odds) : null;
      const rawStake = qBet._splitStake || getStakeForBet(bet, resolvedOdds);
      // Anti-detection: ensure final stake is always $10 rounded
      const stake = rawStake < 10 ? 10 : Math.round(rawStake / 10) * 10;

      // Skip bets where drift reduced stake to 0 (below min odds)
      if (stake <= 0) {
        setBetStatuses((prev) => ({ ...prev, [i]: { status: 'failed', error: 'Odds drifted below minimum — skipped' } }));
        continue;
      }

      // For split bets, assign each split to a different account (round-robin from first enabled)
      if (qBet._splitOf > 1) {
        // Reset to spread across accounts: split 0 → account 0, split 1 → account 1, etc.
        accIdx = qBet._splitN % enabledAccounts.length;
      }

      // Find account with sufficient balance
      while (accIdx < enabledAccounts.length && runningBalances[enabledAccounts[accIdx].id] < stake) {
        accIdx++;
      }

      // If no single account can cover the full stake, try balance-aware split
      if (accIdx >= enabledAccounts.length) {
        // Collect all accounts with ANY remaining balance (>$1)
        const partialAccs = enabledAccounts
          .map((a, idx) => ({ ...a, idx, bal: runningBalances[a.id] || 0 }))
          .filter((a) => a.bal >= 1)
          .sort((a, b) => b.bal - a.bal); // Highest balance first

        const totalAvail = partialAccs.reduce((s, a) => s + a.bal, 0);

        if (totalAvail >= stake && partialAccs.length >= 2) {
          // Split across accounts by available balance
          let remaining = stake;
          for (const pAcc of partialAccs) {
            if (remaining <= 0) break;
            const chunk = Math.min(remaining, Math.floor(pAcc.bal / 10) * 10); // Round down to $10
            if (chunk < 1) continue;

            const splitSessionId = sessions[pAcc.id]?.session_id;
            if (!splitSessionId) continue;

            const splitLabel = ` (bal-split $${chunk})`;
            setBetStatuses((prev) => ({
              ...prev,
              [i]: { ...(prev[i] || {}), status: 'placing', account: pAcc.id, splitLabel },
            }));

            try {
              const result = await api.csbPlaceOne(splitSessionId, {
                bet_type: bet.bet_type, game_id: bet.game_id, bet: bet.bet,
                odds: bet.odds, min_odds: bet.min_odds, ev_pct: bet.ev_pct,
                units: bet.units, stake: chunk,
                sport: sport.sport, competition: sport.competition,
              });

              if (result.success) {
                const rawBal = result.account_balance || '';
                const newBal = rawBal ? parseFloat(String(rawBal).replace(/[$,]/g, '')) : runningBalances[pAcc.id] - chunk;
                runningBalances[pAcc.id] = newBal;
                setAccountBalances((prev) => ({ ...prev, [pAcc.id]: newBal }));

                setBetStatuses((prev) => {
                  const existing = prev[i] || {};
                  const prevPlacements = existing.placements || [];
                  return {
                    ...prev,
                    [i]: {
                      status: 'placed',
                      account: pAcc.id,
                      combined_odds: result.combined_odds,
                      placements: [...prevPlacements, {
                        account: pAcc.id,
                        accountLabel: sessions[pAcc.id]?.accountLabel || sessions[pAcc.id]?.email || '',
                        ticket_number: result.ticket_number,
                        combined_odds: result.combined_odds,
                        stake: result.stake || chunk,
                      }],
                    },
                  };
                });
                remaining -= chunk;
              } else {
                setBetStatuses((prev) => ({ ...prev, [i]: { status: 'failed', account: pAcc.id, error: result.error || 'Failed' } }));
              }
            } catch (err) {
              setBetStatuses((prev) => ({ ...prev, [i]: { status: 'failed', account: pAcc.id, error: err.message } }));
            }

            if (!abortRef.current) await new Promise((r) => setTimeout(r, randomDelay()));
          }

          if (remaining <= 0) {
            // Successfully split across accounts, move to next bet
            accIdx = 0; // Reset for next bet
            continue;
          }
        }

        // Truly no balance left
        for (let j = i; j < parsedBets.length; j++) {
          if (selectedBets[j]) {
            setBetStatuses((prev) => ({
              ...prev,
              [j]: { status: 'failed', error: 'No accounts with sufficient balance' },
            }));
          }
        }
        break;
      }

      const currentAcc = enabledAccounts[accIdx];
      const sessionId = sessions[currentAcc.id]?.session_id;

      setCurrentBetIndex(i);
      setCurrentAccountIdx(accIdx);
      const splitLabel = qBet._splitOf > 1 ? ` (split ${qBet._splitN + 1}/${qBet._splitOf})` : '';
      setBetStatuses((prev) => ({
        ...prev,
        [i]: { ...(qBet._splitOf > 1 ? prev[i] : {}), status: 'placing', account: currentAcc.id, splitLabel },
      }));

      try {
        const result = await api.csbPlaceOne(sessionId, {
          bet_type: bet.bet_type,
          game_id: bet.game_id,
          bet: bet.bet,
          odds: bet.odds,
          min_odds: bet.min_odds,
          ev_pct: bet.ev_pct,
          units: bet.units,
          stake: stake,
          sport: sport.sport,
          competition: sport.competition,
        });

        if (result.success) {
          const rawBal = result.account_balance || '';
          const newBalance = rawBal
            ? parseFloat(String(rawBal).replace(/[$,]/g, ''))
            : runningBalances[currentAcc.id] - stake;
          runningBalances[currentAcc.id] = newBalance;
          setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: newBalance }));

          setBetStatuses((prev) => {
            const existing = prev[i] || {};
            const prevPlacements = existing.placements || [];
            return {
              ...prev,
              [i]: {
                status: 'placed',
                account: currentAcc.id,
                ticket_number: result.ticket_number,
                combined_odds: result.combined_odds,
                matched_odds: result.resolved?.matched_odds,
                placements: [...prevPlacements, {
                  account: currentAcc.id,
                  accountLabel: sessions[currentAcc.id]?.accountLabel || sessions[currentAcc.id]?.email || '',
                  ticket_number: result.ticket_number,
                  combined_odds: result.combined_odds,
                  stake: result.stake || stake,
                }],
              },
            };
          });
        } else {
          const errMsg = result.error || 'Unknown error';
          if (errMsg.includes('INSUFFICIENT_FUNDS') || errMsg.toLowerCase().includes('insufficient')) {
            runningBalances[currentAcc.id] = 0;
            setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: 0 }));
            accIdx++;
            qi--;
            setBetStatuses((prev) => ({ ...prev, [i]: { status: 'pending' } }));
          } else {
            setBetStatuses((prev) => ({
              ...prev,
              [i]: {
                status: 'failed',
                account: currentAcc.id,
                error: errMsg,
                combined_odds: result.resolved?.combined_odds,
                matched_odds: result.resolved?.matched_odds,
              },
            }));
          }
        }
      } catch (err) {
        const errMsg = err.message || 'Network error';
        if (errMsg.includes('INSUFFICIENT_FUNDS') || errMsg.toLowerCase().includes('insufficient')) {
          runningBalances[currentAcc.id] = 0;
          setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: 0 }));
          accIdx++;
          qi--;
        } else {
          setBetStatuses((prev) => ({
            ...prev,
            [i]: { status: 'failed', account: currentAcc.id, error: errMsg },
          }));
        }
      }

      betsSinceBreak++;

      // Human-like delays
      if (i < parsedBets.length - 1 && !abortRef.current) {
        if (betsSinceBreak >= nextBreakAt) {
          const longDelay = 60000 + Math.floor(Math.random() * 60000); // 60-120s
          setCountdown(longDelay);
          await new Promise((r) => setTimeout(r, longDelay));
          setCountdown(0);
          betsSinceBreak = 0;
          nextBreakAt = 15 + Math.floor(Math.random() * 11);
        } else {
          const delay = randomDelay();
          setCountdown(delay);
          await new Promise((r) => setTimeout(r, delay));
          setCountdown(0);
        }
      }
    }

    setPlacing(false);
    setHasPlaced(true);
    setCurrentBetIndex(-1);
  };

  // ─── Retry Failed ───
  const handleRetryFailed = async () => {
    if (enabledAccounts.length === 0) return;

    const failedIndices = Object.entries(betStatuses)
      .filter(([, s]) => s.status === 'failed')
      .map(([idx]) => parseInt(idx));

    if (failedIndices.length === 0) return;

    setRetrying(true);
    setError('');
    abortRef.current = false;

    let accIdx = 0;
    const runningBalances = {};
    for (const acc of enabledAccounts) {
      runningBalances[acc.id] = accountBalances[acc.id] || 10000;
    }

    for (const i of failedIndices) {
      if (abortRef.current) break;

      const bet = parsedBets[i];
      const stake = getStakeForBet(bet);

      while (accIdx < enabledAccounts.length && runningBalances[enabledAccounts[accIdx].id] < stake) {
        accIdx++;
      }
      if (accIdx >= enabledAccounts.length) {
        setBetStatuses((prev) => ({ ...prev, [i]: { ...prev[i], error: 'No accounts with sufficient balance' } }));
        continue;
      }

      const currentAcc = enabledAccounts[accIdx];
      const sessionId = sessions[currentAcc.id]?.session_id;

      setBetStatuses((prev) => ({ ...prev, [i]: { status: 'placing', account: currentAcc.id } }));

      try {
        const result = await api.csbPlaceOne(sessionId, {
          bet_type: bet.bet_type, game_id: bet.game_id, bet: bet.bet,
          odds: bet.odds, min_odds: bet.min_odds, ev_pct: bet.ev_pct,
          units: bet.units, stake: stake,
          sport: sport.sport, competition: sport.competition,
        });

        if (result.success) {
          const rawBal = result.account_balance || '';
          const newBal = rawBal ? parseFloat(String(rawBal).replace(/[$,]/g, '')) : runningBalances[currentAcc.id] - stake;
          runningBalances[currentAcc.id] = newBal;
          setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: newBal }));
          setBetStatuses((prev) => {
            const existing = prev[i] || {};
            const placements = existing.placements || [];
            return {
              ...prev,
              [i]: {
                status: 'placed',
                account: currentAcc.id,
                ticket_number: result.ticket_number,
                combined_odds: result.combined_odds,
                matched_odds: result.resolved?.matched_odds,
                placements: [...placements, {
                  account: currentAcc.id,
                  accountLabel: sessions[currentAcc.id]?.accountLabel || sessions[currentAcc.id]?.email || '',
                  ticket_number: result.ticket_number,
                  combined_odds: result.combined_odds,
                  matched_odds: result.resolved?.matched_odds,
                  stake: result.stake || stake,
                }],
              },
            };
          });
        } else {
          const errMsg = result.error || 'Unknown error';
          if (errMsg.includes('INSUFFICIENT_FUNDS') || errMsg.toLowerCase().includes('insufficient')) {
            runningBalances[currentAcc.id] = 0;
            setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: 0 }));
            accIdx++;
          }
          setBetStatuses((prev) => ({ ...prev, [i]: { status: 'failed', account: currentAcc.id, error: errMsg } }));
        }
      } catch (err) {
        setBetStatuses((prev) => ({ ...prev, [i]: { status: 'failed', account: currentAcc.id, error: err.message || 'Network error' } }));
      }

      if (!abortRef.current) {
        await new Promise((r) => setTimeout(r, randomDelay()));
      }
    }

    setRetrying(false);
  };

  const handleAbort = () => {
    abortRef.current = true;
  };

  const handleSyncManualBets = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = await api.syncManualBets();
      setSyncResult(result);
    } catch (err) {
      setSyncResult({ imported: 0, error: err.message });
    } finally {
      setSyncing(false);
    }
  };

  const placedCount = Object.values(betStatuses).filter((s) => s.status === 'placed').length;
  const failedCount = Object.values(betStatuses).filter((s) => s.status === 'failed').length;
  const resolvedCount = Object.values(betStatuses).filter((s) => s.status === 'resolved').length;
  const progressPct = parsedBets.length > 0 ? ((placedCount + failedCount) / parsedBets.length) * 100 : 0;

  const currentAccountLabel = () => {
    if (currentAccountIdx < 0 || currentAccountIdx >= enabledAccounts.length) return '';
    const acc = enabledAccounts[currentAccountIdx];
    const s = sessions[acc.id];
    return s?.accountLabel || s?.email || acc.id;
  };

  return (
    <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* ─── Warmup Panel (always visible) ─── */}
      <div className="card" style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>Prop Cache</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {CSB_SPORTS.map((s) => {
              const warmResult = warmupStatus?.results?.find?.((r) => r.competition === s.competition);
              const isWarming = warmupStatus === 'warming' && sport.label === s.label;
              return (
                <button
                  key={s.label}
                  onClick={() => { setSport(s); runWarmup(s); }}
                  disabled={warmupStatus === 'warming'}
                  className="btn btn-secondary"
                  style={{
                    padding: '6px 14px',
                    fontSize: 12,
                    position: 'relative',
                    borderColor: warmResult ? 'var(--success)' : undefined,
                    color: warmResult ? 'var(--success)' : undefined,
                  }}
                >
                  {isWarming && <Loader2 size={12} className="animate-spin" style={{ marginRight: 4 }} />}
                  {warmResult && <CheckCircle size={12} style={{ marginRight: 4, color: 'var(--success)' }} />}
                  {s.label}
                </button>
              );
            })}
            <button
              onClick={handleWarmupAll}
              disabled={warmupStatus === 'warming' || sessionEntries.length === 0}
              className="btn btn-primary"
              style={{ padding: '6px 14px', fontSize: 12 }}
            >
              {warmupStatus === 'warming' ? (
                <><Loader2 size={12} className="animate-spin" /> Warming...</>
              ) : (
                <><Zap size={12} /> Warm All</>
              )}
            </button>
          </div>
          {sessionEntries.length === 0 && (
            <span style={{ fontSize: 12, color: 'var(--warning)' }}>Login to a TAB account first</span>
          )}
        </div>

        {/* Warmup results */}
        {warmupStatus && warmupStatus !== 'warming' && warmupStatus.results && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
            {warmupStatus.results.map((r) => (
              <div key={r.competition} style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{r.competition}</span>
                {r.error ? (
                  <span style={{ color: 'var(--danger)', marginLeft: 6 }}>{r.error}</span>
                ) : (
                  <>
                    <span style={{ marginLeft: 6 }}>{r.matches_count} matches</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{r.props_count.toLocaleString()} props</span>
                    <span style={{ color: r.cached ? 'var(--success)' : 'var(--text-muted)', marginLeft: 4 }}>
                      {r.cached ? 'cached' : `${r.seconds}s`}
                    </span>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
        {warmupStatus?.error && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--danger)' }}>{warmupStatus.error}</div>
        )}
      </div>

      {parsedBets.length === 0 ? (
        /* ─── CSV Input ─── */
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 8 }}>
              Paste CSV data below (SGM or Multi format)
            </label>
            <textarea
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder={`SGM format:\nBet Type,Game ID,Bet,Odds,SGM Min Odds,EV %,Units,Leg 1 TAB Odds,Leg 2 TAB Odds\nSGM,20260328_NME_ESS,Harry Sheezel 30+ Disposals/Nate Caddy 15+ Disposals,5.5,3.87,41.73%,1.5,1.33,3.1\n\nMulti format:\nBet Type,Bet,Odds,Min Odds,EV %,Units,Teams,Leg 1 TAB Odds,Leg 2 TAB Odds\nMulti,Luke Davies-Uniacke 30+ Disposals/Campbell Chesser 15+ Disposals,3.89,3.262,19.10%,1.7,,2.1,1.85`}
              rows={12}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '12px 16px', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }}
            />
          </div>
          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13, whiteSpace: 'pre-wrap' }}>
              {error}
            </div>
          )}
          <button onClick={handleParse} disabled={!csvText.trim()} className="btn btn-primary" style={{ alignSelf: 'flex-start' }}>
            <ClipboardPaste size={16} />
            Parse CSV
          </button>
        </div>
      ) : (
        /* ─── Bets Management ─── */
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          {/* Summary bar */}
          <div className="card" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 24, padding: '14px 20px' }}>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Bets: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{parsedBets.length}</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              SGM: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{parsedBets.filter((b) => b.bet_type === 'SGM').length}</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Multi: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{parsedBets.filter((b) => b.bet_type === 'Multi').length}</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Total Stake: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>${totalStake.toFixed(2)}</span>
            </div>
            {placing && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                Progress: <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{placedCount + failedCount}/{parsedBets.length}</span>
                {countdown > 0 && (
                  <span style={{ color: 'var(--warning)', marginLeft: 8 }}>Next in {(countdown / 1000).toFixed(1)}s</span>
                )}
              </div>
            )}
            {!placing && (placedCount > 0 || failedCount > 0) && (
              <>
                <div style={{ fontSize: 13 }}>
                  Placed: <span style={{ color: 'var(--success)', fontWeight: 600 }}>{placedCount}</span>
                </div>
                {failedCount > 0 && (
                  <div style={{ fontSize: 13 }}>
                    Failed: <span style={{ color: 'var(--danger)', fontWeight: 600 }}>{failedCount}</span>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Config row: Sport + Staking */}
          {!placing && (
            <div className="card">
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24 }}>
                {/* Sport selector */}
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Sport</label>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {CSB_SPORTS.map((s) => (
                      <button
                        key={s.label}
                        onClick={() => { setSport(s); runWarmup(s); }}
                        className={sport.label === s.label ? 'btn btn-primary' : 'btn btn-secondary'}
                        style={{ padding: '6px 14px', fontSize: 12 }}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Staking */}
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Staking</label>
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                    {[
                      { key: 'units', label: 'By Units' },
                      { key: 'fixed', label: 'Fixed Stake' },
                      { key: 'liability', label: 'Max Liability' },
                    ].map((mode) => (
                      <button
                        key={mode.key}
                        onClick={() => setStakingMode(mode.key)}
                        className={stakingMode === mode.key ? 'btn btn-primary' : 'btn btn-secondary'}
                        style={{ padding: '6px 14px', fontSize: 12 }}
                      >
                        {mode.label}
                      </button>
                    ))}
                    {stakingMode === 'units' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Unit $</span>
                        <input
                          type="number"
                          value={unitSize}
                          onChange={(e) => setUnitSize(e.target.value)}
                          min="1"
                          className="t-input"
                          style={{ width: 72, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }}
                        />
                      </div>
                    )}
                    {stakingMode === 'fixed' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>$</span>
                        <input
                          type="number"
                          value={fixedStake}
                          onChange={(e) => setFixedStake(e.target.value)}
                          min="1"
                          className="t-input"
                          style={{ width: 72, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }}
                        />
                      </div>
                    )}
                    {stakingMode === 'liability' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Max $</span>
                        <input
                          type="number"
                          value={maxLiability}
                          onChange={(e) => setMaxLiability(e.target.value)}
                          min="10"
                          className="t-input"
                          style={{ width: 80, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }}
                        />
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>stake = max / odds</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Odds Drift */}
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Odds Drift</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                      <input type="checkbox" checked={oddsDriftEnabled}
                        onChange={() => setOddsDriftEnabled((v) => !v)} />
                      Scale stake
                    </label>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      Reduce units when TAB odds &lt; tipped odds (profit ratio)
                    </span>
                  </div>
                </div>

                {/* Liability Split */}
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Liability Split</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                      <input type="checkbox" checked={liabilitySplitEnabled}
                        onChange={() => setLiabilitySplitEnabled((v) => !v)} />
                      Auto-split
                    </label>
                    {liabilitySplitEnabled && (
                      <>
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Cap $</span>
                        <input type="number" value={liabilityCap}
                          onChange={(e) => setLiabilityCap(e.target.value)} min="100" step="100"
                          className="t-input"
                          style={{ width: 80, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }} />
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                          Split across {enabledAccounts.length} accounts, $10 increments
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Progress bar */}
          {placing && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ width: '100%', background: 'var(--bg-input)', borderRadius: 9999, height: 8, overflow: 'hidden' }}>
                <div
                  style={{
                    background: 'var(--primary)',
                    height: '100%',
                    borderRadius: 9999,
                    transition: 'width 0.3s',
                    width: `${progressPct}%`,
                  }}
                />
              </div>
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0 }}>
                Placing bet {currentBetIndex + 1}/{parsedBets.length} on{' '}
                <span style={{ color: 'var(--primary)', fontWeight: 500 }}>{currentAccountLabel()}</span>
                {accountBalances[enabledAccounts[currentAccountIdx]?.id] != null && (
                  <span style={{ color: 'var(--text-muted)' }}>
                    {' '}(${accountBalances[enabledAccounts[currentAccountIdx]?.id]?.toFixed(2)} remaining)
                  </span>
                )}
              </p>
            </div>
          )}

          {/* Account Queue */}
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
                  const isCurrentAccount = placing && currentAccountIdx < enabledAccounts.length && enabledAccounts[currentAccountIdx]?.id === acc.id;
                  return (
                    <div
                      key={acc.id}
                      className="card"
                      style={{
                        minWidth: 200,
                        opacity: acc.enabled ? 1 : 0.4,
                        outline: isCurrentAccount ? '2px solid var(--primary)' : 'none',
                        outlineOffset: -1,
                        transition: 'all 0.2s',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <input type="checkbox" checked={acc.enabled} onChange={() => toggleAccount(acc.id)} disabled={placing} style={{ borderRadius: 4 }} />
                          {acc.enabled && enabledIdx >= 0 && (
                            <span style={{
                              background: 'var(--primary)', color: '#fff', fontSize: 11, fontWeight: 700,
                              width: 20, height: 20, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                            }}>
                              {enabledIdx + 1}
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button onClick={() => moveAccountUp(idx)} disabled={placing || idx === 0} className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)', opacity: placing || idx === 0 ? 0.3 : 1 }}>
                            <ArrowUp size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                          <button onClick={() => moveAccountDown(idx)} disabled={placing || idx === accountQueue.length - 1} className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)', opacity: placing || idx === accountQueue.length - 1 ? 0.3 : 1 }}>
                            <ArrowDown size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                        </div>
                      </div>
                      <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</p>
                      {s.account_number && <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>#{s.account_number}</p>}
                      <p style={{ fontSize: 13, marginTop: 6, marginBottom: 0 }}>
                        {bal != null ? (
                          <span style={{ color: bal > 0 ? 'var(--success)' : 'var(--danger)' }}>${bal.toFixed(2)}</span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>Balance unknown</span>
                        )}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13, whiteSpace: 'pre-wrap' }}>
              {error}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            {!placing && !retrying && (
              <>
                <button onClick={handleResolveAll} disabled={resolving || enabledAccounts.length === 0} className="btn btn-secondary">
                  {resolving ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                  {resolving ? 'Resolving...' : 'Resolve All'}
                </button>
                <button onClick={handlePlaceAll} disabled={placing || hasPlaced || enabledAccounts.length === 0 || selectedCount === 0} className="btn btn-secondary" title={hasPlaced ? 'Already placed — reload page to place again' : ''}>
                  <Play size={16} />
                  {hasPlaced ? 'Placed (v1)' : selectedCount < parsedBets.length ? `Place v1 (${selectedCount})` : 'Place v1'}
                </button>
                <button onClick={handlePlaceEngine} disabled={placing || hasPlaced || enabledAccounts.length === 0 || selectedCount === 0} className="btn btn-success" title={hasPlaced ? 'Already placed — reload page to place again' : ''}>
                  <Zap size={16} />
                  {hasPlaced ? 'Placed (v2)' : selectedCount < parsedBets.length ? `Place All (${selectedCount})` : 'Place All'}
                </button>
                {failedCount > 0 && (
                  <button onClick={handleRetryFailed} disabled={retrying || enabledAccounts.length === 0} className="btn btn-primary">
                    <RotateCcw size={16} />
                    Retry Failed ({failedCount})
                  </button>
                )}
              </>
            )}
            {(placing || retrying) && (
              <button onClick={handleAbort} className="btn btn-danger">
                <StopCircle size={16} />
                {retrying ? 'Abort Retry' : 'Abort'}
              </button>
            )}
            <button onClick={handleReset} className="btn btn-secondary" disabled={placing || retrying}>
              <RotateCcw size={16} />
              Reset
            </button>
            <button onClick={handleSyncManualBets} disabled={syncing || placing || retrying} className="btn btn-secondary" style={{ marginLeft: 'auto' }}>
              {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              {syncing ? 'Syncing...' : 'Sync Manual Bets'}
            </button>
          </div>
          {syncResult && (
            <div style={{ fontSize: 12, padding: '8px 12px', borderRadius: 6, background: syncResult.imported > 0 ? 'var(--success-muted)' : 'var(--bg-card)', color: syncResult.error ? 'var(--danger)' : 'var(--text-secondary)' }}>
              {syncResult.error ? syncResult.error : syncResult.imported > 0 ? (
                <><CheckCircle size={13} style={{ color: 'var(--success)', verticalAlign: 'middle', marginRight: 4 }} />{syncResult.imported} manual bet{syncResult.imported !== 1 ? 's' : ''} imported ({syncResult.accounts_checked?.length || 0} accounts checked)</>
              ) : `No new manual bets found (${syncResult.accounts_checked?.length || 0} accounts checked)`}
            </div>
          )}

          {/* V2 Engine Log */}
          {v2Log.length > 0 && (
            <div style={{ padding: '10px 14px', borderRadius: 6, background: 'var(--bg-input)', border: '1px solid var(--border)', fontSize: 11, fontFamily: 'var(--font-mono)', maxHeight: 220, overflowY: 'auto', lineHeight: 1.8, marginBottom: 8 }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4, fontSize: 12 }}>Engine v2 Log</div>
              {v2Log.map((l, i) => (
                <div key={i} style={{ color: l.msg.includes('\u2713') ? 'var(--success)' : l.msg.includes('\u2717') ? 'var(--danger)' : l.msg.includes('\u2298') ? 'var(--warning)' : 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--text-dim)', marginRight: 6 }}>{l.t}</span>
                  {l.msg}
                </div>
              ))}
              {placing && <div style={{ color: 'var(--primary)' }}><Loader2 size={11} className="animate-spin" style={{ display: 'inline', verticalAlign: -1, marginRight: 4 }} />Waiting for engine response...</div>}
            </div>
          )}

          {/* Bets Table */}
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input type="checkbox" checked={selectedCount === parsedBets.length && parsedBets.length > 0}
                      onChange={toggleSelectAll} disabled={placing || retrying} style={{ borderRadius: 4 }} />
                  </th>
                  <th style={{ width: 32 }}>#</th>
                  <th>Type</th>
                  <th>Game</th>
                  <th>Legs</th>
                  <th style={{ textAlign: 'right' }}>CSV Odds</th>
                  <th style={{ textAlign: 'right' }}>Min Odds</th>
                  <th style={{ textAlign: 'right' }}>EV%</th>
                  <th style={{ textAlign: 'right' }}>Units</th>
                  <th style={{ textAlign: 'right' }}>Stake</th>
                  {oddsDriftEnabled && <th style={{ textAlign: 'right' }}>Adj.</th>}
                  <th style={{ textAlign: 'right' }}>Liability</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {parsedBets.map((bet, i) => {
                  const st = betStatuses[i];
                  const legs = bet.bet.split('/').map((l) => l.trim());
                  const resolvedOdds = st?.combined_odds ? parseFloat(st.combined_odds) : null;
                  const baseStake = getStakeForBet(bet);
                  const adjStake = resolvedOdds ? getStakeForBet(bet, resolvedOdds) : baseStake;
                  const stake = adjStake > 0 ? adjStake : baseStake;
                  const driftApplied = oddsDriftEnabled && resolvedOdds && resolvedOdds < bet.odds;
                  return (
                    <tr key={i} style={{ background: st ? statusRowBg(st.status) : undefined, opacity: selectedBets[i] === false ? 0.4 : 1 }}>
                      <td>
                        <input type="checkbox" checked={!!selectedBets[i]}
                          onChange={() => toggleBetSelection(i)} disabled={placing || retrying}
                          style={{ borderRadius: 4 }} />
                      </td>
                      <td style={{ color: 'var(--text-muted)' }}>{i + 1}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span className={`badge ${bet.bet_type === 'SGM' ? 'badge-purple' : 'badge-accent'}`}>
                          {bet.bet_type}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-secondary)', fontSize: 12, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {bet.game_id || bet.teams || '-'}
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          {legs.map((leg, j) => (
                            <div key={j} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span style={{ color: 'var(--text-primary)', fontSize: 12 }}>{leg}</span>
                              {bet.leg_odds[j] && (
                                <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>@{bet.leg_odds[j].toFixed(2)}</span>
                              )}
                              {st?.matched_odds?.[j] != null && (
                                <span style={{
                                  fontSize: 12,
                                  fontWeight: 500,
                                  color: st.matched_odds[j] >= (bet.leg_odds[j] || 0) ? 'var(--success)' : 'var(--danger)',
                                }}>
                                  TAB: {st.matched_odds[j].toFixed(2)}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        {bet.odds.toFixed(2)}
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                        {bet.min_odds.toFixed(2)}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <span style={{
                          color: bet.ev_pct >= 10 ? 'var(--success)' : bet.ev_pct >= 5 ? 'var(--warning)' : 'var(--text-secondary)',
                          fontWeight: bet.ev_pct >= 10 ? 600 : 400,
                        }}>
                          {bet.ev_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                        {bet.units}
                      </td>
                      <td style={{ textAlign: 'right', color: driftApplied ? 'var(--warning)' : 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        ${stake.toFixed(2)}
                        {driftApplied && <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 2 }}>↓</span>}
                      </td>
                      {oddsDriftEnabled && (
                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap', fontSize: 11 }}>
                          {driftApplied ? (
                            <span style={{ color: 'var(--warning)' }}>
                              {((resolvedOdds - 1) / (bet.odds - 1) * 100).toFixed(0)}%
                            </span>
                          ) : resolvedOdds ? (
                            <span style={{ color: 'var(--success)', fontSize: 11 }}>100%</span>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>
                      )}
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {(() => {
                          const liab = (bet.odds - 1) * stake;
                          const cap = parseFloat(liabilityCap) || 600;
                          const over = liabilitySplitEnabled && liab > cap;
                          return (
                            <span style={{ color: over ? 'var(--danger)' : liab > cap * 0.8 ? 'var(--warning)' : 'var(--text-muted)', fontWeight: over ? 600 : 400, fontSize: 12 }}>
                              ${liab.toFixed(0)}
                              {over && <span style={{ fontSize: 10, marginLeft: 2 }}>⚡</span>}
                            </span>
                          );
                        })()}
                      </td>
                      <td>
                        {st?.status === 'placed' && st?.placements?.length > 0 ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            {st.placements.map((p, pi) => {
                              const label = (p.accountLabel || sessions[p.account]?.accountLabel || sessions[p.account]?.email || '').split('@')[0].substring(0, 3).toUpperCase();
                              return (
                                <div key={pi} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                                  <StatusIcon status="placed" />
                                  <span style={{ color: 'var(--success)', fontWeight: 600 }}>{label}</span>
                                  <span style={{ color: 'var(--text-primary)' }}>${parseFloat(p.stake || 0).toFixed(0)}</span>
                                  <span style={{ color: 'var(--text-muted)' }}>@{p.combined_odds || '?'}</span>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <StatusIcon status={st?.status} />
                            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                              {st?.status === 'resolved' && `OK @${st.combined_odds || '?'}`}
                              {st?.status === 'below_min' && `Below min @${st.combined_odds || '?'}`}
                              {st?.status === 'failed' && (st?.error || 'Failed')}
                              {st?.status === 'placing' && 'Placing...'}
                              {st?.status === 'resolving' && 'Resolving...'}
                              {st?.status === 'pending' && 'Pending'}
                              {!st && '-'}
                            </span>
                          </div>
                        )}
                        {st?.status !== 'placed' && st?.account && (
                          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                            {sessions[st.account]?.accountLabel || sessions[st.account]?.email || ''}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
