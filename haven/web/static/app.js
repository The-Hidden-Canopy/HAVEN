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
  roomsCount: $('#rooms-count'),
  roomNav: $('#room-nav'),
  roomDetail: $('#room-detail'),
  navItems: document.querySelectorAll('.rail-nav li'),
  activityList: $('#activity-list'),
  activityCount: $('#activity-count'),
  memoryList: $('#memory-list'),
  memoryCount: $('#memory-count'),
  peopleList: $('#people-list'),
  peopleCount: $('#people-count'),
  automationsList: $('#automations-list'),
  automationsCount: $('#automations-count'),
  systemBody: $('#system-body'),
  modelsCount: $('#models-count'),
  modelsList: $('#models-list'),
  runtimesStrip: $('#runtimes-strip'),
  jobsSection: $('#jobs-section'),
  jobsList: $('#jobs-list'),
  downloadNote: $('#download-note'),
  modelToggles: document.querySelectorAll('[data-model-panel]'),
  panelDownload: $('#panel-download'),
  catalogList: $('#catalog-list'),
  inspectForm: $('#inspect-form'),
  inspectUrl: $('#inspect-url'),
  inspectionBox: $('#inspection-box'),
  inspectInstall: $('#inspect-install'),
  panelErrorDownload: $('#panel-error-download'),
  panelLocal: $('#panel-local'),
  installLocalForm: $('#install-local-form'),
  installFolder: $('#install-folder'),
  rootsList: $('#roots-list'),
  addRootForm: $('#add-root-form'),
  rootPath: $('#root-path'),
  scanRoots: $('#scan-roots'),
  discoveredList: $('#discovered-list'),
  panelErrorLocal: $('#panel-error-local'),
  panelEndpoint: $('#panel-endpoint'),
  endpointForm: $('#endpoint-form'),
  endpointUrl: $('#endpoint-url'),
  panelErrorEndpoint: $('#panel-error-endpoint'),
  placeholderNote: $('#placeholder-note'),
  focusLabel: $('#focus-label'),
  contextList: $('#context-list'),
  conversation: $('#conversation'),
  pendingSlot: $('#pending-slot'),
  chatForm: $('#chat-form'),
  chatInput: $('#chat-input'),
  micWave: $('#mic-wave'),
  voiceWake: $('#voice-wake'),
  voiceForm: $('#voice-form'),
  voiceInput: $('#voice-input'),
  voiceStop: $('#voice-stop'),
  coreName: $('#core-name'),
  statusDevices: $('#status-devices'),
  statusPeople: $('#status-people'),
  statusLine: $('#status-line'),
  devButtons: document.querySelectorAll('[data-preview]'),
  demoReset: $('#demo-reset'),
  demoCamDown: $('#demo-cam-down'),
  demoCamUp: $('#demo-cam-up'),
  demoWake: $('#demo-wake'),
  chainPanel: $('#chain-panel'),
  chainBody: $('#chain-body'),
  chainClose: $('#chain-close'),
  chainScrim: $('#chain-scrim'),
  setupScrim: $('#setup-scrim'),
  setupWizard: $('#setup-wizard'),
  setupSteps: $('#setup-steps'),
  setupStepLabel: $('#setup-step-label'),
  setupError: $('#setup-error'),
  setupWarning: $('#setup-warning'),
  setupBody: $('#setup-body'),
  setupBack: $('#setup-back'),
  setupNext: $('#setup-next'),
};

/* Voice states that mean HAVEN is actively capturing audio. */
const VOICE_ACTIVE = new Set(['wake', 'listening', 'interpreting']);

const app = {
  data: null,
  focus: null,      // room id or null
  selectedRoom: null, // room id selected in the rooms view, or null
  view: 'world',    // center view: world | rooms | activity | memory | people | automations | system | models | placeholder
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
  renderVoice(payload);
  renderRooms(payload);
  renderRoomsView(payload);
  renderContexts(payload);
  renderConversation(payload);
  renderPending(payload);
  renderStatus(payload);
  renderActivity(payload);
  renderMemory(payload);
  renderPeople(payload);
  renderAutomations(payload);
  renderSystem(payload);
  renderFocusLabel();
  setGlow(payload.glow || 'idle', payload.glow_target);
  if (payload.glow === 'completed') scheduleCompletedFade();
  // Completed outside the wizard (e.g. another session) — fold it away.
  if (setupState.open && setupState.setup && setupObj().completed === true) {
    setSetupOpen(false);
  }
  maybeNotify(payload);
}

function renderPill(payload) {
  const attention =
    payload.glow === 'permission' ||
    payload.glow === 'critical' ||
    (Array.isArray(payload.pending) && payload.pending.length > 0);
  if (attention) {
    // amber attention outranks all voice chrome
    els.pill.textContent = '◉ ATTENTION';
    els.pill.className = 'pill pill-attention';
    return;
  }
  const voice = (payload.voice && payload.voice.state) || 'dormant';
  if (voice === 'listening') {
    els.pill.textContent = '● LISTENING';
    els.pill.className = 'pill pill-voice';
  } else if (voice === 'interpreting') {
    els.pill.textContent = '◉ INTERPRETING';
    els.pill.className = 'pill pill-voice';
  } else if (voice === 'wake') {
    els.pill.textContent = '● HAVEN';
    els.pill.className = 'pill pill-voice';
  } else {
    els.pill.textContent = '○ DORMANT';
    els.pill.className = 'pill pill-dormant';
  }
}

function renderVoice(payload) {
  const voice = payload.voice || {};
  const state = voice.state || 'dormant';

  // waveform: visible only while the backend says the mic is open
  els.micWave.hidden = !voice.mic;
  els.micWave.classList.toggle('live', state === 'listening');
  els.micWave.classList.toggle('calm', state === 'interpreting');

  // composer: Speak always; STT stand-in + Stop only while active
  els.voiceForm.hidden = !(state === 'listening' || state === 'interpreting');
  els.voiceWake.classList.toggle('active', VOICE_ACTIVE.has(state));
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
      const cam = room.camera;
      const online = cam.online !== false; // missing key = online
      const tile = document.createElement('div');
      tile.className = 'camera-tile' + (online ? '' : ' offline');
      const head = document.createElement('div');
      head.className = 'camera-head';
      const label = document.createElement('span');
      label.textContent = 'camera · ' + (cam.label || cam.id);
      head.appendChild(label);
      if (!online) {
        const badge = document.createElement('span');
        badge.className = 'camera-badge';
        badge.textContent = 'OFFLINE';
        head.appendChild(badge);
      }
      const motion = document.createElement('span');
      if (online) {
        motion.className = 'camera-motion' + (cam.motion ? '' : ' quiet');
        motion.textContent = cam.motion ? 'Motion' : 'No motion';
      } else {
        motion.className = 'camera-motion offline';
        motion.textContent = 'Offline';
      }
      tile.appendChild(head);
      tile.appendChild(motion);
      card.appendChild(tile);
    }

    card.addEventListener('click', () => {
      // World cards and the rooms view share one selection/focus model.
      app.focus = app.focus === room.id ? null : room.id;
      app.selectedRoom = app.focus;
      renderRooms(app.data);
      renderRoomsView(app.data);
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

/* ---------- rooms view: navigator chips + room detail ---------- */

function selectRoom(roomId) {
  if (app.selectedRoom === roomId) {
    app.selectedRoom = null;
    app.focus = null;
  } else {
    app.selectedRoom = roomId;
    app.focus = roomId;
  }
  renderFocusLabel();
  if (app.data) {
    renderRooms(app.data);
    renderRoomsView(app.data);
  }
}

function renderRoomsView(payload) {
  const rooms = Array.isArray(payload.rooms) ? payload.rooms : [];
  const occupied = rooms.filter((r) => Array.isArray(r.people) && r.people.length).length;
  els.roomsCount.textContent = occupied + ' / ' + rooms.length + ' occupied';

  els.roomNav.textContent = '';
  for (const room of rooms) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'room-chip' + (app.selectedRoom === room.id ? ' selected' : '');
    chip.dataset.roomId = room.id;

    const dot = document.createElement('span');
    dot.className = 'room-chip-dot' +
      (Array.isArray(room.people) && room.people.length ? ' occupied' : '');
    chip.appendChild(dot);

    const name = document.createElement('span');
    name.textContent = room.name;
    chip.appendChild(name);

    chip.addEventListener('click', () => selectRoom(room.id));
    els.roomNav.appendChild(chip);
  }

  els.roomDetail.textContent = '';
  const room = rooms.find((r) => r.id === app.selectedRoom) || null;
  if (!room) {
    app.selectedRoom = null;
    const empty = document.createElement('p');
    empty.className = 'room-detail-empty muted';
    empty.textContent = rooms.length ? 'Select a room.' : 'No rooms.';
    els.roomDetail.appendChild(empty);
    return;
  }
  els.roomDetail.appendChild(makeRoomDetail(room));
}

function makeRoomDetail(room) {
  const pane = document.createElement('div');
  pane.className = 'room-pane';

  const head = document.createElement('div');
  head.className = 'room-pane-head';
  const title = document.createElement('h2');
  title.className = 'room-pane-name';
  title.textContent = room.name;
  head.appendChild(title);
  const who = document.createElement('span');
  who.className = 'muted';
  who.textContent = (Array.isArray(room.people) && room.people.length)
    ? room.people.join(' · ')
    : 'Empty';
  head.appendChild(who);
  pane.appendChild(head);

  if (room.camera) {
    const cam = room.camera;
    const online = cam.online !== false; // missing key = online
    const line = document.createElement('p');
    line.className = 'room-pane-camera muted';
    const parts = ['camera · ' + (cam.label || cam.id)];
    if (online) parts.push(cam.motion ? 'motion' : 'no motion');
    else parts.push('offline');
    line.textContent = parts.join(' — ');
    pane.appendChild(line);
  }

  const devs = Array.isArray(room.devices) ? room.devices : [];
  const list = document.createElement('div');
  list.className = 'device-list';
  if (!devs.length) {
    const none = document.createElement('p');
    none.className = 'sys-unavailable muted';
    none.textContent = 'No devices in this room.';
    list.appendChild(none);
  }
  for (const dev of devs) list.appendChild(makeDeviceRow(dev));
  pane.appendChild(list);
  return pane;
}

/* State line per role. `is_on` proxies the cover position; devices without
   an on/off concept render "—". New provenance fields are optional — the
   backend may not emit them yet. */
function deviceStateLine(dev) {
  const role = String(dev.role || '').toLowerCase();
  if (role === 'cover') return dev.is_on ? 'Open' : 'Closed';
  if (role === 'light') {
    if (!dev.is_on) return 'Off';
    return dev.brightness_pct !== null && dev.brightness_pct !== undefined
      ? 'On · ' + dev.brightness_pct + '%'
      : 'On';
  }
  if (dev.is_on === true) return 'On';
  if (dev.is_on === false) return 'Off';
  return '—';
}

function deviceProvenanceText(dev) {
  const parts = [];
  if (dev.observed_at) {
    const ago = fmtAgo(dev.observed_at, Date.now());
    parts.push(ago ? 'observed ' + ago : 'observed ' + fmtDateTime(dev.observed_at));
  }
  if (dev.status) parts.push(String(dev.status));
  if (dev.changed_by) parts.push('by ' + String(dev.changed_by));
  if (dev.confidence !== null && dev.confidence !== undefined) {
    parts.push('confidence ' + dev.confidence);
  }
  if (dev.source) parts.push(String(dev.source));
  return parts.join(' · ');
}

function makeDeviceCommandButton(deviceId, label, body) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn';
  btn.textContent = label;
  btn.addEventListener('click', () => sendDeviceCommand(deviceId, body));
  return btn;
}

async function sendDeviceCommand(deviceId, body) {
  const resp = await postJSON(
    '/api/devices/' + encodeURIComponent(deviceId) + '/command', body);
  if (resp && resp.ok && resp.state) {
    renderState(resp.state);
  } else if (resp && resp.ok === false) {
    console.warn('device command failed', resp.error);
  }
  // null (404 / network) — quiet no-op, per postJSON
}

