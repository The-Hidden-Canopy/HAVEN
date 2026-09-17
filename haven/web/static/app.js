/* HAVEN frontend — plain ES module, no dependencies.
   Flow: GET /api/state → render, then EventSource("/events") for
   `state` (full re-render) and `glow` (glow-only) events. */

const $ = (sel) => document.querySelector(sel);

const els = {
  pill: $('#state-pill'),
  time: $('#local-time'),
  center: $('.center'),
  centerMeta: $('#center-meta'),
  rooms: $('#rooms'),
  navItems: document.querySelectorAll('.rail-nav li'),
  activityList: $('#activity-list'),
  activityCount: $('#activity-count'),
  memoryList: $('#memory-list'),
  memoryCount: $('#memory-count'),
  placeholderNote: $('#placeholder-note'),
  focusLabel: $('#focus-label'),
  contextList: $('#context-list'),
  conversation: $('#conversation'),
  pendingSlot: $('#pending-slot'),
  chatForm: $('#chat-form'),
  chatInput: $('#chat-input'),
  coreName: $('#core-name'),
  statusDevices: $('#status-devices'),
  statusPeople: $('#status-people'),
  statusLine: $('#status-line'),
  devButtons: document.querySelectorAll('[data-preview]'),
  demoReset: $('#demo-reset'),
  demoCamDown: $('#demo-cam-down'),
  demoCamUp: $('#demo-cam-up'),
};

const app = {
  data: null,
  focus: null,      // room id or null
  view: 'world',    // center view: world | activity | memory | placeholder
  glowTarget: null, // authoritative glow target room id from the engine, or null
  preview: null,    // dev-override glow state, null when following server
  completedTimer: null,
};

/* ---------- helpers ---------- */

function activeGlowTarget() {
  const g = document.body.dataset.glow;
  return (g === 'acting' || g === 'completed') ? app.glowTarget : null;
}

function setGlow(glow, target) {
  document.body.dataset.glow = glow;
  app.glowTarget = target || null;
  applyGlowTargets(activeGlowTarget());
}

function scheduleCompletedFade() {
  clearTimeout(app.completedTimer);
  app.completedTimer = setTimeout(() => {
    if (document.body.dataset.glow !== 'completed') return;
    app.preview = null;
    updateDevButtons();
    setGlow('idle');
  }, 2000);
}

function applyGlowTargets(roomId) {
  for (const card of els.rooms.querySelectorAll('.room-card')) {
    if (roomId && card.dataset.roomId === roomId) {
      card.setAttribute('data-glow-target', '');
    } else {
      card.removeAttribute('data-glow-target');
    }
  }
}

async function postJSON(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body == null ? {} : body),
    });
    if (!res.ok) return null; // e.g. 404 on expired request — ignore quietly
    return await res.json();
  } catch {
    return null;
  }
}

/* ---------- rendering ---------- */

function renderState(payload) {
  app.data = payload;
  app.preview = null; // any server state event clears the dev override
  updateDevButtons();
  renderPill(payload);
  renderRooms(payload);
  renderContexts(payload);
  renderConversation(payload);
  renderPending(payload);
  renderStatus(payload);
  renderActivity(payload);
  renderMemory(payload);
  renderFocusLabel();
  setGlow(payload.glow || 'idle', payload.glow_target);
  if (payload.glow === 'completed') scheduleCompletedFade();
}

function renderPill(payload) {
  const attention =
    payload.glow === 'permission' ||
    payload.glow === 'critical' ||
    (Array.isArray(payload.pending) && payload.pending.length > 0);
  if (attention) {
    els.pill.textContent = '◉ ATTENTION';
    els.pill.className = 'pill pill-attention';
  } else {
    els.pill.textContent = '● LISTENING';
    els.pill.className = 'pill pill-idle';
  }
}

function renderRooms(payload) {
  const rooms = payload.rooms || [];
  const deviceCount = rooms.reduce((n, r) => n + ((r.devices && r.devices.length) || 0), 0);
  els.centerMeta.textContent =
    rooms.length + (rooms.length === 1 ? ' room' : ' rooms') + ' · ' +
    deviceCount + (deviceCount === 1 ? ' device' : ' devices') + ' tracked';

  els.rooms.textContent = '';
  for (const room of rooms) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'room-card' + (app.focus === room.id ? ' focused' : '');
    card.dataset.roomId = room.id;

    const name = document.createElement('div');
    name.className = 'room-name';
    name.textContent = room.name;
    card.appendChild(name);

    const sub = document.createElement('div');
    sub.className = 'room-sub';
    const parts = [];
    if (room.people && room.people.length) parts.push(room.people.join(' · '));
    const devs = room.devices || [];
    if (devs.length) {
      const on = devs.filter((d) => d.is_on).length;
      parts.push(devs.length + (devs.length === 1 ? ' device' : ' devices') + (on ? ' · ' + on + ' on' : ''));
    }
    if (!parts.length) parts.push('Empty');
    sub.textContent = parts.join(' — ');
    card.appendChild(sub);

    if (room.camera) {
      const tile = document.createElement('div');
      tile.className = 'camera-tile';
      const label = document.createElement('span');
      label.textContent = 'camera · ' + (room.camera.label || room.camera.id);
      const motion = document.createElement('span');
      motion.className = 'camera-motion' + (room.camera.motion ? '' : ' quiet');
      motion.textContent = room.camera.motion ? 'Motion' : 'No motion';
      tile.appendChild(label);
      tile.appendChild(motion);
      card.appendChild(tile);
    }

    card.addEventListener('click', () => {
      app.focus = app.focus === room.id ? null : room.id;
      renderRooms(app.data);
      renderFocusLabel();
    });

    els.rooms.appendChild(card);
  }
  applyGlowTargets(activeGlowTarget());
}

