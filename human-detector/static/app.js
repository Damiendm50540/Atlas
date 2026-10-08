// ─── utils ───────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const post = (p, b) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b||{})});
const esc = s => String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = s => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;

let toastTimer;
function toast(msg) {
  $('toast').textContent = msg; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(()=>$('toast').hidden=true, 3500);
}

// ─── user ────────────────────────────────────────────────────
let currentUser = {username:'', role:'user'};
async function loadCurrentUser() {
  try {
    const r = await fetch('/api/me');
    if (r.ok) currentUser = await r.json();
  } catch {}
  $('user-label').textContent = currentUser.username || '—';
  const isAdmin = currentUser.role === 'admin';
  $('tab-people').hidden = !isAdmin;
  $('clear').hidden = !isAdmin;
  $('m-del').hidden = !isAdmin;
  if (!isAdmin && tab === 'people') showTab('live');
}

$('logout').onclick = async () => {
  const r = await fetch('/api/logout', {method:'POST'});
  window.location.href = r.redirected ? r.url : '/login';
};

// ─── tabs ────────────────────────────────────────────────────
let tab = 'live';
function showTab(t) {
  if (currentUser.role !== 'admin' && t === 'people') t = 'live';
  tab = t;
  $('view-live').hidden = t !== 'live';
  $('view-history').hidden = t !== 'history';
  $('view-people').hidden = t !== 'people' || currentUser.role !== 'admin';
  $('tab-live').classList.toggle('on', t === 'live');
  $('tab-history').classList.toggle('on', t === 'history');
  $('tab-people').classList.toggle('on', t === 'people');
  if (t === 'history') refreshHistory();
  if (t === 'people' && currentUser.role === 'admin') loadPeople();
}
$('tab-live').onclick = () => showTab('live');
$('tab-history').onclick = () => showTab('history');
$('tab-people').onclick = () => showTab('people');

// ─── source / vidéo ──────────────────────────────────────────
// Historique des URLs (localStorage) : autofill de la dernière + liste déroulante
const URL_KEY = 'atlas.streamUrls';
const loadUrls = () => { try { return JSON.parse(localStorage.getItem(URL_KEY)) || []; } catch { return []; } };
function renderUrls() {
  $('url-history').innerHTML = loadUrls().map(u => `<option value="${esc(u)}"></option>`).join('');
}
function rememberUrl(u) {
  u = u.trim(); if (!u) return;
  const list = [u, ...loadUrls().filter(x => x !== u)].slice(0, 15);
  try { localStorage.setItem(URL_KEY, JSON.stringify(list)); } catch {}
  renderUrls();
}
renderUrls();
// Au clic, on vide le champ pour que la liste affiche tout l'historique (le navigateur filtre sur le texte);
// si rien n'est choisi/tapé, l'URL précédente revient au blur.
let prevUrl = '';
$('url').addEventListener('focus', () => { prevUrl = $('url').value; $('url').value = ''; });
$('url').addEventListener('blur', () => { if (!$('url').value) $('url').value = prevUrl; });
if (!$('url').value) $('url').value = loadUrls()[0] || '';

$('form').onsubmit = async e => {
  e.preventDefault();
  const r = await post('/api/start', {url: $('url').value});
  if (r.ok) rememberUrl($('url').value);
  showVideoFeed();
};

$('file').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  $('file-name').textContent = f.name;
  $('error').textContent = 'Envoi en cours…';
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/api/upload', {method:'POST', body:fd});
  $('error').textContent = r.ok ? '' : "Échec de l'envoi";
  if (r.ok) showVideoFeed();
  e.target.value = '';
};

function showVideoFeed() {
  $('video-placeholder').hidden = true;
  $('video').style.display = 'block';
  $('video').src = '/api/video?t=' + Date.now();
}

$('stop').onclick = async () => {
  await post('/api/stop');
  $('video').removeAttribute('src');
  $('video').style.display = 'none';
  $('video-placeholder').hidden = false;
};

let paused = false, dragging = false;
$('play').onclick = () => post(paused ? '/api/resume' : '/api/pause');
$('seek').oninput = () => {
  dragging = true;
  $('time').textContent = `${fmt($('seek').value)} / ${fmt($('seek').max)}`;
};
$('seek').onchange = async () => {
  await post('/api/seek', {t: parseFloat($('seek').value)});
  dragging = false;
};

// ─── alarme / digicode ───────────────────────────────────────
let armed = false, entry = '', beep = null, idleTimer = null;

const renderDots = () => {
  $('dots').textContent = '●'.repeat(entry.length) + '○'.repeat(4 - entry.length);
};
renderDots();

