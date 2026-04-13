// 도원결의 (桃園結義) — Frontend Application
'use strict';

const state = {
  grants: [],
  stats: {},
  categories: [],
  filter: { q: '', category: '', status: '' },
  cal: { year: new Date().getFullYear(), month: new Date().getMonth() + 1, events: {}, selected: null },
  view: 'dashboard',
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ============================================================
// API
// ============================================================
async function fetchGrants() {
  const params = new URLSearchParams();
  if (state.filter.q) params.set('q', state.filter.q);
  if (state.filter.category) params.set('category', state.filter.category);
  if (state.filter.status) params.set('status', state.filter.status);
  const res = await fetch('/api/grants?' + params.toString());
  const data = await res.json();
  state.grants = data.grants;
  state.stats = data.stats;
  state.categories = data.categories;
  return data;
}

async function fetchCalendar(year, month) {
  const res = await fetch(`/api/calendar?year=${year}&month=${month}`);
  const data = await res.json();
  state.cal.year = year;
  state.cal.month = month;
  state.cal.events = data.events;
  return data;
}

async function refreshData() {
  await fetch('/api/refresh', { method: 'POST' });
}

// ============================================================
// Utilities
// ============================================================
function ddayBadge(dday, status) {
  if (status === '상시' || status === '상시접수') {
    return `<span class="badge dday-always"><span class="badge-dot"></span>상시</span>`;
  }
  if (dday === null || dday === undefined) return '';
  if (dday < 0) return `<span class="badge dday-done">마감</span>`;
  if (dday === 0) return `<span class="badge dday-urgent"><span class="badge-dot"></span>오늘 마감</span>`;
  if (dday <= 3) return `<span class="badge dday-urgent"><span class="badge-dot"></span>D-${dday}</span>`;
  if (dday <= 14) return `<span class="badge dday-soon">D-${dday}</span>`;
  return `<span class="badge dday-ok">D-${dday}</span>`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

function escape(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[m]));
}

function grantCardHTML(g) {
  const tags = [];
  if (g.category) tags.push(`<span class="tag">${escape(g.category)}</span>`);
  if (g.target && g.target.length < 20) tags.push(`<span class="tag">${escape(g.target)}</span>`);

  return `
    <div class="grant-card" data-id="${g.id}">
      <div class="grant-top">
        <div class="grant-org">${escape(g.organization || '—')}</div>
        ${ddayBadge(g.dday, g.status)}
      </div>
      <div class="grant-title">${escape(g.title)}</div>
      <div class="grant-meta">
        <div class="meta-item">
          <div class="meta-label">지원금액</div>
          <div class="meta-value amount">${escape(g.amount_display)}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">마감일</div>
          <div class="meta-value">${fmtDate(g.end_date)}</div>
        </div>
      </div>
      <div class="grant-bottom">
        <div class="grant-tags">${tags.join('')}</div>
      </div>
    </div>
  `;
}

// ============================================================
// Render
// ============================================================
function renderKPI() {
  const s = state.stats;
  $('#kpiAll').textContent = (s.all_count || 0).toLocaleString();
  $('#kpiOpen').textContent = (s.open_count || 0).toLocaleString();
  $('#kpiClosing').textContent = (s.closing_soon || 0).toLocaleString();
  $('#kpiBudget').textContent = s.total_budget_display || '—';
  if (s.updated) {
    const d = new Date(s.updated * 1000);
    $('#lastUpdated').textContent = d.toLocaleString('ko-KR');
  }
}

function renderDashboard() {
  const closing = state.grants
    .filter(g => g.dday !== null && g.dday >= 0 && g.dday <= 14)
    .sort((a, b) => a.dday - b.dday)
    .slice(0, 8);
  $('#closingGrid').innerHTML = closing.length
    ? closing.map(grantCardHTML).join('')
    : `<div class="kpi-card" style="grid-column: 1/-1; text-align:center; color:var(--ink-500)">마감 임박 공고가 없습니다.</div>`;

  const recent = state.grants
    .filter(g => g.status === '모집중' || g.status === '마감임박' || g.status === '상시')
    .slice(0, 6);
  $('#recentGrid').innerHTML = recent.length
    ? recent.map(grantCardHTML).join('')
    : `<div class="kpi-card" style="grid-column: 1/-1; text-align:center; color:var(--ink-500)">데이터를 불러오는 중입니다.</div>`;
}

function renderList() {
  const list = state.grants;
  $('#listCount').textContent = `총 ${list.length.toLocaleString()}건의 공고가 있습니다.`;
  $('#listGrid').innerHTML = list.length
    ? list.map(grantCardHTML).join('')
    : `<div class="kpi-card" style="grid-column: 1/-1; text-align:center; color:var(--ink-500); padding: 40px">조건에 맞는 공고가 없습니다.</div>`;

  const filterCat = $('#filterCategory');
  const existing = new Set(Array.from(filterCat.querySelectorAll('.chip')).map(c => c.dataset.value));
  state.categories.forEach(cat => {
    if (!existing.has(cat)) {
      const b = document.createElement('button');
      b.className = 'chip';
      b.dataset.value = cat;
      b.textContent = cat;
      filterCat.appendChild(b);
    }
  });
}