function makeDeviceRow(dev) {
  const row = document.createElement('div');
  row.className = 'device-row';

  const head = document.createElement('div');
  head.className = 'device-row-head';
  head.appendChild(makeCtxRow(
    (dev.role || 'device') + (dev.id ? ' · ' + dev.id : ''),
    deviceStateLine(dev)
  ));
  row.appendChild(head);

  const role = String(dev.role || '').toLowerCase();
  if (role === 'light') {
    const controls = document.createElement('div');
    controls.className = 'device-controls';
    if (dev.is_on) {
      controls.appendChild(makeDeviceCommandButton(
        dev.id, 'Turn off', { service: 'light.turn_off' }));
    }
    if (dev.brightness_pct !== null && dev.brightness_pct !== undefined) {
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '0';
      input.max = '100';
      input.value = String(dev.brightness_pct);
      input.className = 'device-brightness';
      input.setAttribute('aria-label', 'Brightness percent');
      controls.appendChild(input);
      const set = document.createElement('button');
      set.type = 'button';
      set.className = 'btn';
      set.textContent = 'Set';
      set.addEventListener('click', () => {
        const pct = Math.max(0, Math.min(100, Math.round(Number(input.value) || 0)));
        sendDeviceCommand(dev.id, { service: 'light.set_brightness', brightness_pct: pct });
      });
      controls.appendChild(set);
    }
    row.appendChild(controls);
  } else if (role === 'cover') {
    const controls = document.createElement('div');
    controls.className = 'device-controls';
    controls.appendChild(makeDeviceCommandButton(
      dev.id, 'Open', { service: 'cover.open' }));
    controls.appendChild(makeDeviceCommandButton(
      dev.id, 'Close', { service: 'cover.close' }));
    row.appendChild(controls);
  }

  const prov = deviceProvenanceText(dev);
  if (prov) {
    const line = document.createElement('p');
    line.className = 'device-prov';
    line.textContent = prov;
    row.appendChild(line);
  }
  return row;
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

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleString([], {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
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
    const row = makeFeedRow(
      ev.event_type || '',
      ev.summary || '',
      fmtClock(ev.occurred_at),
      ev.actor_id || ''
    );
    if (ACTION_EVENT_TYPES.has(ev.event_type) && ev.event_id) {
      row.classList.add('feed-row-link');
      row.setAttribute('role', 'button');
      row.setAttribute('tabindex', '0');
      row.setAttribute('aria-label', 'Show trust chain: ' + (ev.summary || ev.event_type));
      row.addEventListener('click', () => openChainPanel(ev.event_id));
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openChainPanel(ev.event_id);
        }
      });
    }
    els.activityList.appendChild(row);
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

/* ---------- trust chain drill-down ---------- */

/* Activity rows expose event ids (not action ids), so the drill-down resolves
   the row's event server-side; rows without action correlation never get the
   click affordance. */
const ACTION_EVENT_TYPES = new Set(['action_authorized', 'action_executed', 'action_blocked']);
let chainSeq = 0;

function fmtAgo(iso, fromMs) {
  const then = Date.parse(iso);
  if (isNaN(then)) return '';
  const seconds = Math.max(0, (fromMs - then) / 1000);
  if (seconds < 1.5) return 'just now';
  if (seconds < 60) return Math.round(seconds) + 's ago';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + 'm ago';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + 'h ago';
  return Math.floor(hours / 24) + 'd ago';
}

async function fetchChain(eventId) {
  try {
    const res = await fetch('/api/actions/chain?event_id=' + encodeURIComponent(eventId));
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function chainNote(text) {
  const p = document.createElement('p');
  p.className = 'chain-note muted';
  p.textContent = text;
  return p;
}

function chainText(text) {
  const p = document.createElement('p');
  p.className = 'chain-text';
  p.textContent = text;
  return p;
}

function chainSection(title) {
  const section = document.createElement('section');
  section.className = 'chain-section';
  const heading = document.createElement('h3');
  heading.className = 'micro';
  heading.textContent = title;
  const body = document.createElement('div');
  body.className = 'chain-section-body';
  section.appendChild(heading);
  section.appendChild(body);
  return { section, body };
}

/* One dotted-leader row; a missing value renders as a muted "unavailable". */
function chainRow(label, value) {
  const missing = value === null || value === undefined || value === '';
  const row = makeCtxRow(label, missing ? 'unavailable' : String(value));
  if (missing) row.querySelector('.ctx-value').classList.add('muted');
  return row;
}

function renderChain(chain) {
  const nowMs = Date.now();
  els.chainBody.textContent = '';

  const request = (chain && typeof chain.request === 'object' && chain.request) || null;
  const reqSection = chainSection('REQUEST');
  if (!request) {
    reqSection.body.appendChild(chainNote('unavailable'));
  } else {
    const requested = request.requested_at
      ? fmtDateTime(request.requested_at) + (fmtAgo(request.requested_at, nowMs) ? ' · ' + fmtAgo(request.requested_at, nowMs) : '')
      : null;
    const parameterKeys = request.parameters && typeof request.parameters === 'object'
      ? Object.keys(request.parameters) : [];
    reqSection.body.appendChild(chainRow(
      'Action',
      request.capability ? request.capability + ' (' + request.action_kind + ')' : request.action_kind
    ));
    reqSection.body.appendChild(chainRow('Target', request.target_device_id));
    reqSection.body.appendChild(chainRow(
      'Origin',
      request.origin === 'rule' ? 'rule · ' + (request.rule_id || '') : 'direct command'
    ));
    reqSection.body.appendChild(chainRow('Actor', request.actor));
    reqSection.body.appendChild(chainRow(
      'Parameters', parameterKeys.length ? JSON.stringify(request.parameters) : 'none'
    ));
    reqSection.body.appendChild(chainRow('Requested', requested));
  }
  els.chainBody.appendChild(reqSection.section);

  const interpretation = (chain && typeof chain.interpretation === 'object' && chain.interpretation) || null;
  const interpSection = chainSection('INTERPRETATION');
  if (!interpretation || (!interpretation.text && !interpretation.source_text)) {
    interpSection.body.appendChild(chainNote('unavailable'));
  } else {
    if (interpretation.text) interpSection.body.appendChild(chainText(interpretation.text));
    if (interpretation.source_text && interpretation.source_text !== interpretation.text) {
      interpSection.body.appendChild(chainNote('rule source: "' + interpretation.source_text + '"'));
    }
    const basisLabels = {
      rule_interpretation: 'the approved rule interpretation',
      rule_source: 'the rule source text',
      direct_justification: "the human's stated justification",
    };
    if (basisLabels[interpretation.basis]) {
      interpSection.body.appendChild(chainNote('Basis: ' + basisLabels[interpretation.basis]));
    }
  }
  els.chainBody.appendChild(interpSection.section);

  const evidence = Array.isArray(chain && chain.evidence) ? chain.evidence : [];
  const evSection = chainSection('EVIDENCE');
  if (!evidence.length) {
    evSection.body.appendChild(chainNote('unavailable'));
  }
  for (const item of evidence) {
    if (!item || typeof item !== 'object') continue;
    evSection.body.appendChild(chainRow(
      item.kind ? String(item.kind) : 'evidence',
      (item.subject ? String(item.subject) : '') + (item.status ? ' · ' + item.status : '')
    ));
    const parts = [];
    if (item.observed_at) {
      const ago = fmtAgo(item.observed_at, nowMs);
      parts.push(ago ? 'observed ' + ago : 'observed ' + fmtDateTime(item.observed_at));
    }
    if (item.fresh === true) parts.push('fresh');
    if (item.fresh === false) parts.push('stale');
    if (item.confidence !== null && item.confidence !== undefined) parts.push('confidence ' + item.confidence);
    if (item.note) parts.push(String(item.note));
    if (parts.length) evSection.body.appendChild(chainNote(parts.join(' · ')));
  }
  els.chainBody.appendChild(evSection.section);

  const authority = (chain && typeof chain.authority === 'object' && chain.authority) || null;
  const authSection = chainSection('AUTHORITY');
  if (!authority) {
    authSection.body.appendChild(chainNote('unavailable'));
  } else {
    authSection.body.appendChild(chainRow(
      'Decision',
      (authority.status || '') + (authority.code ? ' · ' + authority.code : '')
    ));
    authSection.body.appendChild(chainRow('Risk tier', authority.risk_tier));
    if (authority.required_role) authSection.body.appendChild(chainRow('Required role', authority.required_role));
    if (authority.risk_note) authSection.body.appendChild(chainNote(String(authority.risk_note)));
    if (authority.explanation) authSection.body.appendChild(chainText(authority.explanation));
  }
  els.chainBody.appendChild(authSection.section);

  const execution = (chain && typeof chain.execution === 'object' && chain.execution) || null;
  const execSection = chainSection('EXECUTION');
  if (!execution || !execution.attempted) {
    execSection.body.appendChild(chainNote('not executed'));
  } else {
    execSection.body.appendChild(chainRow('Service', execution.service));
    execSection.body.appendChild(chainRow('Provider', execution.provider_id));
    execSection.body.appendChild(chainRow(
      'Result',
      execution.success === true ? 'success' : execution.success === false ? 'failed' : null
    ));
    if (execution.detail) execSection.body.appendChild(chainText(String(execution.detail)));
    if (execution.executed_at) {
      const ago = fmtAgo(execution.executed_at, nowMs);
      execSection.body.appendChild(chainRow(
        'Executed',
        fmtDateTime(execution.executed_at) + (ago ? ' · ' + ago : '')
      ));
    }
  }
  els.chainBody.appendChild(execSection.section);

  const consequence = (chain && typeof chain.consequence === 'object' && chain.consequence) || null;
  const consSection = chainSection('CONSEQUENCE');
  if (!consequence || !consequence.observed) {
    consSection.body.appendChild(chainNote(
      consequence && consequence.note ? String(consequence.note) : 'not yet observed'
    ));
  } else {
    if (consequence.summary) consSection.body.appendChild(chainText(String(consequence.summary)));
    const parts = [];
    if (consequence.observed_at) {
      const ago = fmtAgo(consequence.observed_at, nowMs);
      if (ago) parts.push('observed ' + ago);
    }
    if (consequence.delay_ms !== null && consequence.delay_ms !== undefined) {
      parts.push(consequence.delay_ms + ' ms after execution');
    }
    if (parts.length) consSection.body.appendChild(chainNote(parts.join(' · ')));
  }
  els.chainBody.appendChild(consSection.section);

  const timeline = Array.isArray(chain && chain.timeline) ? chain.timeline : [];
  const tlSection = chainSection('TIMELINE');
  if (!timeline.length) {
    tlSection.body.appendChild(chainNote('unavailable'));
  }
  for (const entry of timeline) {
    if (!entry || typeof entry !== 'object') continue;
    const row = document.createElement('div');
    row.className = 'chain-event';
    const time = document.createElement('span');
    time.className = 'chain-event-time';
    time.textContent = fmtClock(entry.at);
    const type = document.createElement('span');
    type.className = 'chain-event-type micro';
    type.textContent = entry.event_type || '';
    const summary = document.createElement('span');
    summary.className = 'chain-event-summary';
    summary.textContent = entry.summary || '';
    row.appendChild(time);
    row.appendChild(type);
    row.appendChild(summary);
    tlSection.body.appendChild(row);
  }
  els.chainBody.appendChild(tlSection.section);
}

function setChainOpen(open) {
  els.chainPanel.hidden = !open;
  els.chainScrim.hidden = !open;
}

async function openChainPanel(eventId) {
  if (!eventId) return;
  const seq = ++chainSeq;
  setChainOpen(true);
  els.chainBody.textContent = '';
  els.chainBody.appendChild(chainNote('Loading trust chain…'));
  const resp = await fetchChain(eventId);
  if (seq !== chainSeq) return; // superseded by a newer open or a close
  if (!resp || resp.ok !== true || !resp.chain) {
    els.chainBody.textContent = '';
    els.chainBody.appendChild(chainNote('Trust chain unavailable for this event.'));
    return;
  }
  renderChain(resp.chain);
}

function closeChainPanel() {
  chainSeq += 1; // invalidate any in-flight fetch
  setChainOpen(false);
}


/* ---------- first-run setup wizard ---------- */
/* Owns the screen until setup.completed is true. GET /api/setup after the
   initial state fetch; every POST returns the GET-style envelope, which
   refreshes setupState. The wizard tolerates a missing backend: failed
   requests land on the error line, never an uncaught throw. No close
   affordance — onboarding finishes or the page closes. */

const SETUP_STEP_COUNT = 7;

const setupState = {
  setup: null,       // last `setup` object from the backend, or null
  configError: null, // optional quiet config-file warning
  step: 1,
  furthest: 1,       // furthest step reached; indicator clicks beyond are locked
  open: false,
};

function setupObj() {
  const s = setupState.setup;
  return (s && typeof s === 'object') ? s : {};
}

function setSetupOpen(open) {
  setupState.open = open;
  els.setupScrim.hidden = !open;
  els.setupWizard.hidden = !open;
  if (open) setupRender();
}

function showSetupError(msg) {
  els.setupError.textContent = msg;
  els.setupError.hidden = false;
}

function hideSetupError() {
  els.setupError.textContent = '';
  els.setupError.hidden = true;
}

async function fetchSetupStatus() {
  try {
    const res = await fetch('/api/setup');
    if (!res.ok) return null;
    const data = await res.json();
    return (data && typeof data === 'object' && data.setup) ? data : null;
  } catch {
    return null;
  }
}

/* Shared envelope handling for every setup POST. Returns true on ok; the
   error line carries failures and the body stays put (form values kept). */
async function setupPost(path, body) {
  const resp = await postJSON(path, body);
  if (resp === null) {
    showSetupError('Setup service unavailable.');
    return false;
  }
  if (resp.ok === false) {
    showSetupError(typeof resp.error === 'string' ? resp.error : 'Request failed.');
    return false;
  }
  if (resp.setup) setupState.setup = resp.setup;
  if (typeof resp.config_error === 'string') setupState.configError = resp.config_error;
  hideSetupError();
  setupRender();
  return true;
}

function setupGotoStep(step) {
  setupState.step = Math.max(1, Math.min(SETUP_STEP_COUNT, step));
  if (setupState.step > setupState.furthest) setupState.furthest = setupState.step;
  setupRender();
}

function renderSetupIndicator() {
  els.setupSteps.textContent = '';
  for (let i = 1; i <= SETUP_STEP_COUNT; i++) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'setup-step' + (i === setupState.step ? ' active' : '');
    btn.textContent = String(i);
    if (i > setupState.furthest) {
      btn.classList.add('locked');
      btn.disabled = true;
    } else {
      btn.addEventListener('click', () => setupGotoStep(i));
    }
    els.setupSteps.appendChild(btn);
  }
  els.setupStepLabel.textContent = 'STEP ' + setupState.step + ' / ' + SETUP_STEP_COUNT;
}

function setupText(text) {
  const p = document.createElement('p');
  p.className = 'setup-text';
  p.textContent = text;
  return p;
}

/* --- step 1: welcome --- */

function renderSetupWelcome(container) {
  container.appendChild(setupText(
    'HAVEN keeps the house\u2019s configuration on this machine \u2014 ' +
    'local first, no cloud account.'));
  container.appendChild(setupText('Seven short steps.'));
  container.appendChild(setupText(
    'Everything except Finish is skippable; each step can be revisited later from System.'));
}

/* --- step 2: data directory --- */

function renderSetupDataDir(container) {
  const dataDir = setupObj().data_dir || {};
  container.appendChild(makeCtxRow('Current folder', dataDir.resolved || '\u2014'));
  container.appendChild(makeCtxRow(
    'Source', dataDir.source === 'chosen' ? 'custom' : 'default'));

  const form = document.createElement('form');
  form.className = 'model-form setup-dir-form';
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'custom folder path\u2026';
  input.autocomplete = 'off';
  input.spellcheck = false;
  const use = document.createElement('button');
  use.type = 'submit';
  use.textContent = 'Use this folder';
  form.appendChild(input);
  form.appendChild(use);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await setupPost('/api/setup/data-dir', { path: input.value.trim() });
  });
  container.appendChild(form);

  const useDefault = document.createElement('button');
  useDefault.type = 'button';
  useDefault.className = 'btn';
  useDefault.textContent = 'Use default';
  useDefault.addEventListener('click', async () => {
    await setupPost('/api/setup/data-dir', { path: '' });
  });
  container.appendChild(useDefault);
}