['1','2','3','4','5','6','7','8','9','⌫','0','OK'].forEach(k => {
  const b = document.createElement('button');
  b.type = 'button'; b.textContent = k; b.className = 'pad-btn';
  b.onclick = () => key(k);
  $('pad').appendChild(b);
});

async function key(k) {
  $('alarm-msg').textContent = ''; $('alarm-msg').style.color = '';
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { entry = ''; renderDots(); }, 4000);
  if (k === '⌫') entry = entry.slice(0, -1);
  else if (k === 'OK') { await submitAlarm(); return; }
  else if (entry.length < 4) entry += k;
  renderDots();
  if (entry.length === 4 && k !== '⌫') submitAlarm();
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (!$('modal').hidden) {
    if (e.key === 'Escape') closeModal();
    else if (e.key === 'ArrowLeft') step(-1);
    else if (e.key === 'ArrowRight') step(1);
    return;
  }
  if (tab !== 'live') return;
  if (/^\d$/.test(e.key)) key(e.key);
  else if (e.key === 'Backspace') key('⌫');
  else if (e.key === 'Enter') key('OK');
});

async function submitAlarm() {
  if (entry.length !== 4) { $('alarm-msg').textContent = 'Le code doit avoir 4 chiffres'; return; }
  const code = entry, wasArmed = armed;
  entry = ''; renderDots(); clearTimeout(idleTimer);
  let r;
  try { r = await post(wasArmed ? '/api/alarm/disarm' : '/api/alarm/arm', {code}); }
  catch { $('alarm-msg').textContent = 'Serveur injoignable'; return; }
  if (!r.ok) {
    $('alarm-msg').style.color = 'var(--danger)';
    $('alarm-msg').textContent = r.status === 403 ? 'Code incorrect' : `Erreur (${r.status})`;
  } else {
    armed = !wasArmed;
    $('alarm-msg').style.color = 'var(--ok)';
    $('alarm-msg').textContent = wasArmed ? 'Alarme désactivée ✓' : `Alarme activée ✓`;
    setTimeout(() => { $('alarm-msg').textContent = ''; $('alarm-msg').style.color = ''; }, 3000);
  }
}

function ringAlarm(on) {
  if (on && !beep) {
    try {
      const ctx = new AudioContext(), osc = ctx.createOscillator();
      osc.type = 'square'; osc.frequency.value = 880;
      osc.connect(ctx.destination); osc.start();
      let hi = true;
      const t = setInterval(() => { osc.frequency.value = (hi = !hi) ? 880 : 660; }, 400);
      beep = () => { clearInterval(t); osc.stop(); ctx.close(); };
    } catch {}
  } else if (!on && beep) { beep(); beep = null; }
}

