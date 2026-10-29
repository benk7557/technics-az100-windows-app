// Flyout behaviour. The UI only claims what the earbuds have confirmed by read-back.
(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const MODE = { OFF: 0, ANC: 1, AMBIENT: 2 };
  const LEVEL_LABEL = { 0: 'Saved noise cancelling level', 1: 'Noise cancelling level', 2: 'Ambient level' };
  const WRITE_GAP_MS = 500;      // same throttle as the official app's slider
  const POLL_MS = 4000;          // light refresh while the flyout is open
  const FULL_REFRESH_MS = 30000; // battery and codec

  const S = {
    phase: 'loading', mode: MODE.ANC, nc: 60, amb: 60, codec: null, battery: null,
    adaptive: null, sub: null,   // null = unknown: shown disabled, never as a claimed state
    writing: false, pend: { apply: false, adaptive: false, sub: false }, lastSend: 0, dragging: false, lastFull: 0, visible: false,
    // bumped on every local change and every write start/finish: a read that began under an older epoch is stale
    // (it may have been taken before a write that has since completed) and must never be drawn over newer state
    epoch: 0,
    stale: false,   // the flyout was just reopened: what is drawn may be old until the first fresh read lands
    hotkey: null,   // label of the global shortcut, if it is registered
  };
  let api = null, sendTimer = null, pollTimer = null;
  const pending = () => S.pend.apply || S.pend.adaptive || S.pend.sub;
  const SUB_HELP = {
    0: 'Lets in all sounds around you.',
    1: 'Emphasizes voices and announcements around you. Audio stops when you switch to Ambient; press play to resume.',
  };

  const els = {
    body: document.body, link: $('#link'), seg: $('#seg'), label: $('#levelLabel'), out: $('#levelOut'),
    level: $('#level'), auto: $('#auto'), sub: $('#sub'), subHelp: $('#subHelp'),
    optOff: $('#optOff'), optNc: $('#optNc'), optAmb: $('#optAmb'), status: $('#status'), retry: $('#retry'), plot: $('#plot'),
  };

  // ---------------------------------------------------------------- rendering
  function codecText(c) {
    if (!c || !c.code) return 'Connected';
    const rate = c.rate_hz ? ` ${c.rate_hz % 1000 ? (c.rate_hz / 1000).toFixed(1) : c.rate_hz / 1000} kHz` : '';
    return `Connected, ${c.codec}${rate}`;
  }

  function activeLevel() { return S.mode === MODE.AMBIENT ? S.amb : S.nc; }

  function render() {
    els.body.dataset.phase = S.phase;
    els.body.dataset.stale = String(S.stale);
    $('#plate').setAttribute('aria-busy', String(S.phase === 'loading' || S.stale));
    const usable = S.phase === 'ready';
    // while loading the placeholders hold no text at all, so nothing unconfirmed is ever announced
    els.link.textContent = S.phase === 'loading' ? '' : S.phase === 'error' ? 'Not connected' : codecText(S.codec);

    els.seg.querySelectorAll('[role=radio]').forEach((b) => {
      const on = usable && Number(b.dataset.mode) === S.mode;   // nothing looks chosen until the earbuds answered
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
      b.disabled = !usable;
    });

    // option band: same reserved height in every state; only the content differs
    els.optOff.textContent = 'Noise cancelling and ambient sound are off.' + (S.hotkey ? ` ${S.hotkey} cycles the mode.` : '');
    els.optOff.hidden = !(usable && S.mode === MODE.OFF);
    els.optNc.hidden = !(usable && S.mode === MODE.ANC);
    els.optAmb.hidden = !(usable && S.mode === MODE.AMBIENT);
    els.auto.setAttribute('aria-checked', String(S.adaptive === true));
    els.auto.toggleAttribute('data-unknown', S.adaptive == null);
    els.auto.disabled = !usable || S.adaptive == null;
    els.sub.querySelectorAll('[role=radio]').forEach((b) => {
      const on = usable && S.sub != null && Number(b.dataset.sub) === S.sub;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
      b.disabled = !usable || S.sub == null;
    });
    els.subHelp.textContent = usable && S.sub != null ? SUB_HELP[S.sub] : '';

    const auto = usable && S.mode === MODE.ANC && S.adaptive === true;
    const v = activeLevel();
    const shown = usable ? v : 1;                              // neutral thumb while loading or after an error
    els.label.textContent = auto ? 'Noise cancelling level, automatic' : LEVEL_LABEL[S.mode];
    els.out.textContent = S.phase === 'loading' ? '' : S.phase === 'error' ? '--' : auto ? 'Auto' : String(v); // never a stale value
    els.level.value = String(shown);
    // in Auto the earbuds are not using the saved level, so the thumb is hidden and the track shows no fill
    els.level.dataset.auto = String(auto);
    els.level.style.setProperty('--fill', `${auto ? 0 : ((shown - 1) / 99) * 100}%`);
    els.level.disabled = !usable || S.stale || S.mode === MODE.OFF || auto;
    els.out.classList.toggle('off', usable && (S.mode === MODE.OFF || auto));

    els.retry.hidden = S.phase !== 'error';
    drawPlot(S.phase === 'error' ? null : S.battery);
    // a hotkey can hide the pane that held focus; keep keyboard focus somewhere sensible instead of on the body
    if (S.visible && document.activeElement === document.body) $('#plate').focus({ preventScroll: true });
  }

  function setStatus(text) { els.status.textContent = text; }

  // ---------------------------------------------------------------- battery plot (audiogram marks)
  const NS = 'http://www.w3.org/2000/svg';
  function el(name, attrs, text) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function drawPlot(b) {
    const svg = els.plot;
    svg.replaceChildren();
    const y = (v) => 38 - v * 0.32;   // 0 at the bottom rule, 100 at the top rule
    const cols = [79, 170, 261];
    for (const v of [0, 50, 100]) svg.append(el('line', { class: 'grid', x1: 34, x2: 306, y1: y(v), y2: y(v) }));
    for (const cx of cols) svg.append(el('line', { class: 'grid', x1: cx, x2: cx, y1: y(100), y2: y(0) }));
    for (const v of [0, 100]) svg.append(el('text', { class: 'axis', x: 28, y: y(v) + 4, 'text-anchor': 'end' }, String(v)));
    const data = b || { left: null, case: null, right: null };
    const names = ['left', 'case', 'right'];
    const label = { left: 'Left', case: 'Case', right: 'Right' };
    names.forEach((k, i) => {
      const cx = cols[i], v = data[k];
      if (v != null) {
        const cy = y(v);
        if (k === 'left') svg.append(el('path', { class: 'left', d: `M${cx - 5} ${cy - 5}l10 10m0 -10l-10 10` }));
        if (k === 'case') svg.append(el('rect', { class: 'case', x: cx - 5, y: cy - 5, width: 10, height: 10 }));
        if (k === 'right') svg.append(el('circle', { class: 'right', cx, cy, r: 5.5 }));
      }
      svg.append(el('text', { class: `lbl-${k}`, x: cx, y: 57, 'text-anchor': 'middle' }, `${label[k]} ${v != null ? v + '%' : '--'}`));
    });
    const say = (k) => (data[k] != null ? `${label[k]} ${data[k]} percent` : `${label[k]} unknown`);
    svg.setAttribute('aria-label', `Battery: ${names.map(say).join(', ')}`);
  }

  // ---------------------------------------------------------------- talking to Python
  async function call(name, ...args) {
    try { return await api[name](...args); }
    catch (e) { return { error: { kind: 'closed', message: 'Lost the connection to the app.' } }; }
  }

  function fail(err) {
    S.phase = 'error';
    S.stale = false;
    S.pend = { apply: false, adaptive: false, sub: false };   // an error ends any queued writes; nothing may fire after it
    render();
    setStatus(err.message || 'Something went wrong.');
  }

  async function loadFull() {
    const epoch = S.epoch;
    const res = await call('snapshot');
    if (S.phase === 'ready' && (S.epoch !== epoch || S.writing || pending() || S.dragging)) return; // stale or user moved on
    if (res.error) return fail(res.error);
    S.phase = 'ready';
    S.mode = res.mode; S.nc = res.nc; S.amb = res.amb; S.codec = res.codec; S.battery = res.battery;
    S.adaptive = res.adaptive ?? null; S.sub = res.ambient_sub ?? null;
    S.lastFull = Date.now();
    S.stale = false;
    render();
    setStatus('Up to date');
  }

  async function poll(force) {
    if ((!S.visible && force !== true) || S.phase !== 'ready' || S.writing || pending() || S.dragging) return;
    if (Date.now() - S.lastFull > FULL_REFRESH_MS) return loadFull();
    const epoch = S.epoch;
    const res = await call('poll');
    // the staleness check comes first: an old result (even an error) must not act on a newer state
    if (S.epoch !== epoch || S.writing || pending() || S.dragging) return;
    if (res.error) return fail(res.error);
    // a missing optional read means "no information", not "changed": keep what we last knew
    const ad = res.adaptive ?? S.adaptive, sub = res.ambient_sub ?? S.sub;
    if (res.mode !== S.mode || res.nc !== S.nc || res.amb !== S.amb || ad !== S.adaptive || sub !== S.sub) {
      S.mode = res.mode; S.nc = res.nc; S.amb = res.amb; S.adaptive = ad; S.sub = sub;
      render();
      setStatus('Changed on the earbuds');
    }
  }

  // ---------------------------------------------------------------- writes
  // One queue for apply / set_adaptive / set_ambient_sub: one in flight, 500 ms apart, trailing value wins per kind
  // (values are read from S at send time, so the newest choice always replaces an older one).
  function arm() {
    clearTimeout(sendTimer);
    const wait = Math.max(0, WRITE_GAP_MS - (Date.now() - S.lastSend));
    sendTimer = setTimeout(send, wait);
  }
  function scheduleSend(kind) {
    S.epoch++;
    S.pend[kind] = true;
    if (!S.writing) arm();                      // a write in flight re-arms when it finishes
  }

  async function send() {
    if (S.writing) return;
    const kind = S.pend.apply ? 'apply' : S.pend.adaptive ? 'adaptive' : S.pend.sub ? 'sub' : null;
    if (!kind) return;
    S.pend[kind] = false;
    S.epoch++;
    S.writing = true; S.lastSend = Date.now();
    setStatus('Applying');
    const res = kind === 'apply' ? await call('apply', S.mode, S.nc, S.amb)
      : kind === 'adaptive' ? await call('set_adaptive', S.adaptive === true)
      : await call('set_ambient_sub', S.sub);
    S.writing = false;
    S.epoch++;
    if (res.error) {
      if (res.error.kind === 'unconfirmed') { setStatus(res.error.message); if (pending()) arm(); return; }
      S.pend = { apply: false, adaptive: false, sub: false };
      return fail(res.error);
    }
    if (pending()) return arm();                // a newer value is waiting; do not overwrite it
    if (res.ok) { setStatus('Confirmed by the earbuds'); return; }
    if (kind === 'apply') { S.mode = res.mode; S.nc = res.nc; S.amb = res.amb; }
    else if (kind === 'adaptive') S.adaptive = res.adaptive ?? null;
    else S.sub = res.ambient_sub ?? null;
    render();
    setStatus('The earbuds report a different setting');
  }

  // ---------------------------------------------------------------- movie mode (independent of the earbud state machine)
  const MOVIE_POLL_MS = 700;
  const MOVIE_GRACE_MS = 2500;   // right after movie_set the first status may not say busy yet
  const M = { st: null, err: null, startedAt: 0, target: null, frac: 0, seq: 0, timer: null, wasBusy: false, sending: false };
  const mEls = { row: $('#movieRow'), sw: $('#movie'), status: $('#movieStatus'), bar: $('#movieBar') };

  function movieBusy() {
    return !!(M.st && M.st.busy) || (M.startedAt && Date.now() - M.startedAt < MOVIE_GRACE_MS && !M.err);
  }

  function renderMovie() {
    const st = M.st, sw = mEls.sw;
    const busy = movieBusy() || M.sending;
    const known = st && st.supported && (st.active === true || st.active === false);
    let text = '', checked = false, unknown = false, disabled = false, err = false, frac = 0;
    if (!st && !M.err) { unknown = true; disabled = true; }
    else if (!st) { unknown = true; disabled = true; text = M.err; err = true; }
    else if (st.supported === false) { disabled = true; text = 'Not set up yet: run tray\\admin\\setup_movie_helper.ps1 once'; }
    else if (busy) {
      const target = M.sending && !st.busy ? M.target : (st.target || M.target);
      checked = target === 'movie';
      const sec = Math.max(0, Math.floor(st.busy ? (st.elapsed_s || 0) : 0));
      text = `${(st.busy && st.phase) || 'Starting'}, ${sec} s`;
      const a = st.busy && st.steps > 0 ? Math.min(1, (st.step || 0) / st.steps) : 0;
      const b = st.busy && st.expected_s > 0 ? Math.min(1, (st.elapsed_s || 0) / st.expected_s) : 0;
      M.frac = Math.max(M.frac, Math.min(0.97, (a + b) / 2));   // never backwards, never claims done early
      frac = M.frac;
    } else if (st.active == null) { unknown = true; disabled = true; text = 'Movie mode state unknown'; }
    else {
      checked = st.active === true;
      const c = st.codec;
      if (st.error || M.err) { text = st.error || M.err; err = true; }
      else if (checked) text = `${c || 'SBC'}${st.delay_ms != null ? `, about ${Math.round(st.delay_ms)} ms audio delay` : ''}`;
      else text = `${c === 'LDAC' ? 'LDAC, full quality' : c || 'Music quality'}. Switching reconnects audio (about 25 s)`;
    }
    if (!busy) M.frac = 0;
    if (!busy && st && st.error && known && !err) { text = st.error; err = true; }
    sw.setAttribute('aria-checked', String(checked));
    sw.toggleAttribute('data-unknown', unknown);
    sw.disabled = disabled;
    if (busy) sw.setAttribute('aria-disabled', 'true'); else sw.removeAttribute('aria-disabled');
    mEls.status.textContent = text;
    mEls.status.toggleAttribute('data-err', err);
    mEls.status.setAttribute('aria-live', busy ? 'off' : 'polite');
    mEls.row.toggleAttribute('data-busy', busy);
    mEls.bar.firstElementChild.style.transform = `scaleX(${frac})`;
  }

  async function movieRefresh() {
    const seq = ++M.seq;
    const res = await call('movie_status');
    if (seq !== M.seq) return;   // a newer answer is already drawn
    if (res && res.error && typeof res.error === 'object') { M.st = null; M.err = res.error.message || 'Movie mode is unavailable.'; }
    else {
      M.st = res; M.err = null;
      if (res.busy) M.startedAt = 0;
      else if (M.startedAt && Date.now() - M.startedAt >= MOVIE_GRACE_MS) M.startedAt = 0;
    }
    renderMovie();
    movieSchedule();
  }

  function movieSchedule() {
    clearTimeout(M.timer); M.timer = null;
    if (S.visible && movieBusy()) M.timer = setTimeout(movieRefresh, MOVIE_POLL_MS);
  }

  async function movieToggle() {
    const st = M.st;
    if (S.stale || M.sending || movieBusy() || !st || !st.supported || st.active == null) return;
    const on = !st.active;
    M.target = on ? 'movie' : 'music'; M.frac = 0; M.sending = true; M.err = null;
    renderMovie();
    const res = await call('movie_set', on);
    M.sending = false;
    if (res && res.error) { M.err = res.error.message || 'Could not switch Movie mode.'; M.st = { ...st, error: M.err }; renderMovie(); return; }
    M.startedAt = Date.now();
    renderMovie();
    movieRefresh();
  }
  mEls.row.addEventListener('click', movieToggle);

  // ---------------------------------------------------------------- input
  function pickMode(m) {
    if (S.phase !== 'ready' || S.stale || m === S.mode) return;   // stale: the keyboard must be as inert as the mouse
    S.mode = m;
    render();
    scheduleSend('apply');
  }

  function pickSub(s) {
    if (S.phase !== 'ready' || S.stale || S.sub == null || s === S.sub) return;
    S.sub = s;
    render();
    scheduleSend('sub');
  }

  els.auto.addEventListener('click', () => {
    if (S.phase !== 'ready' || S.stale || S.adaptive == null) return;
    S.adaptive = !S.adaptive;
    render();
    scheduleSend('adaptive');
  });
  els.sub.addEventListener('click', (e) => {
    const b = e.target.closest('[role=radio]');
    if (b) pickSub(Number(b.dataset.sub));
  });
  els.sub.addEventListener('keydown', (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step || S.sub == null || S.stale) return;
    e.preventDefault();
    const next = (S.sub + step + 2) % 2;
    pickSub(next);
    els.sub.querySelector(`[data-sub="${next}"]`).focus();
  });

  els.seg.addEventListener('click', (e) => {
    const b = e.target.closest('[role=radio]');
    if (b) pickMode(Number(b.dataset.mode));
  });
  els.seg.addEventListener('keydown', (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step || S.stale || S.phase !== 'ready') return;
    e.preventDefault();
    const next = (S.mode + step + 3) % 3;
    pickMode(next);
    els.seg.querySelector(`[data-mode="${next}"]`).focus();
  });

  els.level.addEventListener('pointerdown', () => { S.dragging = true; });
  window.addEventListener('pointerup', () => { S.dragging = false; });
  els.level.addEventListener('input', () => {
    const v = Number(els.level.value);
    if (S.mode === MODE.AMBIENT) S.amb = v; else S.nc = v;
    els.out.textContent = String(v);
    els.level.style.setProperty('--fill', `${((v - 1) / 99) * 100}%`);
    scheduleSend('apply');
  });

  els.retry.addEventListener('click', () => {
    S.phase = 'loading'; render(); setStatus('Reading the earbuds'); loadFull();
  });

  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') call('hide'); });
  window.addEventListener('blur', () => setTimeout(() => { if (!document.hasFocus()) call('hide_by_blur'); }, 80));

  // ---------------------------------------------------------------- lifecycle (Python calls these)
  window.__onShow = () => {
    S.visible = true;
    clearInterval(pollTimer);
    pollTimer = setInterval(poll, POLL_MS);
    const plate = $('#plate');
    plate.classList.remove('enter'); void plate.offsetWidth; plate.classList.add('enter'); // authored entrance
    if (S.phase !== 'ready') S.phase = 'loading';
    else S.stale = true;       // what is drawn may predate a hotkey change: controls stay inert until a fresh read lands
    render(); setStatus('Reading the earbuds');
    // focus starts on the plate (no ring for mouse users; the first Tab reaches the chosen mode), or on
    // "Try again" if the earbuds did not answer
    movieRefresh();
    loadFull().then(() => {
      const target = S.phase === 'ready' ? plate : els.retry;
      if (target && !target.hidden) target.focus({ preventScroll: true });
    });
  };
  window.__onRemote = () => { poll(true); };   // a hotkey changed the mode from outside: refresh now
  window.__onHide = () => { S.visible = false; clearInterval(pollTimer); clearTimeout(M.timer); M.timer = null; };

  const boot = () => {
    api = window.pywebview.api;
    render();
    call('hotkey').then((label) => { S.hotkey = typeof label === 'string' ? label : null; render(); });
  };
  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener('pywebviewready', boot);
  render();
})();