/* --- step 3: provider --- */

function renderSetupProvider(container) {
  const provider = setupObj().provider || {};
  if (provider.configured === true) {
    const line = document.createElement('p');
    line.className = 'setup-note muted';
    line.textContent = 'Connected: ' + (provider.base_url || provider.kind || '\u2014');
    container.appendChild(line);
    return; // Skip became Next — the foot's Next advances
  }

  const form = document.createElement('form');
  form.className = 'model-form setup-provider-form';
  const url = document.createElement('input');
  url.type = 'text';
  url.placeholder = 'home assistant base url, e.g. http://homeassistant.local:8123';
  url.autocomplete = 'off';
  url.spellcheck = false;
  const token = document.createElement('input');
  token.type = 'password';
  token.placeholder = 'access token';
  token.autocomplete = 'off';
  token.spellcheck = false;
  const connect = document.createElement('button');
  connect.type = 'submit';
  connect.textContent = 'Test & connect';
  form.appendChild(url);
  form.appendChild(token);
  form.appendChild(connect);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await setupPost('/api/setup/provider', {
      kind: 'home_assistant',
      base_url: url.value.trim(),
      token: token.value,
    });
    token.value = ''; // never keep the token on screen
  });
  container.appendChild(form);

  const skip = document.createElement('button');
  skip.type = 'button';
  skip.className = 'btn';
  skip.textContent = 'Skip for now';
  skip.addEventListener('click', async () => {
    await setupPost('/api/setup/provider', { skip: true });
  });
  container.appendChild(skip);
}

/* --- step 4: discovery --- */

const SETUP_DEVICE_TYPES = ['light', 'thermostat', 'switch'];

function renderSetupCandidate(container, candidate, enrolledIds) {
  const card = document.createElement('div');
  card.className = 'setup-candidate';

  const parts = [String(candidate.candidate_id || 'unknown')];
  if (candidate.source) parts.push(String(candidate.source));
  if (candidate.signal_strength !== null && candidate.signal_strength !== undefined) {
    parts.push('signal ' + candidate.signal_strength);
  }
  const head = document.createElement('div');
  head.className = 'setup-candidate-head';
  const id = document.createElement('span');
  id.className = 'feed-text';
  id.textContent = parts.join(' \u00b7 ');
  head.appendChild(id);
  const dots = document.createElement('span');
  dots.className = 'ctx-dots';
  head.appendChild(dots);
  const suggestion = document.createElement('span');
  suggestion.className = 'muted';
  suggestion.textContent =
    (candidate.suggested_device_type || 'device') +
    (candidate.suggested_room ? ' \u00b7 ' + candidate.suggested_room : '');
  head.appendChild(suggestion);
  card.appendChild(head);

  if (enrolledIds.has(candidate.candidate_id)) {
    const badge = document.createElement('span');
    badge.className = 'setup-enrolled micro';
    badge.textContent = 'ENROLLED';
    head.appendChild(badge);
    container.appendChild(card);
    return;
  }

  const controls = document.createElement('div');
  controls.className = 'setup-enroll';

  const select = document.createElement('select');
  select.setAttribute('aria-label', 'Device type');
  const suggested = String(candidate.suggested_device_type || '');
  const options = SETUP_DEVICE_TYPES.includes(suggested)
    ? SETUP_DEVICE_TYPES
    : SETUP_DEVICE_TYPES.concat(suggested);
  for (const type of options) {
    const opt = document.createElement('option');
    opt.value = type;
    opt.textContent = type;
    if (type === suggested) opt.selected = true;
    select.appendChild(opt);
  }
  controls.appendChild(select);

  const room = document.createElement('input');
  room.type = 'text';
  room.placeholder = 'room';
  room.value = candidate.suggested_room ? String(candidate.suggested_room) : '';
  room.autocomplete = 'off';
  room.spellcheck = false;
  controls.appendChild(room);

  const enroll = document.createElement('button');
  enroll.type = 'button';
  enroll.className = 'btn';
  enroll.textContent = 'Enroll';
  enroll.addEventListener('click', async () => {
    await setupPost('/api/setup/enroll', {
      candidate_id: candidate.candidate_id,
      device_type: select.value,
      room: room.value.trim(),
    });
  });
  controls.appendChild(enroll);

  card.appendChild(controls);
  container.appendChild(card);
}

function renderSetupDiscovery(container) {
  const discovery = setupObj().discovery || {};
  const enrolled = Array.isArray(discovery.enrolled) ? discovery.enrolled : [];
  const enrolledIds = new Set(enrolled.map((e) => e && e.candidate_id));

  const scan = document.createElement('button');
  scan.type = 'button';
  scan.className = 'btn';
  scan.textContent = 'Scan';
  scan.addEventListener('click', async () => {
    await setupPost('/api/setup/discovery/scan', {});
  });
  container.appendChild(scan);

  const candidates = Array.isArray(discovery.candidates) ? discovery.candidates : [];
  if (!candidates.length) {
    container.appendChild(setupText('No candidates yet \u2014 run a scan.'));
    return;
  }
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue;
    renderSetupCandidate(container, candidate, enrolledIds);
  }
}

/* --- step 5: household --- */

/* setupPost re-renders the step on success, so the name input reseeds from
   here — entering several sensors for one person is the common case. */
let setupHouseholdName = '';

function setupMicroHeading(text) {
  const h = document.createElement('div');
  h.className = 'micro';
  h.textContent = text;
  return h;
}

/* Dotted-leader row (ctx-row idiom) with a trailing Remove button; the
   endpoint always takes a single id and removes the whole declaration. */
function makeSetupHouseholdRow(label, value, removePath, removeKey, removeId) {
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
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn setup-remove';
  btn.textContent = 'Remove';
  btn.addEventListener('click', async () => {
    await setupPost(removePath, { [removeKey]: removeId });
  });
  row.appendChild(l);
  row.appendChild(dots);
  row.appendChild(v);
  row.appendChild(btn);
  return row;
}

function makeSetupHouseholdInput(placeholder) {
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = placeholder;
  input.autocomplete = 'off';
  input.spellcheck = false;
  return input;
}

function renderSetupHousehold(container) {
  const household = setupObj().household || {};
  const people = Array.isArray(household.people) ? household.people : [];
  const contexts = Array.isArray(household.contexts) ? household.contexts : [];

  const peopleSection = document.createElement('div');
  peopleSection.className = 'setup-household-section';
  peopleSection.appendChild(setupMicroHeading('People'));
  peopleSection.appendChild(setupText(
    'Tell HAVEN which occupancy sensors report who. A person appears in a ' +
    'room when their sensor says they are there.'));
  peopleSection.appendChild(setupText(
    'The first person marked owner can approve automations; HAVEN acts on ' +
    "behalf of the household's people."));
  if (!people.length) {
    peopleSection.appendChild(setupText('No people declared yet.'));
  }
  for (const person of people) {
    if (!person || typeof person !== 'object') continue;
    const card = document.createElement('div');
    card.className = 'setup-person';
    const nameEl = document.createElement('div');
    nameEl.className = 'setup-person-name';
    nameEl.textContent = String(person.name || person.person_id || 'unknown');
    if (person.role === 'owner') {
      const badge = document.createElement('span');
      badge.className = 'setup-person-owner micro';
      badge.textContent = 'OWNER';
      nameEl.appendChild(badge);
    }
    card.appendChild(nameEl);
    const sources = Array.isArray(person.sources) ? person.sources : [];
    for (const source of sources) {
      if (!source || typeof source !== 'object') continue;
      card.appendChild(makeSetupHouseholdRow(
        String(source.entity_id || '\u2014'),
        String(source.room_id || '\u2014'),
        '/api/setup/household/people/remove',
        'person_id', person.person_id));
    }
    peopleSection.appendChild(card);
  }

  const personForm = document.createElement('form');
  personForm.className = 'model-form setup-provider-form';
  const personName = makeSetupHouseholdInput('name');
  personName.value = setupHouseholdName;
  const personEntity = makeSetupHouseholdInput(
    'occupancy sensor entity id, e.g. binary_sensor.gerron_office_occupancy');
  const personRoom = makeSetupHouseholdInput('room');
  const personRole = document.createElement('select');
  personRole.className = 'setup-person-role';
  for (const [value, label] of [['member', 'Member'], ['owner', 'Owner']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    personRole.appendChild(option);
  }
  const addPerson = document.createElement('button');
  addPerson.type = 'submit';
  addPerson.textContent = 'Add person';
  personForm.appendChild(personName);
  personForm.appendChild(personEntity);
  personForm.appendChild(personRoom);
  personForm.appendChild(personRole);
  personForm.appendChild(addPerson);
  personForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    setupHouseholdName = personName.value;
    await setupPost('/api/setup/household/people', {
      name: personName.value.trim(),
      entity_id: personEntity.value.trim(),
      room_id: personRoom.value.trim(),
      role: personRole.value,
    });
    personRole.value = 'member';
  });
  peopleSection.appendChild(personForm);
  container.appendChild(peopleSection);

  const contextsSection = document.createElement('div');
  contextsSection.className = 'setup-household-section';
  contextsSection.appendChild(setupMicroHeading('Contexts'));
  contextsSection.appendChild(setupText(
    'Name the household states HAVEN should track \u2014 an input_boolean ' +
    'that means working late, vacation mode, and so on.'));
  if (!contexts.length) {
    contextsSection.appendChild(setupText('No contexts declared yet.'));
  }
  for (const context of contexts) {
    if (!context || typeof context !== 'object') continue;
    contextsSection.appendChild(makeSetupHouseholdRow(
      String(context.label || context.context_id || 'unknown'),
      String(context.entity_id || '\u2014'),
      '/api/setup/household/contexts/remove',
      'context_id', context.context_id));
  }

  const contextForm = document.createElement('form');
  contextForm.className = 'model-form setup-provider-form';
  const contextLabel = makeSetupHouseholdInput('label, e.g. Working late');
  const contextEntity = makeSetupHouseholdInput(
    'entity id, e.g. input_boolean.working_late');
  const addContext = document.createElement('button');
  addContext.type = 'submit';
  addContext.textContent = 'Add context';
  contextForm.appendChild(contextLabel);
  contextForm.appendChild(contextEntity);
  contextForm.appendChild(addContext);
  contextForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    await setupPost('/api/setup/household/contexts', {
      label: contextLabel.value.trim(),
      entity_id: contextEntity.value.trim(),
    });
  });
  contextsSection.appendChild(contextForm);
  container.appendChild(contextsSection);
}