// ─── polling statut ──────────────────────────────────────────
setInterval(async () => {
  try {
    const s = await (await fetch('/api/status')).json();
    armed = s.armed;

    // bannière
    const b = $('banner');
    if (s.alarm) {
      b.className = 'status-banner alarm';
      b.innerHTML = `<div class="status-dot"></div><span>🚨 ALARME — ${s.person ? esc(s.person)+' détecté' : 'humain détecté'} !</span>`;
    } else if (s.countdown !== null && s.countdown !== undefined) {
      b.className = 'status-banner countdown';
      b.innerHTML = `<div class="status-dot"></div><span>⚠️ Visage inconnu — alarme dans <strong>${s.countdown} s</strong></span>`;
    } else if (s.human_detected) {
      b.className = 'status-banner detected';
      b.innerHTML = `<div class="status-dot"></div><span>${s.person ? `✅ ${esc(s.person)} reconnu (${s.faces} visage${s.faces>1?'s':''})` : `✅ Humain détecté — ${s.faces} visage${s.faces>1?'s':''}`}</span>`;
    } else {
      b.className = 'status-banner';
      b.innerHTML = '<div class="status-dot"></div><span>Aucun humain détecté</span>';
    }

    // alarm panel
    $('alarm').className = 'card card-body alarm-card' + (armed ? ' armed' : '');
    $('alarm-state-label').innerHTML = armed
      ? `<span class="alarm-armed-badge"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="flex-shrink:0"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>ALARME ACTIVE</span>`
      : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>Alarme désactivée`;

    ringAlarm(s.alarm);
    $('error').textContent = s.error || '';
    $('controls').hidden = !(s.running && s.is_file);
    paused = s.paused;
    $('play').textContent = paused ? '▶ Lecture' : '⏸ Pause';
    if (!dragging) {
      $('seek').max = s.duration;
      $('seek').value = s.position;
      $('time').textContent = `${fmt(s.position)} / ${fmt(s.duration)}`;
    }
  } catch { $('error').textContent = 'Serveur injoignable'; }
}, 500);

// ─── métriques du serveur ─────────────────────────────────────
const SYSTEM_WINDOW_MS = 5 * 60 * 1000;
const systemSamples = [];
let systemSource = null;
const formatBytes = bytes => {
  const units = ['o', 'Ko', 'Mo', 'Go', 'To'];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
};

function renderSystemStats(now = Date.now()) {
  const oldest = now - SYSTEM_WINDOW_MS;
  while (systemSamples.length && systemSamples[0].timestamp < oldest) systemSamples.shift();
  const metrics = [
    {key:'cpu_percent', id:'cpu', value:id=>`${id.toFixed(1)} %`, detail:()=> 'Utilisation CPU · 5 min'},
    {key:'memory_percent', id:'memory', value:id=>`${id.toFixed(1)} %`, detail:sample=>`${formatBytes(sample.memory_used)} / ${formatBytes(sample.memory_total)}`},
    {key:'temperature_c', id:'temperature', value:id=>`${id.toFixed(1)} °C`, detail:()=> 'Température CPU · 5 min'},
    {key:'disk_percent', id:'disk', value:id=>`${id.toFixed(1)} %`, detail:sample=>`${formatBytes(sample.disk_used)} / ${formatBytes(sample.disk_total)}`}
  ];
  for (const metric of metrics) {
    const values = systemSamples.filter(sample => Number.isFinite(sample[metric.key]));
    const latest = values[values.length - 1];
    $(`system-${metric.id}`).textContent = latest ? metric.value(latest[metric.key]) : 'N/D';
    $(`system-${metric.id}-detail`).textContent = latest ? metric.detail(latest) : 'Aucune donnée disponible';
    const path = $(`system-chart-${metric.id}`);
    if (!values.length) { path.setAttribute('d', ''); continue; }
    const raw = values.map(sample => sample[metric.key]);
    const min = Math.min(...raw), max = Math.max(...raw);
    const padding = Math.max((max - min) * 0.15, metric.id === 'temperature' ? 1 : 2);
    const lower = Math.max(0, min - padding), upper = max + padding || 1;
    const points = values.map(sample => {
      const x = Math.max(0, Math.min(300, (sample.timestamp - oldest) / SYSTEM_WINDOW_MS * 300));
      const y = 40 - (sample[metric.key] - lower) / (upper - lower) * 36;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    path.setAttribute('d', `M${points.join(' L')}`);
  }
}

async function loadSystemStats() {
  try {
    const response = await fetch('/api/system/stats');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const sample = await response.json();
    if (systemSource !== sample.source) {
      systemSamples.length = 0;
      systemSource = sample.source;
    }
    sample.timestamp = Number.isFinite(sample.timestamp) ? sample.timestamp * 1000 : Date.now();
    systemSamples.push(sample);
    renderSystemStats();
    $('system-source').textContent = sample.source === 'raspberry_pi'
      ? `Raspberry Pi connecté${sample.hostname ? ` · ${sample.hostname}` : ''}`
      : sample.agent_configured
        ? 'Agent Pi hors ligne · mesures locales du PC ATLAS'
        : 'Mesures locales du PC ATLAS · agent Pi non configuré';
    $('system-updated').textContent = `Mis à jour à ${new Date(sample.timestamp).toLocaleTimeString('fr-FR')}`;
    $('system-error').hidden = true;
  } catch (error) {
    $('system-error').textContent = `Impossible de charger les métriques du serveur (${error.message}).`;
    $('system-error').hidden = false;
  }
}

loadSystemStats();
setInterval(loadSystemStats, 5000);

// ─── historique ──────────────────────────────────────────────
const PAGE = 48;
let items = [], total = 0, alarmOnly = false, day = '', lastTotal = null, current = -1;

const hhmm = ts => new Date(ts*1000).toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
function dayLabel(ts) {
  const d = new Date(ts*1000), today = new Date(), yest = new Date(Date.now()-864e5);
  if (d.toDateString() === today.toDateString()) return "Aujourd'hui";
  if (d.toDateString() === yest.toDateString()) return 'Hier';
  return d.toLocaleDateString('fr-FR', {weekday:'short', day:'numeric', month:'short'});
}
const dur = s => s < 1 ? '< 1 s' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s/60)} min ${Math.round(s%60)} s`;

function range() {
  const q = new URLSearchParams();
  if (alarmOnly) q.set('alarm', 'true');
  if (day) { const t0 = new Date(day+'T00:00:00').getTime()/1000; q.set('since', t0); q.set('until', t0+86400); }
  return q;
}

async function loadStats() {
  const s = await (await fetch('/api/events/stats')).json();
  $('s-total').textContent = s.total; $('s-today').textContent = s.today;
  $('s-24h').textContent = s.last24h; $('s-alarms').textContent = s.alarms;
  $('tab-count').hidden = !s.total; $('tab-count').textContent = s.total;
  const max = Math.max(1, ...s.hourly.map(h => h.n));
  $('bars').innerHTML = s.hourly.map(h => {
    const lbl = `${new Date(h.t*1000).getHours()} h : ${h.n} détection${h.n>1?'s':''}`;
    return `<div class="bar${h.n?'':' zero'}" style="height:${h.n?Math.max(6,h.n/max*100):2}%" title="${lbl}"></div>`;
  }).join('');
  $('axis').innerHTML = s.hourly.map((h,i) =>
    `<span>${i%4===3||i===23 ? new Date(h.t*1000).getHours()+'h' : ''}</span>`
  ).join('');
  $('chart-note').textContent = s.last24h ? `pic : ${max}/h` : '';
  return s;
}

async function loadEvents(reset) {
  if (reset) items = [];
  const q = range(); q.set('limit', PAGE); q.set('offset', items.length);
  const r = await (await fetch('/api/events?'+q)).json();
  items = items.concat(r.items); total = r.total;
  renderGrid();
}

function renderGrid() {
  $('grid').innerHTML = items.map((e,i) => `
    <button type="button" class="event-card" data-i="${i}">
      <div class="thumb" style="background-image:url('${e.crop_url||e.photo_url||''}')">
        ${e.alarm ? '<span class="alarm-badge">🚨 ALARME</span>' : ''}
        ${e.person ? `<span class="person-badge">👤 ${esc(e.person)}</span>` : ''}
        ${e.crop_url&&e.photo_url ? `<span class="thumb-scene" style="background-image:url('${e.photo_url}')" title="Vue d'ensemble"></span>` : ''}
      </div>
      <div class="event-meta">
        <div class="event-when">${dayLabel(e.started_at)} · ${hhmm(e.started_at)}</div>
        <div class="event-sub">
          <span>${dur(e.duration)}</span>
          <span>${Math.round(e.score*100)} %</span>
          ${e.faces>1 ? `<span>${e.faces} visages</span>` : ''}
          <span>${esc(e.source)}</span>
        </div>
      </div>
    </button>`).join('');
  $('empty').hidden = items.length > 0;
  $('more').hidden = items.length >= total;
  $('more').textContent = `Charger plus (${total - items.length} restants)`;
  $('f-reset').hidden = !(alarmOnly || day);
  $('export').href = '/api/events/export.csv?' + range();
}

$('grid').onclick = e => { const c = e.target.closest('.event-card'); if (c) openModal(+c.dataset.i); };
$('more').onclick = () => loadEvents(false);

$('seg-alarm').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  alarmOnly = b.dataset.v === '1';
  [...$('seg-alarm').querySelectorAll('.seg-btn')].forEach(x => x.classList.toggle('on', x === b));
  loadEvents(true);
};
$('f-day').onchange = () => { day = $('f-day').value; loadEvents(true); };
$('f-reset').onclick = () => {
  alarmOnly = false; day = ''; $('f-day').value = '';
  [...$('seg-alarm').querySelectorAll('.seg-btn')].forEach((x,i) => x.classList.toggle('on', i===0));
  loadEvents(true);
};
$('clear').onclick = async () => {
  if (!confirm('Effacer TOUT l\'historique et toutes les photos ? Cette action est définitive.')) return;
  const r = await fetch('/api/events', {method:'DELETE'});
  if (!r.ok) { toast((await r.json()).detail || 'Erreur'); return; }
  closeModal(); refreshHistory(); toast('Historique effacé');
};

async function refreshHistory() { await Promise.all([loadStats(), loadEvents(true)]); }

// ─── modal détail ────────────────────────────────────────────
function openModal(i) {
  const e = items[i]; if (!e) return;
  current = i;
  $('m-photo').src = e.photo_url || '';
  $('m-title').textContent = `${dayLabel(e.started_at)} à ${hhmm(e.started_at)}`;
  $('m-face').hidden = !e.crop_url;
  if (e.crop_url) $('m-face').src = e.crop_url;
  const rows = [
    ['Début', new Date(e.started_at*1000).toLocaleString('fr-FR')],
    ['Fin', hhmm(e.ended_at)],
    ['Durée', dur(e.duration)],
    ['Source', `${esc(e.source)} (${e.source_type})`],
    e.video_time != null ? ['Position vidéo', fmt(e.video_time)] : null,
    e.person ? ['Personne', `👤 ${esc(e.person)}`] : null,
    ['Visages (max)', e.faces],
    ['Confiance', `${Math.round(e.score*100)} %`],
    ['Alarme', e.alarm ? '🚨 activée — alerte envoyée' : 'désactivée'],
    e.frame_w ? ['Résolution', `${e.frame_w} × ${e.frame_h} px`] : null,
    e.face_w ? ['Taille visage', `${e.face_w} × ${e.face_h} px`] : null,
    ['ID', `#${e.id}`],
  ].filter(Boolean);
  $('m-dl').innerHTML = rows.map(([k,v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  $('m-dl-photo').href = e.photo_url || '#';
  $('m-dl-photo').download = `argus-${e.id}.jpg`;
  $('m-prev').disabled = i <= 0;
  $('m-next').disabled = i >= items.length - 1;
  $('modal').hidden = false;
}
function closeModal() { $('modal').hidden = true; current = -1; }
function step(d) { const n = current + d; if (n >= 0 && n < items.length) openModal(n); }
$('m-close').onclick = closeModal;
$('m-prev').onclick = () => step(-1);
$('m-next').onclick = () => step(1);
$('modal').onclick = e => { if (e.target === $('modal')) closeModal(); };
$('m-del').onclick = async () => {
  const e = items[current];
  if (!e || !confirm('Supprimer cette détection et sa photo ?')) return;
  const r = await fetch('/api/events/'+e.id, {method:'DELETE'});
  if (!r.ok) { toast((await r.json()).detail || 'Erreur'); return; }
  closeModal(); refreshHistory(); toast('Détection supprimée');
};

// ─── rafraîchissement auto historique ────────────────────────
setInterval(async () => {
  try {
    const s = await loadStats();
    if (lastTotal !== null && s.total !== lastTotal && tab === 'history' && $('modal').hidden)
      loadEvents(true);
    lastTotal = s.total;
  } catch {}
}, 4000);
loadStats().then(s => lastTotal = s.total).catch(() => {});

// ─── personnes ───────────────────────────────────────────────
async function loadPeople() {
  if (currentUser.role !== 'admin') return;
  let data;
  try { data = await (await fetch('/api/people')).json(); } catch { return; }
  $('people-unavail').hidden = data.available !== false;
  const people = data.items || [];
  $('people-count').hidden = !people.length;
  $('people-count').textContent = people.length;
  $('people-empty').hidden = people.length > 0;
  $('people-grid').innerHTML = people.map(p => `
    <div class="person-card" data-id="${p.id}">
      <img class="person-photo" src="${p.photo_url}" alt="${esc(p.name)}" loading="lazy">
      <div class="person-name">${esc(p.name)}</div>
      <button class="person-del" data-id="${p.id}" type="button">Supprimer</button>
    </div>`).join('');
}

$('people-grid').onclick = async e => {
  const btn = e.target.closest('.person-del');
  if (!btn || currentUser.role !== 'admin') return;
  if (!confirm('Supprimer cette personne de la base ?')) return;
  const r = await fetch('/api/people/'+btn.dataset.id, {method:'DELETE'});
  if (!r.ok) { toast((await r.json()).detail || 'Erreur'); return; }
  loadPeople(); toast('Personne supprimée');
};

$('person-photo').onchange = e => {
  $('person-photo-label').textContent = e.target.files[0]?.name || '';
};

$('add-person-form').onsubmit = async e => {
  e.preventDefault();
  if (currentUser.role !== 'admin') return;
  const name = $('person-name').value.trim();
  const file = $('person-photo').files[0];
  if (!name || !file) return;
  const msg = $('add-person-msg');
  msg.style.color = 'var(--muted)'; msg.textContent = 'Calcul de l\'embedding…';
  const fd = new FormData(); fd.append('name', name); fd.append('file', file);
  const r = await fetch(`/api/people?name=${encodeURIComponent(name)}`, {method:'POST', body:fd});
  if (!r.ok) {
    msg.style.color = 'var(--danger)';
    msg.textContent = (await r.json()).detail || 'Erreur';
    return;
  }
  msg.style.color = 'var(--ok)'; msg.textContent = `${name} ajouté ✓`;
  $('person-name').value = ''; $('person-photo').value = ''; $('person-photo-label').textContent = '';
  loadPeople();
  setTimeout(() => { msg.textContent = ''; }, 4000);
};

// ─── init ────────────────────────────────────────────────────
loadCurrentUser().then(() => showTab('live')).catch(() => showTab('live'));