function renderCalendar() {
  const { year, month, events } = state.cal;
  $('#calTitle').textContent = `${year}년 ${month}월`;

  const grid = $('#calGrid');
  const dows = ['일', '월', '화', '수', '목', '금', '토'];
  let html = dows.map(d => `<div class="cal-dow">${d}</div>`).join('');

  const first = new Date(year, month - 1, 1);
  const firstDow = first.getDay();
  const daysInMonth = new Date(year, month, 0).getDate();

  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

  for (let i = 0; i < firstDow; i++) {
    html += `<div class="cal-day empty"></div>`;
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const dayOfWeek = new Date(year, month - 1, d).getDay();
    const cls = [
      'cal-day',
      dayOfWeek === 0 ? 'sunday' : '',
      dayOfWeek === 6 ? 'saturday' : '',
      iso === todayStr ? 'today' : '',
      state.cal.selected === iso ? 'selected' : '',
    ].filter(Boolean).join(' ');

    const dayEvents = events[iso] || [];
    let eventHtml = '';
    const maxShow = 3;
    dayEvents.slice(0, maxShow).forEach(e => {
      const eCls = e.dday !== null && e.dday <= 3 ? 'urgent' : e.dday !== null && e.dday <= 7 ? 'soon' : '';
      eventHtml += `<div class="cal-event ${eCls}" title="${escape(e.title)}">${escape(e.title)}</div>`;
    });
    if (dayEvents.length > maxShow) {
      eventHtml += `<div class="cal-event-count">+${dayEvents.length - maxShow}건 더</div>`;
    }

    html += `<div class="${cls}" data-date="${iso}"><div class="cal-day-num">${d}</div>${eventHtml}</div>`;
  }

  grid.innerHTML = html;

  grid.querySelectorAll('.cal-day:not(.empty)').forEach(el => {
    el.addEventListener('click', () => {
      state.cal.selected = el.dataset.date;
      renderCalendar();
      renderCalendarAside(el.dataset.date);
    });
  });

  if (state.cal.selected) {
    renderCalendarAside(state.cal.selected);
  }
}

function renderCalendarAside(dateIso) {
  const events = state.cal.events[dateIso] || [];
  const aside = $('#calAside');
  const d = new Date(dateIso);
  const label = `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일`;

  if (!events.length) {
    aside.innerHTML = `
      <h3 class="cal-aside-title">${label}</h3>
      <p class="cal-aside-sub">마감 공고가 없습니다.</p>
      <div class="cal-aside-empty">
        <div class="cal-aside-icon">🎯</div>
        <p>다른 날짜를 선택해보세요.</p>
      </div>
    `;
    return;
  }

  const items = events.map(e => `
    <div class="cal-aside-item" data-id="${e.id}">
      <div class="cal-aside-item-org">${escape(e.organization || '—')}</div>
      <div class="cal-aside-item-title">${escape(e.title)}</div>
      <div class="cal-aside-item-meta">
        ${ddayBadge(e.dday, e.status)}
        <span class="cal-aside-item-amount">💰 ${escape(e.amount_display)}</span>
      </div>
    </div>
  `).join('');

  aside.innerHTML = `
    <h3 class="cal-aside-title">${label}</h3>
    <p class="cal-aside-sub">총 ${events.length}건의 공고가 이날 마감됩니다.</p>
    ${items}
  `;

  aside.querySelectorAll('.cal-aside-item').forEach(el => {
    el.addEventListener('click', () => openDetail(el.dataset.id));
  });
}

// ============================================================
// Modal
// ============================================================
function openDetail(id) {
  const g = state.grants.find(x => x.id === id);
  if (!g) return;

  const ddayText = (() => {
    if (g.status === '상시') return '상시접수';
    if (g.dday === null) return '—';
    if (g.dday < 0) return '마감';
    if (g.dday === 0) return '오늘 마감';
    return `D-${g.dday}`;
  })();
  const ddayClass = g.dday !== null && g.dday <= 3 ? 'urgent' : 'emphasis';

  $('#modalBody').innerHTML = `
    <div class="modal-kicker">${escape(g.organization || '기관 정보 없음')}</div>
    <h2 class="modal-title">${escape(g.title)}</h2>

    <div class="modal-stats">
      <div class="modal-stat">
        <div class="modal-stat-label">지원금액</div>
        <div class="modal-stat-value emphasis">${escape(g.amount_display)}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">남은 기간</div>
        <div class="modal-stat-value ${ddayClass}">${escape(ddayText)}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">접수 기간</div>
        <div class="modal-stat-value" style="font-size:14px">${escape(g.period_raw || '—')}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">지원 분야</div>
        <div class="modal-stat-value" style="font-size:14px">${escape(g.category || '—')}</div>
      </div>
    </div>

    ${g.description ? `
      <div class="modal-section-title">사업 개요</div>
      <p class="modal-desc">${escape(g.description.slice(0, 400))}${g.description.length > 400 ? '...' : ''}</p>
    ` : ''}

    ${g.target ? `
      <div class="modal-section-title">지원 대상</div>
      <p class="modal-desc">${escape(g.target)}</p>
    ` : ''}

    ${g.hashtags ? `
      <div class="modal-section-title">해시태그</div>
      <div class="modal-tags">
        ${g.hashtags.split(',').map(t => t.trim()).filter(Boolean).map(t => `<span class="tag">#${escape(t)}</span>`).join('')}
      </div>
    ` : ''}

    <div class="modal-cta">
      ${g.apply_url ? `<a href="${escape(g.apply_url)}" target="_blank" class="btn btn-primary">📝 신청하러 가기</a>` : ''}
      ${g.link ? `<a href="${escape(g.link)}" target="_blank" class="btn btn-outline">📄 공고 원문 보기</a>` : ''}
    </div>
  `;
  $('#detailModal').classList.remove('hidden');
}