/* --- step 6: preferences --- */

function setupPrefChecked(container, key) {
  const input = container.querySelector('input[data-pref="' + key + '"]');
  return !!(input && input.checked);
}

function makeSetupPref(container, key, label, detail, checked) {
  const row = document.createElement('label');
  row.className = 'pending-auto setup-pref';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.dataset.pref = key;
  checkbox.checked = checked === true;
  checkbox.addEventListener('change', async () => {
    await setupPost('/api/setup/preferences', {
      voice: setupPrefChecked(container, 'voice'),
      intelligence: setupPrefChecked(container, 'intelligence'),
    });
  });
  row.appendChild(checkbox);
  const text = document.createElement('span');
  text.className = 'setup-pref-text';
  const name = document.createElement('span');
  name.textContent = label;
  const sub = document.createElement('span');
  sub.className = 'muted';
  sub.textContent = detail;
  text.appendChild(name);
  text.appendChild(sub);
  row.appendChild(text);
  return row;
}

function renderSetupPreferences(container) {
  const prefs = setupObj().preferences || {};
  container.appendChild(makeSetupPref(
    container, 'voice', 'Voice control',
    'Haven listens for the wake word on this machine.',
    prefs.voice === true));
  container.appendChild(makeSetupPref(
    container, 'intelligence', 'Intelligence model',
    'Allow HAVEN to use a local model for understanding.',
    prefs.intelligence === true));
}

/* --- step 7: finish --- */

function renderSetupFinish(container) {
  const s = setupObj();
  const dataDir = (s.data_dir && typeof s.data_dir === 'object') ? s.data_dir : {};
  container.appendChild(makeCtxRow(
    'Data folder',
    dataDir.source === 'chosen' ? 'custom' : 'default'));
  const provider = (s.provider && typeof s.provider === 'object') ? s.provider : {};
  container.appendChild(makeCtxRow(
    'Provider',
    provider.configured === true
      ? 'connected \u00b7 ' + (provider.base_url || '')
      : 'skipped'));
  const discovery = (s.discovery && typeof s.discovery === 'object') ? s.discovery : {};
  const enrolled = Array.isArray(discovery.enrolled) ? discovery.enrolled.length : 0;
  container.appendChild(makeCtxRow(
    'Devices enrolled', String(enrolled)));
  const household = (s.household && typeof s.household === 'object') ? s.household : {};
  const peopleCount = Array.isArray(household.people) ? household.people.length : 0;
  const contextsCount = Array.isArray(household.contexts) ? household.contexts.length : 0;
  container.appendChild(makeCtxRow(
    'Household',
    (peopleCount === 0 && contextsCount === 0)
      ? 'not declared'
      : peopleCount + ' people \u00b7 ' + contextsCount + ' contexts declared'));
  const prefs = (s.preferences && typeof s.preferences === 'object') ? s.preferences : {};
  container.appendChild(makeCtxRow('Voice control', prefs.voice === true ? 'on' : 'off'));
  container.appendChild(makeCtxRow(
    'Intelligence model', prefs.intelligence === true ? 'on' : 'off'));
}

const SETUP_STEP_RENDERERS = {
  1: renderSetupWelcome,
  2: renderSetupDataDir,
  3: renderSetupProvider,
  4: renderSetupDiscovery,
  5: renderSetupHousehold,
  6: renderSetupPreferences,
  7: renderSetupFinish,
};

function setupRender() {
  if (!setupState.open) return;
  hideSetupError();
  renderSetupIndicator();
  els.setupWarning.hidden = !setupState.configError;
  if (setupState.configError) els.setupWarning.textContent = setupState.configError;

  els.setupBody.textContent = '';
  const render = SETUP_STEP_RENDERERS[setupState.step] || renderSetupWelcome;
  render(els.setupBody);

  els.setupBack.hidden = setupState.step === 1;
  els.setupNext.textContent = setupState.step === SETUP_STEP_COUNT ? 'Finish' : 'Next';
}

async function onSetupNext() {
  if (setupState.step < SETUP_STEP_COUNT) {
    setupGotoStep(setupState.step + 1);
    return;
  }
  const done = await setupPost('/api/setup/complete', {});
  if (!done) return;
  setSetupOpen(false);
  // Setup may have registered providers — resync the app state.
  try {
    const res = await fetch('/api/state');
    if (res.ok) renderState(await res.json());
  } catch {
    // SSE will deliver the next full state on its own.
  }
}

async function refreshSetupOnBoot() {
  const resp = await fetchSetupStatus();
  if (!resp) return; // backend not there yet — no wizard until next load
  setupState.setup = resp.setup;
  setupState.configError = typeof resp.config_error === 'string'
    ? resp.config_error : null;
  if (resp.setup.completed === true) {
    setSetupOpen(false);
    return;
  }
  setupState.step = 1;
  setupState.furthest = 1;
  setSetupOpen(true);
}


/* ---------- people, automations, system views ---------- */

function renderPeople(payload) {
  const people = Array.isArray(payload.people) ? payload.people : [];
  els.peopleCount.textContent =
    people.length + (people.length === 1 ? ' person' : ' people');
  els.peopleList.textContent = '';
  if (!people.length) {
    const empty = document.createElement('div');
    empty.className = 'feed-empty muted';
    empty.textContent = 'No one home.';
    els.peopleList.appendChild(empty);
    return;
  }
  for (const person of people) {
    const present = !!person.room;
    const card = document.createElement('div');
    card.className = 'person-card';

    const name = document.createElement('div');
    name.className = 'room-name';
    name.textContent = person.name || 'Unknown';
    card.appendChild(name);

    const room = document.createElement('div');
    room.className = 'room-sub';
    room.textContent = present ? person.room : 'Away';
    card.appendChild(room);

    const status = document.createElement('span');
    status.className = 'person-status micro' + (present ? ' present' : '');
    status.textContent = present ? 'PRESENT' : 'AWAY';
    card.appendChild(status);

    els.peopleList.appendChild(card);
  }
}

const AUTO_BADGE_CLASS = {
  approved: 'approved',
  proposed: 'proposed',
  revoked: 'revoked',
};

function renderAutomations(payload) {
  const rules = Array.isArray(payload.automations) ? payload.automations : [];
  els.automationsCount.textContent =
    rules.length + (rules.length === 1 ? ' rule' : ' rules');
  els.automationsList.textContent = '';
  if (!rules.length) {
    const empty = document.createElement('div');
    empty.className = 'feed-empty muted';
    empty.textContent = 'No automations yet.';
    els.automationsList.appendChild(empty);
    return;
  }
  for (const rule of rules) {
    const row = document.createElement('div');
    row.className = 'feed-row';

    const action = document.createElement('span');
    action.className = 'feed-type micro';
    action.textContent = rule.action || '';

    const body = document.createElement('span');
    body.className = 'feed-text';
    body.textContent = rule.summary || '';

    row.appendChild(action);
    row.appendChild(body);

    if (rule.target) {
      const target = document.createElement('span');
      target.className = 'feed-target muted';
      target.textContent = rule.target;
      row.appendChild(target);
    }

    const dots = document.createElement('span');
    dots.className = 'ctx-dots';
    row.appendChild(dots);

    const status = document.createElement('span');
    status.className = 'auto-badge ' + (AUTO_BADGE_CLASS[rule.status] || '');
    status.textContent = rule.status || 'unknown';
    row.appendChild(status);

    const stamp = document.createElement('span');
    stamp.className = 'feed-stamp';
    stamp.textContent = fmtDateTime(rule.approved_at);
    row.appendChild(stamp);

    els.automationsList.appendChild(row);
  }
}

function makeSysSection(title) {
  const sec = document.createElement('section');
  sec.className = 'sys-section';
  const h = document.createElement('h3');
  h.className = 'micro';
  h.textContent = title;
  sec.appendChild(h);
  return sec;
}

function makeSysUnavailable() {
  const p = document.createElement('p');
  p.className = 'sys-unavailable muted';
  p.textContent = 'unavailable';
  return p;
}

function makeProviderRow(provider) {
  const row = document.createElement('div');
  row.className = 'feed-row';

  const kind = document.createElement('span');
  kind.className = 'feed-type micro';
  kind.textContent = provider.kind || '';

  const id = document.createElement('span');
  id.className = 'feed-text';
  id.textContent = provider.provider_id || '';

  row.appendChild(kind);
  row.appendChild(id);

  const caps = Array.isArray(provider.capabilities) ? provider.capabilities : [];
  if (caps.length) {
    const shown = caps.slice(0, 4).join(', ') +
      (caps.length > 4 ? ' +' + (caps.length - 4) : '');
    const list = document.createElement('span');
    list.className = 'muted';
    list.textContent = shown;
    row.appendChild(list);
  }

  return row;
}

function renderSystem(payload) {
  const sys = (payload.system && typeof payload.system === 'object') ? payload.system : null;
  els.systemBody.textContent = '';

  const core = makeSysSection('CORE');
  const engine = makeSysSection('ENGINE');
  const providers = makeSysSection('PROVIDERS');

  if (!sys) {
    core.appendChild(makeSysUnavailable());
    engine.appendChild(makeSysUnavailable());
    providers.appendChild(makeSysUnavailable());
  } else {
    core.appendChild(makeCtxRow('Revision', sys.revision != null ? String(sys.revision) : '—'));
    const events = sys.event_count != null ? sys.event_count : 0;
    core.appendChild(makeCtxRow('Events', String(events)));
    const memories = sys.memory_count != null ? sys.memory_count : 0;
    core.appendChild(makeCtxRow('Memories', String(memories)));

    const eng = (sys.engine && typeof sys.engine === 'object') ? sys.engine : {};
    const mins = eng.human_override_minutes;
    engine.appendChild(makeCtxRow('Human override', mins != null ? mins + ' min' : '—'));
    const conf = eng.minimum_confidence;
    engine.appendChild(makeCtxRow('Minimum evidence confidence',
      conf != null ? (Number.isInteger(conf) ? conf.toFixed(1) : String(conf)) : '—'));

    const list = Array.isArray(sys.providers) ? sys.providers : [];
    if (!list.length) {
      const none = document.createElement('p');
      none.className = 'sys-unavailable muted';
      none.textContent = 'No providers registered.';
      providers.appendChild(none);
    } else {
      for (const provider of list) providers.appendChild(makeProviderRow(provider));
    }
  }

  const setupSec = makeSysSection('SETUP');
  const setupInfo = setupObj();
  const setupDone = setupInfo.completed === true;
  setupSec.appendChild(makeCtxRow('Setup state', setupDone ? 'complete' : 'not complete'));
  const setupBtn = document.createElement('button');
  setupBtn.type = 'button';
  setupBtn.className = 'btn';
  setupBtn.textContent = setupDone ? 'Reopen setup' : 'Open setup';
  setupBtn.addEventListener('click', async () => {
    if (setupDone) {
      const ok = await setupPost('/api/setup/reopen', {});
      if (!ok) return;
      setupState.step = SETUP_STEP_COUNT;
      setupState.furthest = SETUP_STEP_COUNT;
    }
    setSetupOpen(true);
  });
  setupSec.appendChild(setupBtn);

  els.systemBody.appendChild(core);
  els.systemBody.appendChild(engine);
  els.systemBody.appendChild(providers);
  els.systemBody.appendChild(setupSec);
  els.systemBody.appendChild(makeDiagnosticsSection());
  els.systemBody.appendChild(makeBackupSection());
  els.systemBody.appendChild(makeServiceSection());
}

