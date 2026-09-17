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
  peopleList: $('#people-list'),
  peopleCount: $('#people-count'),
  automationsList: $('#automations-list'),
  automationsCount: $('#automations-count'),
  systemBody: $('#system-body'),
  modelsCount: $('#models-count'),
  modelsList: $('#models-list'),
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
};

/* Voice states that mean HAVEN is actively capturing audio. */
const VOICE_ACTIVE = new Set(['wake', 'listening', 'interpreting']);

const app = {
  data: null,
  focus: null,      // room id or null
  view: 'world',    // center view: world | activity | memory | people | automations | system | models | placeholder
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

  els.systemBody.appendChild(core);
  els.systemBody.appendChild(engine);
  els.systemBody.appendChild(providers);
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

const modelsState = {
  models: [],
  roots: [],
  catalog: [],
  discovered: [],
  inspectedUrl: null,
  jobs: [],
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

  if (state === 'loaded') {
    actions.appendChild(makeModelAction(model.id, 'unload', 'Unload'));
  } else if (MODEL_LOADABLE.has(state)) {
    actions.appendChild(makeModelAction(model.id, 'load', 'Load'));
  }
  actions.appendChild(makeModelAction(model.id, 'remove', 'Remove'));

  row.appendChild(actions);
  return row;
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