function renderFocusLabel() {
  const rooms = (app.data && app.data.rooms) || [];
  const room = rooms.find((r) => r.id === app.focus);
  els.focusLabel.textContent = 'Focused: ' + (room ? room.name : 'none');
}

function makeCtxRow(label, value) {
  const row = document.createElement('div');
  row.className = 'ctx-row';
  const l = document.createElement('span');
  l.className = 'ctx-label';
  l.textContent = label;
  const dots = document.createElement('span');
  dots.className = 'ctx-dots';
  const v = document.createElement('span');
  v.className = 'ctx-value';
  v.textContent = value;
  row.appendChild(l);
  row.appendChild(dots);
  row.appendChild(v);
  return row;
}

function renderContexts(payload) {
  els.contextList.textContent = '';
  const people = payload.people || [];
  if (people.length) {
    els.contextList.appendChild(makeCtxRow(
      'Someone home',
      people.map((p) => p.name + (p.room ? ' · ' + p.room : '')).join(', ')
    ));
  } else {
    els.contextList.appendChild(makeCtxRow('Someone home', 'No one'));
  }
  for (const ctx of payload.contexts || []) {
    els.contextList.appendChild(makeCtxRow(ctx.label, ctx.active ? 'Active' : 'Inactive'));
  }
}

function renderConversation(payload) {
  els.conversation.textContent = '';
  for (const entry of payload.conversation || []) {
    const wrap = document.createElement('div');
    wrap.className = 'convo-entry ' + (entry.from === 'user' ? 'user' : 'haven');
    const who = document.createElement('span');
    who.className = 'convo-who';
    who.textContent = entry.from === 'user' ? 'YOU' : 'HAVEN';
    const text = document.createElement('span');
    text.className = 'convo-text';
    text.textContent = entry.text;
    wrap.appendChild(who);
    wrap.appendChild(text);
    els.conversation.appendChild(wrap);
  }
  els.conversation.scrollTop = els.conversation.scrollHeight;
}

function renderPending(payload) {
  els.pendingSlot.textContent = '';
  const req = (payload.pending && payload.pending[0]) || null;
  if (!req) return;

  const card = document.createElement('div');
  card.className = 'pending-card';

  const title = document.createElement('h4');
  title.className = 'pending-title';
  title.textContent = req.title || 'Permission requested';
  card.appendChild(title);

  if (req.detail) {
    const detail = document.createElement('p');
    detail.className = 'pending-detail';
    detail.textContent = req.detail;
    card.appendChild(detail);
  }

  const actions = document.createElement('div');
  actions.className = 'pending-actions';

  const approve = document.createElement('button');
  approve.type = 'button';
  approve.className = 'btn btn-approve';
  approve.textContent = 'APPROVE';

  const deny = document.createElement('button');
  deny.type = 'button';
  deny.className = 'btn btn-deny';
  deny.textContent = 'DENY';

  const auto = document.createElement('label');
  auto.className = 'pending-auto';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  auto.appendChild(checkbox);
  auto.appendChild(document.createTextNode('Allow this automatically'));

  approve.addEventListener('click', async () => {
    const resp = await postJSON('/api/requests/' + encodeURIComponent(req.request_id) + '/approve', {
      auto: checkbox.checked,
    });
    if (resp && resp.ok && resp.state) renderState(resp.state);
  });

  deny.addEventListener('click', async () => {
    const resp = await postJSON('/api/requests/' + encodeURIComponent(req.request_id) + '/deny', {});
    if (resp && resp.ok && resp.state) renderState(resp.state);
  });

  actions.appendChild(approve);
  actions.appendChild(deny);
  card.appendChild(actions);
  card.appendChild(auto);
  els.pendingSlot.appendChild(card);
}

function renderStatus(payload) {
  const status = payload.status || {};
  els.coreName.textContent = status.core || 'Local Core';
  const devices = status.devices != null ? status.devices : 0;
  const people = status.people != null ? status.people : 0;
  els.statusDevices.textContent = devices + (devices === 1 ? ' device' : ' devices');
  els.statusPeople.textContent = people + (people === 1 ? ' person' : ' people');
  const line = status.line || 'Everything nominal';
  els.statusLine.textContent = line;
  const attention =
    payload.glow === 'permission' ||
    payload.glow === 'critical' ||
    line !== 'Everything nominal';
  els.statusLine.className = 'status-right ' + (attention ? 'attention' : 'nominal');
}

/* ---------- activity & memory feeds ---------- */