/* ---------- system diagnostics & backup ---------- */

/* Diagnostics + backup state lives outside the SSE payload: fetched on
   System view entry and after backup actions; both sections re-render from
   this state on every renderSystem call. Missing keys render muted '—'. */
const diagState = {
  diagnostics: null, // last GET /api/system/diagnostics payload
  probe: null,       // {tone, text} inline probe result
  probeBusy: false,
  backups: null,     // array | null (null = not fetched yet)
  backupNote: null,  // {tone, text} quiet confirmation
  backupError: null, // section-scoped error string
  busy: new Set(),   // backup ids with an in-flight action
  service: null,     // last GET /api/system/service payload (the .service object)
  serviceBusy: false,
  serviceNote: null, // {tone, text} inline install/uninstall result
};

/* postJSON swallows non-2xx bodies; the diagnostics endpoints signal
   failure with 400 + {ok:false,error}, so parse the envelope regardless
   of status. */
async function diagPost(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body == null ? {} : body),
    });
    const data = await res.json().catch(() => null);
    return { status: res.status, data: data };
  } catch {
    return null;
  }
}

function diagNumber(v) {
  return (typeof v === 'number' && Number.isFinite(v)) ? v : null;
}

/* 'N ua · M ub', '—' when either count is missing. */
function diagPair(a, ua, b, ub) {
  if (a == null || b == null) return '—';
  return a + ' ' + ua + ' · ' + b + ' ' + ub;
}

/* seconds → '42 s' | '5 min' | '2 h 10 min'; '—' when absent. */
function fmtUptime(seconds) {
  const total = diagNumber(seconds);
  if (total == null || total < 0) return '—';
  const s = Math.floor(total);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h && m) return h + ' h ' + m + ' min';
  if (h) return h + ' h';
  if (m) return m + ' min';
  return s + ' s';
}

function makeDiagNote(tone, text) {
  const p = document.createElement('p');
  p.className = 'diag-note' + (tone ? ' ' + tone : '');
  p.textContent = text;
  return p;
}

function diagRerender() {
  if (app.data) renderSystem(app.data);
}

function makeDiagnosticsSection() {
  const sec = makeSysSection('DIAGNOSTICS');
  const d = diagState.diagnostics;
  if (!d || typeof d !== 'object') {
    sec.appendChild(makeSysUnavailable());
    return sec;
  }

  const world = (d.world && typeof d.world === 'object') ? d.world : {};
  sec.appendChild(makeCtxRow('World',
    world.mode === 'home_assistant' ? 'Home Assistant' : 'Simulated household'));

  const dirRow = makeCtxRow('Data dir',
    typeof d.data_dir === 'string' && d.data_dir ? d.data_dir : '—');
  dirRow.lastElementChild.className = 'ctx-value diag-mono muted';
  sec.appendChild(dirRow);

  const provider = (d.provider && typeof d.provider === 'object') ? d.provider : {};
  const providerText = provider.configured === true
    ? (typeof provider.base_url === 'string' && provider.base_url ? provider.base_url : 'configured')
    : 'not configured';
  const providerRow = makeCtxRow('Provider', providerText);
  const probeBtn = document.createElement('button');
  probeBtn.type = 'button';
  probeBtn.className = 'btn btn-sm';
  probeBtn.textContent = diagState.probeBusy ? 'Testing…' : 'Test connection';
  probeBtn.disabled = diagState.probeBusy;
  probeBtn.addEventListener('click', runProbe);
  providerRow.appendChild(probeBtn);
  sec.appendChild(providerRow);

  if (diagState.probe) {
    sec.appendChild(makeDiagNote(diagState.probe.tone, diagState.probe.text));
  }

  const hh = (d.household && typeof d.household === 'object') ? d.household : {};
  sec.appendChild(makeCtxRow('Household',
    diagPair(diagNumber(hh.people), 'people', diagNumber(hh.contexts), 'contexts')));

  const dev = (d.devices && typeof d.devices === 'object') ? d.devices : {};
  sec.appendChild(makeCtxRow('Devices',
    diagPair(diagNumber(dev.enrolled), 'enrolled', diagNumber(dev.registered), 'registered')));

  const rules = (d.rules && typeof d.rules === 'object') ? d.rules : {};
  const rt = diagNumber(rules.total);
  const ra = diagNumber(rules.approved);
  const rp = diagNumber(rules.proposed);
  sec.appendChild(makeCtxRow('Rules',
    rt != null && ra != null && rp != null
      ? rt + ' total · ' + ra + ' approved · ' + rp + ' proposed'
      : '—'));

  const sched = (d.scheduler && typeof d.scheduler === 'object') ? d.scheduler : {};
  sec.appendChild(makeCtxRow('Scheduler',
    diagPair(diagNumber(sched.entries), 'entries', diagNumber(sched.enabled), 'enabled')));

  const events = diagNumber(d.events);
  sec.appendChild(makeCtxRow('Events', events != null ? String(events) : '—'));

  const models = (d.models && typeof d.models === 'object') ? d.models : {};
  sec.appendChild(makeCtxRow('Models',
    diagPair(diagNumber(models.registered), 'registered', diagNumber(models.loaded), 'loaded')));

  const voice = (d.voice && typeof d.voice === 'object') ? d.voice : {};
  const voiceText = voice.enabled === true
    ? 'enabled' + (typeof voice.state === 'string' && voice.state ? ' · ' + voice.state : '')
    : 'disabled';
  sec.appendChild(makeCtxRow('Voice', voiceText));

  sec.appendChild(makeCtxRow('Uptime', fmtUptime(d.uptime_seconds)));

  if (typeof d.config_error === 'string' && d.config_error) {
    sec.appendChild(makeDiagNote('diag-warn', d.config_error));
  }
  return sec;
}

async function runProbe() {
  if (diagState.probeBusy) return;
  diagState.probeBusy = true;
  diagRerender();
  const resp = await diagPost('/api/system/diagnostics/probe', {});
  diagState.probeBusy = false;
  const data = resp && resp.data && typeof resp.data === 'object' ? resp.data : null;
  if (data && data.ok === false) {
    diagState.probe = {
      tone: 'diag-warn',
      text: typeof data.error === 'string' && data.error ? data.error : 'probe failed',
    };
  } else if (data && data.ok === true && data.reachable === true) {
    diagState.probe = { tone: 'diag-ok', text: 'reachable' };
  } else if (data && data.ok === true) {
    const detail = typeof data.detail === 'string' && data.detail ? data.detail : 'no detail';
    diagState.probe = { tone: 'diag-warn', text: 'unreachable: ' + detail };
  } else {
    diagState.probe = { tone: 'diag-warn', text: 'probe failed' };
  }
  diagRerender();
}

/* System view entry + after every backup action. No polling. */
async function refreshSystemDetails() {
  try {
    const res = await fetch('/api/system/diagnostics');
    if (res.ok) {
      const data = await res.json();
      if (data && data.ok && data.diagnostics && typeof data.diagnostics === 'object') {
        diagState.diagnostics = data.diagnostics;
      }
    }
  } catch {
    // Backend may not be up yet — muted '—' rows render below.
  }
  try {
    const res = await fetch('/api/system/backups');
    if (res.ok) {
      const data = await res.json();
      if (data && data.ok && Array.isArray(data.backups)) diagState.backups = data.backups;
    }
  } catch {
    // Same — the list renders 'unavailable' until fetched.
  }
  try {
    const res = await fetch('/api/system/service');
    if (res.ok) {
      const data = await res.json();
      if (data && data.ok && data.service && typeof data.service === 'object') {
        diagState.service = data.service;
      }
    }
  } catch {
    // Same — the status row renders '—' until fetched.
  }
  diagRerender();
}

function makeBackupSection() {
  const sec = makeSysSection('BACKUP');

  const createBtn = document.createElement('button');
  createBtn.type = 'button';
  createBtn.className = 'btn';
  createBtn.textContent = 'Create backup';
  createBtn.addEventListener('click', createBackup);
  sec.appendChild(createBtn);

  if (diagState.backupError) {
    sec.appendChild(makeDiagNote('diag-warn', diagState.backupError));
  }
  if (diagState.backupNote) {
    sec.appendChild(makeDiagNote(diagState.backupNote.tone, diagState.backupNote.text));
  }

  const backups = diagState.backups;
  if (!backups) {
    sec.appendChild(makeSysUnavailable());
  } else if (!backups.length) {
    const none = document.createElement('p');
    none.className = 'sys-unavailable muted';
    none.textContent = 'No backups yet.';
    sec.appendChild(none);
  } else {
    for (const backup of backups) sec.appendChild(makeBackupRow(backup));
  }
  return sec;
}

function makeBackupRow(backup) {
  const b = (backup && typeof backup === 'object') ? backup : {};
  const id = typeof b.id === 'string' && b.id ? b.id : null;
  const row = document.createElement('div');
  row.className = 'feed-row';

  const idEl = document.createElement('span');
  idEl.className = 'feed-text diag-mono';
  idEl.textContent = id || 'unknown';
  row.appendChild(idEl);

  const when = document.createElement('span');
  when.className = 'muted';
  when.textContent = typeof b.created_at === 'string' && b.created_at ? b.created_at : '—';
  row.appendChild(when);

  const files = Array.isArray(b.files) ? b.files.length : 0;
  const count = document.createElement('span');
  count.className = 'muted';
  count.textContent = files + (files === 1 ? ' file' : ' files');
  row.appendChild(count);

  const busy = id != null && diagState.busy.has(id);
  const restoreBtn = document.createElement('button');
  restoreBtn.type = 'button';
  restoreBtn.className = 'btn btn-sm';
  restoreBtn.textContent = 'Restore';
  restoreBtn.disabled = busy;
  restoreBtn.addEventListener('click', () => { if (id != null) restoreBackup(id); });
  row.appendChild(restoreBtn);

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'btn btn-sm';
  deleteBtn.textContent = 'Delete';
  deleteBtn.disabled = busy;
  deleteBtn.addEventListener('click', () => { if (id != null) deleteBackup(id); });
  row.appendChild(deleteBtn);

  return row;
}

function diagBackupError(resp, fallback) {
  const data = resp && resp.data && typeof resp.data === 'object' ? resp.data : null;
  if (data && typeof data.error === 'string' && data.error) return data.error;
  return fallback;
}

async function createBackup() {
  diagState.backupError = null;
  const resp = await diagPost('/api/system/backup', {});
  const data = resp && resp.data && typeof resp.data === 'object' ? resp.data : null;
  if (data && data.ok === true && data.backup && typeof data.backup.id === 'string') {
    diagState.backupNote = { tone: 'diag-ok', text: 'Backup ' + data.backup.id + ' created' };
  } else {
    diagState.backupNote = null;
    diagState.backupError = diagBackupError(resp, 'Backup failed');
  }
  await refreshSystemDetails();
}