function closeDetail() {
  $('#detailModal').classList.add('hidden');
}

// ============================================================
// View switching
// ============================================================
function switchView(name) {
  state.view = name;
  $$('.view').forEach(v => v.classList.add('hidden'));
  $(`#view-${name}`).classList.remove('hidden');
  $$('.nav-item').forEach(i => i.classList.toggle('active', i.dataset.view === name));
  if (name === 'calendar') {
    fetchCalendar(state.cal.year, state.cal.month).then(renderCalendar);
  }
  if (name === 'list') {
    renderList();
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ============================================================
// Event Wiring
// ============================================================
function bindEvents() {
  $$('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
      e.preventDefault();
      switchView(item.dataset.view);
    });
  });
  $$('.link-more').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      switchView(el.dataset.view);
    });
  });

  $('#searchBtn').addEventListener('click', async () => {
    state.filter.q = $('#searchInput').value.trim();
    await fetchGrants();
    renderKPI();
    renderDashboard();
    renderList();
    if (state.filter.q) switchView('list');
  });
  $('#searchInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') $('#searchBtn').click();
  });

  // Filter chips
  $('#filterStatus').addEventListener('click', async e => {
    if (e.target.classList.contains('chip')) {
      $$('#filterStatus .chip').forEach(c => c.classList.remove('active'));
      e.target.classList.add('active');
      state.filter.status = e.target.dataset.value;
      await fetchGrants();
      renderList();
    }
  });
  $('#filterCategory').addEventListener('click', async e => {
    if (e.target.classList.contains('chip')) {
      $$('#filterCategory .chip').forEach(c => c.classList.remove('active'));
      e.target.classList.add('active');
      state.filter.category = e.target.dataset.value;
      await fetchGrants();
      renderList();
    }
  });

  // Calendar nav
  $('#calPrev').addEventListener('click', async () => {
    let y = state.cal.year, m = state.cal.month - 1;
    if (m < 1) { m = 12; y--; }
    state.cal.selected = null;
    await fetchCalendar(y, m);
    renderCalendar();
    $('#calAside').innerHTML = `<div class="cal-aside-empty"><div class="cal-aside-icon">🗓️</div><p>날짜를 선택하면<br/>해당일 마감 공고가 표시됩니다.</p></div>`;
  });
  $('#calNext').addEventListener('click', async () => {
    let y = state.cal.year, m = state.cal.month + 1;
    if (m > 12) { m = 1; y++; }
    state.cal.selected = null;
    await fetchCalendar(y, m);
    renderCalendar();
    $('#calAside').innerHTML = `<div class="cal-aside-empty"><div class="cal-aside-icon">🗓️</div><p>날짜를 선택하면<br/>해당일 마감 공고가 표시됩니다.</p></div>`;
  });
  $('#calToday').addEventListener('click', async () => {
    const now = new Date();
    state.cal.selected = null;
    await fetchCalendar(now.getFullYear(), now.getMonth() + 1);
    renderCalendar();
  });

  // Card clicks (delegated)
  document.body.addEventListener('click', e => {
    const card = e.target.closest('.grant-card');
    if (card) openDetail(card.dataset.id);
  });

  // Modal close
  $$('#detailModal [data-close]').forEach(el => el.addEventListener('click', closeDetail));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetail(); });

  // Refresh button
  $('#refreshBtn').addEventListener('click', async () => {
    const btn = $('#refreshBtn');
    btn.classList.add('spinning');
    await refreshData();
    setTimeout(async () => {
      await fetchGrants();
      renderKPI();
      renderDashboard();
      renderList();
      if (state.view === 'calendar') {
        await fetchCalendar(state.cal.year, state.cal.month);
        renderCalendar();
      }
      btn.classList.remove('spinning');
    }, 2500);
  });
}

// ============================================================
// Init
// ============================================================
(async function init() {
  $('#loader').classList.remove('hidden');
  bindEvents();
  try {
    await fetchGrants();
    renderKPI();
    renderDashboard();
    renderList();
  } catch (e) {
    console.error(e);
  } finally {
    $('#loader').classList.add('hidden');
  }
})();