function fmtClock(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function makeFeedRow(label, text, stamp, actor) {
  const row = document.createElement('div');
  row.className = 'feed-row';
  const time = document.createElement('span');
  time.className = 'feed-time';
  time.textContent = stamp;
  const type = document.createElement('span');
  type.className = 'feed-type micro';
  type.textContent = label;
  const body = document.createElement('span');
  body.className = 'feed-text';
  body.textContent = text;
  row.appendChild(time);
  row.appendChild(type);
  row.appendChild(body);
  if (actor) {
    const dots = document.createElement('span');
    dots.className = 'ctx-dots';
    const who = document.createElement('span');
    who.className = 'feed-actor muted';
    who.textContent = actor;
    row.appendChild(dots);
    row.appendChild(who);
  }
  return row;
}

function renderActivity(payload) {
  const events = Array.isArray(payload.activity) ? payload.activity : [];
  els.activityCount.textContent =
    events.length + (events.length === 1 ? ' event' : ' events');
  els.activityList.textContent = '';
  if (!events.length) {
    const empty = document.createElement('div');
    empty.className = 'feed-empty muted';
    empty.textContent = 'No events yet.';
    els.activityList.appendChild(empty);
    return;
  }
  for (const ev of events) {
    els.activityList.appendChild(makeFeedRow(
      ev.event_type || '',
      ev.summary || '',
      fmtClock(ev.occurred_at),
      ev.actor_id || ''
    ));
  }
}

function renderMemory(payload) {
  const entries = Array.isArray(payload.memory) ? payload.memory : [];
  els.memoryCount.textContent =
    entries.length + (entries.length === 1 ? ' entry' : ' entries');
  els.memoryList.textContent = '';
  if (!entries.length) {
    const empty = document.createElement('div');
    empty.className = 'feed-empty muted';
    empty.textContent = 'Nothing remembered yet.';
    els.memoryList.appendChild(empty);
    return;
  }
  for (const entry of entries) {
    els.memoryList.appendChild(makeFeedRow(
      entry.kind || '',
      entry.content || '',
      fmtClock(entry.recorded_at),
      ''
    ));
  }
}

/* ---------- nav views ---------- */

function switchView(view, label) {
  app.view = view;
  els.center.dataset.view = view;
  if (view === 'placeholder') {
    els.placeholderNote.textContent = (label || 'This view') + ' is not in this slice.';
  }
}

/* ---------- dev overlay ---------- */

function updateDevButtons() {
  for (const btn of els.devButtons) {
    btn.classList.toggle('active', btn.dataset.preview === app.preview);
  }
}

/* ---------- events & boot ---------- */

function startClock() {
  const tick = () => {
    els.time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  tick();
  setInterval(tick, 10000);
}

function wireEvents() {
  els.chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = els.chatInput.value.trim();
    if (!text) return;
    els.chatInput.value = '';
    const resp = await postJSON('/api/chat', { text: text, focus: app.focus });
    if (resp && resp.ok && resp.state) renderState(resp.state);
  });

  for (const btn of els.devButtons) {
    btn.addEventListener('click', () => {
      app.preview = btn.dataset.preview;
      updateDevButtons();
      setGlow(app.preview);
      if (app.preview === 'completed') scheduleCompletedFade();
    });
  }

  for (const item of els.navItems) {
    item.querySelector('a').addEventListener('click', (e) => {
      e.preventDefault();
      for (const li of els.navItems) li.classList.toggle('active', li === item);
      const label = item.querySelector('.nav-label').textContent;
      switchView(item.dataset.view, label);
    });
  }

  els.demoReset.addEventListener('click', async () => {
    const resp = await postJSON('/api/demo/reset', {});
    if (resp && resp.state) renderState(resp.state);
  });

  els.demoCamDown.addEventListener('click', async () => {
    const resp = await postJSON('/api/demo/camera-down', {});
    if (resp && resp.state) renderState(resp.state);
  });

  els.demoCamUp.addEventListener('click', async () => {
    const resp = await postJSON('/api/demo/camera-up', {});
    if (resp && resp.state) renderState(resp.state);
  });
}

async function boot() {
  startClock();
  wireEvents();

  try {
    const res = await fetch('/api/state');
    if (res.ok) renderState(await res.json());
  } catch {
    // Backend may not be up yet — the first SSE `state` event will render.
  }

  const es = new EventSource('/events');

  es.addEventListener('state', (e) => {
    try {
      renderState(JSON.parse(e.data));
    } catch {
      // malformed payload — wait for the next event
    }
  });

  es.addEventListener('glow', (e) => {
    try {
      const data = JSON.parse(e.data);
      const glow = (data && typeof data === 'object') ? data.state : null;
      const target = (data && typeof data === 'object') ? (data.target || null) : null;
      if (app.preview) return; // preview override holds until a full `state` event
      setGlow(glow || 'idle', target);
      if (glow === 'completed') scheduleCompletedFade();
    } catch {
      // malformed payload — ignore
    }
  });

  // EventSource auto-reconnects; nothing to do but stay alive.
  es.addEventListener('error', () => {});
}

boot();