async function restoreBackup(id) {
  diagState.backupError = null;
  diagState.busy.add(id);
  diagRerender();
  const resp = await diagPost('/api/system/backup/restore', { id: id });
  diagState.busy.delete(id);
  const data = resp && resp.data && typeof resp.data === 'object' ? resp.data : null;
  if (data && data.ok === true) {
    const result = data.result && typeof data.result === 'object' ? data.result : {};
    diagState.backupNote = result.restart_required === true
      ? { tone: 'diag-warn', text: 'Restored. Restart HAVEN to apply.' }
      : { tone: 'diag-ok', text: 'Restored.' };
  } else {
    diagState.backupNote = null;
    diagState.backupError = diagBackupError(resp, 'Restore failed');
  }
  await refreshSystemDetails();
}

async function deleteBackup(id) {
  diagState.backupError = null;
  diagState.busy.add(id);
  diagRerender();
  const resp = await diagPost('/api/system/backup/delete', { id: id });
  diagState.busy.delete(id);
  const data = resp && resp.data && typeof resp.data === 'object' ? resp.data : null;
  if (!data || data.ok !== true) {
    diagState.backupError = diagBackupError(resp, 'Delete failed');
  }
  await refreshSystemDetails();
}

/* Logon-start (per-user Task Scheduler task) status + install/remove. The
   task only affects the NEXT sign-in — the running server is untouched.
   Missing keys render muted '—'; nothing here throws on a null envelope. */
function makeServiceSection() {
  const sec = makeSysSection('SERVICE');
  const svc = diagState.service;
  if (!svc || typeof svc !== 'object') {
    const row = makeCtxRow('Status', '—');
    row.lastElementChild.className = 'ctx-value muted';
    sec.appendChild(row);
    return sec;
  }

  const installed = svc.installed === true;
  const running = svc.running === true;
  const statusText = !installed
    ? 'Not installed'
    : running ? 'Installed · running' : 'Installed · starts at next logon';
  const statusRow = makeCtxRow('Status', statusText);
  statusRow.lastElementChild.className = installed && running ? 'ctx-value ok' : 'ctx-value muted';
  sec.appendChild(statusRow);

  if (typeof svc.detail === 'string' && svc.detail) {
    sec.appendChild(makeDiagNote(null, svc.detail));
  }

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn';
  btn.textContent = diagState.serviceBusy
    ? 'Working…'
    : installed ? 'Remove' : 'Install — start at logon';
  btn.disabled = diagState.serviceBusy;
  btn.addEventListener('click', () => runServiceAction(installed ? 'uninstall' : 'install'));
  sec.appendChild(btn);

  if (diagState.serviceNote) {
    sec.appendChild(makeDiagNote(diagState.serviceNote.tone, diagState.serviceNote.text));
  }

  const explainer = document.createElement('p');
  explainer.className = 'diag-note';
  explainer.textContent = 'Runs HAVEN at sign-in as this user. The current session is unaffected.';
  sec.appendChild(explainer);
  return sec;
}

async function runServiceAction(action) {
  if (diagState.serviceBusy) return;
  diagState.serviceBusy = true;
  diagState.serviceNote = null;
  diagRerender();
  const resp = await diagPost('/api/system/service/' + action, {});
  diagState.serviceBusy = false;
  const data = resp && resp.data && typeof resp.data === 'object' ? resp.data : null;
  if (data && data.ok === true) {
    diagState.serviceNote = {
      tone: 'diag-ok',
      text: typeof data.detail === 'string' && data.detail ? data.detail : 'done',
    };
  } else {
    diagState.serviceNote = { tone: 'diag-warn', text: diagBackupError(resp, 'Request failed') };
  }
  await refreshSystemDetails();
}

/* ---------- models view ---------- */
/* Model-manager surface: GET /api/models for state, POST endpoints for
   mutations. Not part of the SSE demo state — refreshed only on view
   entry and after actions. Missing keys render as empty/muted. */

const MODEL_KINDS = [
  ['intelligence', 'Intelligence'],
  ['vision', 'Vision'],
  ['speech', 'Speech'],
  ['embeddings', 'Embeddings'],
  ['prediction', 'Prediction'],
  ['specialized', 'Specialized'],
];

const MODEL_LOADABLE = new Set(['ready', 'registered', 'verified']);
const MODEL_STATE_CLASS = {
  loaded: 'state-loaded',
  ready: '',
  registered: '',
  verified: '',
  hash_mismatch: 'state-amber',
  backend_missing: 'state-amber',
  load_failed: 'state-amber',
  unreachable: 'state-amber',
  incomplete: 'state-dim',
  unsupported: 'state-dim',
  license_unknown: 'state-dim',
};

/* Role assignment: six fixed roles; a model can serve a role when its
   kind/capabilities match. Defensive substring matching on capability
   names — chat by intelligence family, speech roles by capability name,
   vision by vision family. */
const ROLE_LABEL = {
  chat: 'chat',
  asr: 'asr',
  tts: 'tts',
  wake_word: 'wake',
  vad: 'vad',
  vision: 'vision',
};

const ROLE_CAP_PATTERN = {
  asr: /asr|transcrib|speech[ _-]?to[ _-]?text/i,
  tts: /tts|text[ _-]?to[ _-]?speech|synth/i,
  wake_word: /wake/i,
  vad: /\bvad\b|voice[ _-]?activity/i,
};

function modelServableRoles(model) {
  if (!model || typeof model !== 'object') return [];
  const kind = String(model.kind || '').toLowerCase();
  const caps = (Array.isArray(model.capabilities) ? model.capabilities : [])
    .map((c) => String(c).toLowerCase());
  const hay = caps.join(' ');
  const roles = [];
  if (kind === 'intelligence' || /chat|instruct|generat|completion|llm/.test(hay)) {
    roles.push('chat');
  }
  for (const role of ['asr', 'tts', 'wake_word', 'vad']) {
    const re = ROLE_CAP_PATTERN[role];
    if (re && caps.some((c) => re.test(c))) roles.push(role);
  }
  if (kind === 'vision' || /vision|image|object[ _-]?det|caption|ocr/.test(hay)) {
    roles.push('vision');
  }
  return roles;
}

/* Detail string when the model's backend is registered but unavailable;
   null when available or unknown — callers render the plain button. */
function backendMissingDetail(backend) {
  if (!backend) return null;
  const entry = modelsState.backends.find((b) => b && b.backend === backend);
  if (!entry || entry.available !== false) return null;
  return (typeof entry.detail === 'string' && entry.detail) ? entry.detail : 'runtime not available';
}

const modelsState = {
  models: [],
  roots: [],
  catalog: [],
  discovered: [],
  inspectedUrl: null,
  jobs: [],
  backends: [],    // [{backend, available, detail}] from GET /api/models
  assignments: {}, // role -> model_id | null
  readyTimers: new Map(), // job_id -> timeout dropping a `ready` job from the active list
  noteTimer: null,        // auto-hide for the "Download started" note
};

const modelPanels = {
  download: () => [els.panelDownload, els.panelErrorDownload],
  local: () => [els.panelLocal, els.panelErrorLocal],
  endpoint: () => [els.panelEndpoint, els.panelErrorEndpoint],
};

function modelBadge(text, extraClass) {
  const badge = document.createElement('span');
  badge.className = 'model-badge ' + (extraClass || '');
  badge.textContent = text;
  return badge;
}

function capsText(caps) {
  const list = Array.isArray(caps) ? caps : [];
  if (!list.length) return '';
  return list.slice(0, 4).join(', ') + (list.length > 4 ? ' +' + (list.length - 4) : '');
}

function folderName(path) {
  const parts = String(path || '').split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : (path || 'unknown');
}

function activeModelPanelError() {
  for (const key of Object.keys(modelPanels)) {
    const [panel, err] = modelPanels[key]();
    if (!panel.hidden) return err;
  }
  return null;
}

function showModelError(errEl, msg) {
  const target = errEl || activeModelPanelError();
  if (!target) return;
  target.textContent = msg;
  target.hidden = false;
}

function clearModelError(errEl) {
  const target = errEl || activeModelPanelError();
  if (!target) return;
  target.textContent = '';
  target.hidden = true;
}

function toggleModelPanel(name) {
  for (const key of Object.keys(modelPanels)) {
    const [panel] = modelPanels[key]();
    panel.hidden = key === name ? !panel.hidden : true;
  }
  for (const key of Object.keys(modelPanels)) {
    const [, err] = modelPanels[key]();
    clearModelError(err);
  }
}

function closeModelPanels() {
  for (const key of Object.keys(modelPanels)) {
    const [panel, err] = modelPanels[key]();
    panel.hidden = true;
    clearModelError(err);
  }
}

async function refreshModels() {
  try {
    const res = await fetch('/api/models');
    if (res.ok) {
      const data = await res.json();
      if (data && data.ok && Array.isArray(data.models)) {
        modelsState.models = data.models;
        modelsState.roots = Array.isArray(data.roots) ? data.roots : [];
        modelsState.catalog = Array.isArray(data.catalog) ? data.catalog : [];
        modelsState.backends = Array.isArray(data.backends) ? data.backends : [];
        modelsState.assignments =
          (data.assignments && typeof data.assignments === 'object') ? data.assignments : {};
      }
    }
  } catch {
    // Backend may not be up yet — empty/muted states render below.
  }
  renderModels();
}

/* Successful POSTs return the models+roots payload — replace local state. */
function applyModelsResponse(resp, errEl) {
  if (!resp) return false; // 404 / network — quiet no-op, per postJSON
  if (!resp.ok) {
    showModelError(errEl, typeof resp.error === 'string' ? resp.error : 'Request failed');
    return false;
  }
  if (Array.isArray(resp.models)) modelsState.models = resp.models;
  if (Array.isArray(resp.roots)) modelsState.roots = resp.roots;
  if (Array.isArray(resp.backends)) modelsState.backends = resp.backends;
  if (resp.assignments && typeof resp.assignments === 'object') {
    modelsState.assignments = resp.assignments;
  }
  renderModels();
  return true;
}

function renderModels() {
  const models = modelsState.models;
  els.modelsCount.textContent =
    models.length + (models.length === 1 ? ' model' : ' models');

  els.modelsList.textContent = '';
  if (!models.length) {
    const empty = document.createElement('p');
    empty.className = 'sys-unavailable muted';
    empty.textContent = 'No models installed.';
    els.modelsList.appendChild(empty);
  } else {
    for (const [kind, label] of MODEL_KINDS) {
      const family = models.filter((m) => m && m.kind === kind);
      if (!family.length) continue;
      const sec = makeSysSection(label.toUpperCase());
      for (const model of family) sec.appendChild(makeModelRow(model));
      els.modelsList.appendChild(sec);
    }
  }

  renderCatalog();
  renderRoots();
  renderDiscovered();
  renderRuntimes();
}

/* Runtime availability strip — quiet, informational. Missing backends key
   or an empty list simply hides the strip. */
function renderRuntimes() {
  const backends = modelsState.backends;
  els.runtimesStrip.textContent = '';
  if (!backends.length) {
    els.runtimesStrip.hidden = true;
    return;
  }
  const label = document.createElement('span');
  label.className = 'micro';
  label.textContent = 'RUNTIMES';
  els.runtimesStrip.appendChild(label);
  for (const b of backends) {
    if (!b || typeof b !== 'object') continue;
    const available = b.available === true;
    const chip = document.createElement('span');
    chip.className = 'runtime-chip ' + (available ? 'rt-ready' : 'rt-missing');
    chip.textContent = String(b.backend || 'unknown') + ' ' + (available ? 'ready' : 'missing');
    if (typeof b.detail === 'string' && b.detail) chip.title = b.detail;
    els.runtimesStrip.appendChild(chip);
  }
  els.runtimesStrip.hidden = false;
}

function makeModelRow(model) {
  const row = document.createElement('div');
  row.className = 'model-row';

  const main = document.createElement('div');
  main.className = 'model-main';

  const id = document.createElement('span');
  id.className = 'model-id';
  id.textContent = model.id || 'unknown';
  main.appendChild(id);

  const metaParts = [];
  if (model.architecture) metaParts.push(model.architecture);
  if (model.backend) metaParts.push(model.backend);
  if (metaParts.length) {
    const meta = document.createElement('span');
    meta.className = 'model-meta';
    meta.textContent = metaParts.join(' · ');
    main.appendChild(meta);
  }

  const caps = capsText(model.capabilities);
  if (caps) {
    const capsEl = document.createElement('span');
    capsEl.className = 'model-caps';
    capsEl.textContent = caps;
    main.appendChild(capsEl);
  }

  const extraParts = [];
  if (model.device) extraParts.push(model.device);
  if (model.license) extraParts.push(model.license);
  if (extraParts.length) {
    const extras = document.createElement('span');
    extras.className = 'model-extras';
    extras.textContent = extraParts.join(' · ');
    main.appendChild(extras);
  }

  const roles = modelServableRoles(model);
  if (roles.length) {
    const roleRow = document.createElement('div');
    roleRow.className = 'model-roles';
    for (const role of roles.slice(0, 3)) roleRow.appendChild(makeRoleToken(model, role));
    if (roles.length > 3) {
      const more = document.createElement('span');
      more.className = 'role-token role-more';
      more.textContent = '+' + (roles.length - 3);
      roleRow.appendChild(more);
    }
    main.appendChild(roleRow);
  }

  row.appendChild(main);

  const badges = document.createElement('div');
  badges.className = 'model-badges';
  const source = String(model.source || '').toLowerCase();
  if (source === 'local') badges.appendChild(modelBadge('LOCAL', 'src-local'));
  else if (source === 'downloaded') badges.appendChild(modelBadge('DOWNLOADED', ''));
  else if (source === 'endpoint') badges.appendChild(modelBadge('ENDPOINT', 'src-endpoint'));
  const state = String(model.state || 'unknown');
  badges.appendChild(modelBadge(state.toUpperCase(), MODEL_STATE_CLASS[state] || 'state-dim'));
  row.appendChild(badges);

  const actions = document.createElement('div');
  actions.className = 'model-actions';

  let runtimeMissing = null;
  if (state === 'loaded') {
    actions.appendChild(makeModelAction(model.id, 'unload', 'Unload'));
  } else if (MODEL_LOADABLE.has(state)) {
    runtimeMissing = backendMissingDetail(model.backend);
    const load = makeModelAction(model.id, 'load', 'Load');
    if (runtimeMissing != null) {
      load.classList.add('btn-runtime-missing');
      load.title = runtimeMissing;
    }
    actions.appendChild(load);
  }
  actions.appendChild(makeModelAction(model.id, 'remove', 'Remove'));

  const actCol = document.createElement('div');
  actCol.className = 'model-act';
  actCol.appendChild(actions);
  if (runtimeMissing != null) {
    const note = document.createElement('span');
    note.className = 'model-runtime-note micro';
    note.textContent = 'runtime missing';
    actCol.appendChild(note);
  }
  row.appendChild(actCol);
  return row;
}

/* Role token: click assigns the model to the role; clicking the ACTIVE
   token clears it (id: null). Responses flow through applyModelsResponse,
   so a successful assign re-renders and ACTIVE moves to the new model. */
function makeRoleToken(model, role) {
  const assigned = modelsState.assignments[role] === model.id;
  const token = document.createElement('button');
  token.type = 'button';
  token.className = 'role-token' + (assigned ? ' role-active' : '');
  token.textContent = ROLE_LABEL[role] || role;
  token.title = assigned ? role + ' — click to clear' : 'assign to ' + role;
  token.addEventListener('click', async () => {
    clearModelError(null);
    const resp = await postJSON('/api/models/assign', {
      role: role,
      id: assigned ? null : model.id,
    });
    applyModelsResponse(resp, null);
  });
  return token;
}

function makeModelAction(id, action, label) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn';
  btn.textContent = label;
  btn.addEventListener('click', async () => {
    clearModelError(null);
    const resp = await postJSON('/api/models/' + action, { id: id });
    applyModelsResponse(resp, null);
  });
  return btn;
}

function renderCatalog() {
  els.catalogList.textContent = '';
  const catalog = modelsState.catalog;
  if (!catalog.length) {
    const empty = document.createElement('p');
    empty.className = 'sys-unavailable muted';
    empty.textContent = 'Catalog unavailable.';
    els.catalogList.appendChild(empty);
    return;
  }
  for (const entry of catalog) {
    const row = document.createElement('div');
    row.className = 'feed-row';

    const kind = document.createElement('span');
    kind.className = 'feed-type micro';
    kind.textContent = entry.kind || '';
    row.appendChild(kind);

    const body = document.createElement('span');
    body.className = 'feed-text';
    body.textContent = entry.id || '';
    row.appendChild(body);

    const dots = document.createElement('span');
    dots.className = 'ctx-dots';
    row.appendChild(dots);

    if (entry.description) {
      const desc = document.createElement('span');
      desc.className = 'muted';
      desc.textContent = entry.description;
      row.appendChild(desc);
    }

    const hints = [];
    if (entry.size_hint_mb != null) hints.push(entry.size_hint_mb + ' MB');
    if (entry.license) hints.push(entry.license);
    if (hints.length) {
      const hint = document.createElement('span');
      hint.className = 'feed-stamp';
      hint.textContent = hints.join(' · ');
      row.appendChild(hint);
    }

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Download';
    btn.addEventListener('click', async () => {
      await startModelDownload(entry.manifest_url, els.panelErrorDownload);
    });
    row.appendChild(btn);

    els.catalogList.appendChild(row);
  }
}

function renderRoots() {
  els.rootsList.textContent = '';
  if (!modelsState.roots.length) {
    const empty = document.createElement('p');
    empty.className = 'sys-unavailable muted';
    empty.textContent = 'No roots configured.';
    els.rootsList.appendChild(empty);
    return;
  }
  for (const root of modelsState.roots) {
    const row = document.createElement('div');
    row.className = 'feed-row';
    const body = document.createElement('span');
    body.className = 'feed-text';
    body.textContent = root;
    row.appendChild(body);
    els.rootsList.appendChild(row);
  }
}

function renderDiscovered() {
  els.discoveredList.textContent = '';
  const discovered = modelsState.discovered;
  if (!discovered.length) return;

  const head = document.createElement('h3');
  head.className = 'micro';
  head.textContent = 'DISCOVERED';
  els.discoveredList.appendChild(head);

  for (const cand of discovered) {
    const row = document.createElement('div');
    row.className = 'feed-row';

    const known = cand.id && cand.kind;
    const name = document.createElement('span');
    name.className = 'feed-text';
    name.textContent = known ? cand.id : folderName(cand.path);
    row.appendChild(name);

    if (known) {
      const kind = document.createElement('span');
      kind.className = 'feed-type micro';
      kind.textContent = cand.kind;
      row.appendChild(kind);
    }

    const dots = document.createElement('span');
    dots.className = 'ctx-dots';
    row.appendChild(dots);

    if (Array.isArray(cand.problems) && cand.problems.length) {
      const problems = document.createElement('span');
      problems.className = 'muted';
      problems.textContent = cand.problems.join(', ');
      row.appendChild(problems);
    }

    const state = String(cand.state || 'unknown');
    row.appendChild(modelBadge(state.toUpperCase(), MODEL_STATE_CLASS[state] || 'state-dim'));

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Register';
    btn.addEventListener('click', async () => {
      clearModelError(els.panelErrorLocal);
      const resp = await postJSON('/api/models/register', { path: cand.path });
      if (!applyModelsResponse(resp, els.panelErrorLocal)) return;
      modelsState.discovered = modelsState.discovered.filter((c) => c !== cand);
      renderDiscovered();
    });
    row.appendChild(btn);

    els.discoveredList.appendChild(row);
  }
}

function renderInspection(inspection, url) {
  els.inspectionBox.textContent = '';
  els.inspectInstall.hidden = true;
  modelsState.inspectedUrl = null;

  const info = (inspection && typeof inspection === 'object') ? inspection : {};
  const box = document.createElement('div');
  box.className = 'sys-section';
  box.style.marginBottom = '0';

  const add = (label, value) => box.appendChild(makeCtxRow(label, value));
  add('Id', info.id || '—');
  add('Kind', info.kind || '—');
  add('Backend', info.backend || '—');
  add('Architecture', info.architecture || '—');
  add('Version', info.version || '—');
  add('License', info.license || '—');
  add('Languages', Array.isArray(info.languages) && info.languages.length ? info.languages.join(', ') : '—');
  add('Files', info.file_count != null ? String(info.file_count) : '—');
  add('Hash verification', info.hash_verification ? 'available' : 'not available');
  add('Remote code', info.remote_code === 'none' ? 'none' : (info.remote_code || '—'));

  els.inspectionBox.appendChild(box);
  modelsState.inspectedUrl = url;
  els.inspectInstall.hidden = false;
}

/* ---------- download jobs ---------- */
/* Job feed: SSE /api/models/events while the models view is active.
   `jobs` replaces the list; `model_job` upserts one job. `ready` jobs
   fade out of the active list after a few seconds; failed/cancelled
   stay visible in amber until the next full replace. */

const JOB_STATE_BADGE = {
  downloading: 'src-local',
  verifying: 'src-local',
  ready: 'src-local',
  queued: '',
  failed: 'state-amber',
  cancelled: 'state-amber',
};

let modelsJobSource = null; // module-scoped EventSource, null when the models view is not active

function isJobLike(j) {
  return !!j && typeof j === 'object' && typeof j.job_id === 'string';
}

function jobName(job) {
  if (job.manifest_id) return job.manifest_id;
  try {
    return new URL(job.url).host;
  } catch {
    return job.url || 'unknown';
  }
}

function clearReadyTimer(jobId) {
  const timer = modelsState.readyTimers.get(jobId);
  if (timer != null) {
    clearTimeout(timer);
    modelsState.readyTimers.delete(jobId);
  }
}

function scheduleReadyFade(job) {
  if (job.state !== 'ready') return;
  clearReadyTimer(job.job_id);
  modelsState.readyTimers.set(job.job_id, setTimeout(() => {
    modelsState.readyTimers.delete(job.job_id);
    modelsState.jobs = modelsState.jobs.filter((j) => j.job_id !== job.job_id);
    renderJobs();
  }, 5000));
}

function upsertJob(job) {
  if (!isJobLike(job)) return;
  const list = modelsState.jobs;
  const i = list.findIndex((j) => j.job_id === job.job_id);
  if (i >= 0) list[i] = job;
  else list.push(job);
  if (job.state === 'ready') scheduleReadyFade(job);
  else clearReadyTimer(job.job_id);
  // Jobs finish asynchronously — resync the installed list on terminal states.
  if (job.state === 'ready' || job.state === 'failed') refreshModels();
  renderJobs();
}

function renderJobs() {
  const jobs = modelsState.jobs;
  els.jobsSection.hidden = jobs.length === 0;
  els.jobsList.textContent = '';
  for (const job of jobs) {
    if (isJobLike(job)) els.jobsList.appendChild(makeJobRow(job));
  }
}

function makeJobRow(job) {
  const row = document.createElement('div');
  row.className = 'job-row';

  const head = document.createElement('div');
  head.className = 'job-row-head';

  const name = document.createElement('span');
  name.className = 'job-name';
  name.textContent = jobName(job);
  head.appendChild(name);

  const state = String(job.state || 'queued');
  head.appendChild(modelBadge(state.toUpperCase(), JOB_STATE_BADGE[state] || ''));

  const received = Number(job.received_bytes) || 0;
  const total = typeof job.total_bytes === 'number' && job.total_bytes > 0 ? job.total_bytes : 0;

  if (state === 'queued' || state === 'downloading' || state === 'verifying') {
    const measure = document.createElement('span');
    measure.className = 'job-bytes muted';
    if (total > 0) {
      measure.textContent = Math.min(100, (received / total) * 100).toFixed(0) + '%';
    } else {
      measure.textContent = (received / 1048576).toFixed(1) + ' MB';
    }
    head.appendChild(measure);

    const bar = document.createElement('div');
    bar.className = 'job-progress' + (total > 0 ? '' : ' indeterminate');
    const fill = document.createElement('span');
    if (total > 0) fill.style.width = Math.min(100, (received / total) * 100).toFixed(1) + '%';
    bar.appendChild(fill);
    row.appendChild(bar);
  }

  if (state === 'queued' || state === 'downloading') {
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', async () => {
      const resp = await postJSON('/api/models/jobs/' + encodeURIComponent(job.job_id) + '/cancel', {});
      if (resp && resp.ok && isJobLike(resp.job)) upsertJob(resp.job);
      else refreshJobs();
    });
    head.appendChild(cancel);
  }

  row.appendChild(head);

  if (job.current_file) {
    const file = document.createElement('div');
    file.className = 'job-file muted';
    file.textContent = job.current_file;
    row.appendChild(file);
  }

  if (state === 'failed' && job.error) {
    const err = document.createElement('div');
    err.className = 'job-file muted';
    err.textContent = job.error;
    row.appendChild(err);
  }

  return row;
}

async function refreshJobs() {
  try {
    const res = await fetch('/api/models/jobs');
    if (!res.ok) return;
    const data = await res.json();
    if (!data || data.ok === false || !Array.isArray(data.jobs)) return;
    for (const timer of modelsState.readyTimers.values()) clearTimeout(timer);
    modelsState.readyTimers.clear();
    modelsState.jobs = data.jobs.filter(isJobLike);
    for (const job of modelsState.jobs) scheduleReadyFade(job);
    renderJobs();
  } catch {
    // Backend may not be up yet — the SSE stream will deliver state later.
  }
}

function startJobsStream() {
  stopJobsStream();
  refreshJobs(); // defensive fetch alongside the stream's initial `jobs` event
  const es = new EventSource('/api/models/events');
  modelsJobSource = es;

  es.addEventListener('jobs', (e) => {
    try {
      const data = JSON.parse(e.data);
      if (!data || data.ok === false || !Array.isArray(data.jobs)) return;
      for (const timer of modelsState.readyTimers.values()) clearTimeout(timer);
      modelsState.readyTimers.clear();
      modelsState.jobs = data.jobs.filter(isJobLike);
      for (const job of modelsState.jobs) scheduleReadyFade(job);
      renderJobs();
    } catch {
      // malformed payload — wait for the next event
    }
  });

  es.addEventListener('model_job', (e) => {
    try {
      upsertJob(JSON.parse(e.data));
    } catch {
      // malformed payload — wait for the next event
    }
  });

  // EventSource auto-reconnects; nothing to do but stay alive.
  es.addEventListener('error', () => {});
}

function stopJobsStream() {
  if (modelsJobSource) {
    modelsJobSource.close();
    modelsJobSource = null;
  }
}

/* POST /api/models/download — returns a job, not a models payload.
   On ok, a muted note confirms the start and the jobs section carries
   progress; terminal job events resync the installed list. */
async function startModelDownload(url, errEl) {
  clearModelError(errEl);
  const resp = await postJSON('/api/models/download', { url: url });
  if (!resp) return false; // 404 / network — quiet no-op, per postJSON
  if (!resp.ok) {
    showModelError(errEl, typeof resp.error === 'string' ? resp.error : 'Download failed');
    return false;
  }
  els.downloadNote.textContent = 'Download started';
  els.downloadNote.hidden = false;
  clearTimeout(modelsState.noteTimer);
  modelsState.noteTimer = setTimeout(() => {
    els.downloadNote.hidden = true;
  }, 4000);
  refreshJobs();
  return true;
}

/* ---------- nav views ---------- */

function switchView(view, label) {
  app.view = view;
  els.center.dataset.view = view;
  if (view === 'models') refreshModels();
  if (view === 'system') refreshSystemDetails();
  if (view === 'placeholder') {
    els.placeholderNote.textContent = (label || 'This view') + ' is not in this slice.';
  }
}

/* ---------- dev overlay ---------- */

async function fireWake() {
  const resp = await postJSON('/api/voice/wake', {});
  if (resp && resp.ok && resp.state) renderState(resp.state);
}

function updateDevButtons() {
  for (const btn of els.devButtons) {
    btn.classList.toggle('active', btn.dataset.preview === app.preview);
  }
}

/* ---------- notifications ----------
   Off-screen extension of the two attention meanings only: glow "permission"
   (HAVEN needs you) and glow "critical" (something is wrong). Never fires
   for acting/completed/idle. Opt-in only; failures never touch rendering. */

const NOTIF_STORE_KEY = 'haven.notifications.enabled';

const notifState = {
  enabled: false,
  permission: 'default',
  lastKey: null,
  btn: null,
};

function notifKey(state) {
  if (!state) return null;
  if (state.glow === 'permission') {
    const pending = state.pending;
    if (!Array.isArray(pending) || pending.length === 0) return null;
    const req = pending[0] || {};
    return 'permission:' + (req.request_id != null ? req.request_id : 'unknown');
  }
  if (state.glow === 'critical') {
    return 'critical:' + (state.revision != null ? state.revision : '0');
  }
  return null; // acting / completed / idle — ordinary activity stays quiet
}

function syncNotifBell() {
  if (!notifState.btn) return;
  const granted = notifState.permission === 'granted';
  const on = notifState.enabled && granted;
  notifState.btn.classList.toggle('on', on);
  notifState.btn.title = on
    ? 'Notifications on'
    : (notifState.permission === 'denied'
        ? 'Notifications blocked by the browser'
        : 'Enable notifications');
}

function maybeNotify(state) {
  try {
    if (!('Notification' in window)) return;
    notifState.permission = Notification.permission;
    syncNotifBell();
    const key = notifKey(state);
    if (!key || key === notifState.lastKey) return;
    if (!notifState.enabled || Notification.permission !== 'granted') return;
    let title = 'HAVEN';
    let body = 'Attention needed';
    if (key.indexOf('permission:') === 0) {
      const req = (state.pending && state.pending[0]) || {};
      title = req.title || 'Permission requested';
      body = req.detail || '';
    } else if (state.status && state.status.line) {
      body = state.status.line;
    }
    const n = new Notification(title, { body: body, tag: key, silent: false });
    n.onclick = () => { window.focus(); };
    notifState.lastKey = key;
  } catch {
    // best-effort by doctrine — a failed notification never breaks rendering
  }
}

function initNotifBell() {
  if (!('Notification' in window)) return; // unsupported — no button at all
  notifState.permission = Notification.permission;
  let stored = null;
  try {
    stored = localStorage.getItem(NOTIF_STORE_KEY);
  } catch {
    // private mode etc. — session-only opt-in
  }
  notifState.enabled = stored === '1';

  const host = els.pill ? els.pill.parentElement : null;
  if (!host) return;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'notif-bell';
  btn.setAttribute('aria-label', 'Notifications');
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svgNS, 'path');
  path.setAttribute('d',
    'M8 1.8a3.3 3.3 0 0 0-3.3 3.3v2.7l-1.4 2.1h9.4l-1.4-2.1V5.1A3.3 3.3 0 0 0 8 1.8z' +
    'M6.4 11.9a1.6 1.6 0 0 0 3.2 0');
  svg.appendChild(path);
  btn.appendChild(svg);

  btn.addEventListener('click', async () => {
    try {
      if (Notification.permission === 'default') {
        const result = await Notification.requestPermission();
        notifState.permission = result;
        notifState.enabled = result === 'granted';
      } else if (Notification.permission === 'denied') {
        btn.title = 'Notifications blocked by the browser';
        return; // browsers require a settings change for denied
      }
      try {
        localStorage.setItem(NOTIF_STORE_KEY, notifState.enabled ? '1' : '0');
      } catch {
        // persistence best-effort
      }
      syncNotifBell();
    } catch {
      // never let the bell break the topbar
    }
  });

  notifState.btn = btn;
  host.insertBefore(btn, els.pill); // bell sits left of the state pill
  syncNotifBell();
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

  els.voiceWake.addEventListener('click', fireWake);
  els.demoWake.addEventListener('click', fireWake);

  els.voiceForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = els.voiceInput.value.trim();
    if (!text) return;
    els.voiceInput.value = '';
    const resp = await postJSON('/api/voice/utterance', { text: text });
    if (resp && resp.ok && resp.state) renderState(resp.state);
    // ok:false (e.g. no longer listening) — no-op, server state will resync
  });

  els.voiceStop.addEventListener('click', async () => {
    const resp = await postJSON('/api/voice/cancel', {});
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
    const link = item.querySelector('a');
    if (!link) continue; // group label — not a view switch
    link.addEventListener('click', (e) => {
      e.preventDefault();
      for (const li of els.navItems) li.classList.toggle('active', li === item);
      const label = item.querySelector('.nav-label').textContent;
      if (item.dataset.view === 'models') startJobsStream();
      else stopJobsStream();
      switchView(item.dataset.view, label);
    });
  }

  // Job stream lifecycle: never leave it open when the models view is not
  // active, and close it on page unload so refreshes don't pile up streams.
  window.addEventListener('pagehide', stopJobsStream);

  /* models view: panel toggles + actions */
  for (const btn of els.modelToggles) {
    btn.addEventListener('click', () => toggleModelPanel(btn.dataset.modelPanel));
  }

  els.inspectForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearModelError(els.panelErrorDownload);
    els.inspectionBox.textContent = '';
    els.inspectInstall.hidden = true;
    modelsState.inspectedUrl = null;
    const url = els.inspectUrl.value.trim();
    if (!url) return;
    const resp = await postJSON('/api/models/inspect', { url: url });
    if (!resp) return; // 404 — no-op per postJSON pattern
    if (!resp.ok) {
      showModelError(els.panelErrorDownload,
        typeof resp.error === 'string' ? resp.error : 'Inspect failed');
      return;
    }
    renderInspection(resp.inspection, url);
  });

  els.inspectInstall.addEventListener('click', async () => {
    if (!modelsState.inspectedUrl) return;
    const started = await startModelDownload(modelsState.inspectedUrl, els.panelErrorDownload);
    if (!started) return;
    els.inspectionBox.textContent = '';
    els.inspectInstall.hidden = true;
    modelsState.inspectedUrl = null;
  });

  els.installLocalForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearModelError(els.panelErrorLocal);
    const folder = els.installFolder.value.trim();
    if (!folder) return;
    const resp = await postJSON('/api/models/install-local', { folder: folder });
    if (!applyModelsResponse(resp, els.panelErrorLocal)) return;
    els.installFolder.value = '';
    closeModelPanels();
  });

  els.addRootForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearModelError(els.panelErrorLocal);
    const path = els.rootPath.value.trim();
    if (!path) return;
    const resp = await postJSON('/api/models/add-root', { path: path });
    if (!applyModelsResponse(resp, els.panelErrorLocal)) return;
    els.rootPath.value = '';
  });

  els.scanRoots.addEventListener('click', async () => {
    clearModelError(els.panelErrorLocal);
    const resp = await postJSON('/api/models/scan', {});
    if (!resp) return;
    if (!resp.ok) {
      showModelError(els.panelErrorLocal,
        typeof resp.error === 'string' ? resp.error : 'Scan failed');
      return;
    }
    if (Array.isArray(resp.models)) modelsState.models = resp.models;
    if (Array.isArray(resp.roots)) modelsState.roots = resp.roots;
    if (Array.isArray(resp.backends)) modelsState.backends = resp.backends;
    if (resp.assignments && typeof resp.assignments === 'object') {
      modelsState.assignments = resp.assignments;
    }
    modelsState.discovered = Array.isArray(resp.discovered) ? resp.discovered : [];
    renderModels();
  });

  els.endpointForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearModelError(els.panelErrorEndpoint);
    const url = els.endpointUrl.value.trim();
    if (!url) return;
    const resp = await postJSON('/api/models/add-endpoint', { url: url });
    if (!applyModelsResponse(resp, els.panelErrorEndpoint)) return;
    els.endpointUrl.value = '';
    closeModelPanels();
  });

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

  /* trust chain slide-over: close affordances, one panel at a time */
  els.chainClose.addEventListener('click', closeChainPanel);
  els.chainScrim.addEventListener('click', closeChainPanel);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !els.chainPanel.hidden) closeChainPanel();
  });

  /* setup wizard: local navigation only — no Escape/scrim close, onboarding
     must be finished or the page closed */
  els.setupBack.addEventListener('click', () => setupGotoStep(setupState.step - 1));
  els.setupNext.addEventListener('click', onSetupNext);
}

async function boot() {
  startClock();
  initNotifBell();
  wireEvents();

  try {
    const res = await fetch('/api/state');
    if (res.ok) renderState(await res.json());
  } catch {
    // Backend may not be up yet — the first SSE `state` event will render.
  }

  await refreshSetupOnBoot();

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

  /* installable PWA: register the service worker last so a failed or slow
     registration never delays first render; silent failure is fine */
  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}

boot();
