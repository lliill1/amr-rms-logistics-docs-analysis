// DocAnalyzer AMR/RMS — веб-интерфейс. Статический, без сборки: все файлы лежат в web/.
import { app, loadMeta, refreshStatus } from './app-state.js';
import { button, codeBlock, emptyState } from './components.js';
import { copyText, h, mount } from './dom.js';
import { icon } from './icons.js';
import { JOB_LABELS, JOB_PAGES, jobEvents, jobPercent, jobs, resume } from './jobs.js';
import { closeOverlay } from './overlay.js';
import { createArchivePage } from './pages/archive.js';
import { createAskPage } from './pages/ask.js';
import { createFactsPage } from './pages/facts.js';
import { createReportPage } from './pages/report.js';
import { createSystemsPage } from './pages/systems.js';
import { getTheme, toggleTheme } from './theme.js';
import { toast } from './toast.js';

const ROUTES = [
  { path: '/archive', label: 'Архив и обработка', create: createArchivePage },
  { path: '/ask', label: 'Вопросы к архиву', create: createAskPage },
  { path: '/facts', label: 'Факты и ревью', create: createFactsPage },
  { path: '/systems', label: 'Логистика и системы', create: createSystemsPage },
  { path: '/report', label: 'Отчёт', create: createReportPage },
];

// ───────── шапка ─────────
function brandMark() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('width', '20');
  svg.setAttribute('height', '20');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = '<g fill="#fff"><rect x="3" y="4" width="2.4" height="12" rx="1.2"/><rect x="7.4" y="7" width="2.4" height="9" rx="1.2" opacity=".85"/><rect x="11.8" y="3" width="2.4" height="13" rx="1.2"/><rect x="16.2" y="9" width="1.6" height="7" rx=".8" opacity=".7"/></g>';
  return svg;
}

const nav = h('nav', { class: 'pillnav', 'aria-label': 'Разделы' },
  ROUTES.map((r) => h('a', { class: 'pillnav__item', href: `#${r.path}`, dataset: { path: r.path } }, r.label)));

// статус Ollama
const statusDot = h('span', { class: 'statuschip__dot' });
const statusLabel = h('span');
const statusModel = h('span', { class: 'statuschip__model' });
const statusBtn = h('button', { class: 'statuschip', type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': 'false', onClick: () => (popover ? closePopover() : openPopover()) },
  statusDot, statusLabel, statusModel);
let popover = null;
const statusWrap = h('div', { style: { position: 'relative' } }, statusBtn);

function renderStatusChip() {
  const s = app.get().status;
  statusBtn.className = 'statuschip';
  statusModel.textContent = '';
  if (!s) { statusLabel.textContent = 'Проверяю Ollama…'; return; }
  if (s.state === 'ok') {
    statusBtn.classList.add('statuschip--ok');
    statusLabel.textContent = 'Ollama';
    statusModel.textContent = s.chat_model || '';
  } else if (s.state === 'unreachable') {
    statusBtn.classList.add('statuschip--bad');
    statusLabel.textContent = 'Ollama не отвечает';
  } else {
    statusBtn.classList.add('statuschip--warn');
    statusLabel.textContent = 'Не хватает моделей';
  }
}

function popoverContent() {
  const s = app.get().status;
  const body = [];
  if (!s) return [h('p', { class: 'muted' }, 'Проверяю…')];
  if (s.state === 'ok') {
    body.push(h('div', { class: 'card__title' }, 'Ollama работает'),
      h('dl', null, h('dt', null, 'LLM'), h('dd', null, s.chat_model), h('dt', null, 'Эмбеддинги'), h('dd', null, s.embed_model)));
  } else if (s.state === 'unreachable') {
    body.push(h('div', { class: 'card__title' }, 'Ollama не отвечает'),
      h('p', { class: 'muted', style: { marginTop: '8px' } }, 'Запустите Ollama (значок в трее Windows) и нажмите «Проверить снова».'),
      h('p', { class: 'field__hint', style: { marginTop: '8px' } }, 'Без модели работают загрузка, каталог и поиск по словам.'),
      s.error && h('p', { class: 'field__hint', style: { marginTop: '8px', wordBreak: 'break-word' } }, s.error));
  } else {
    body.push(h('div', { class: 'card__title' }, 'Не скачаны модели'),
      h('p', { class: 'muted', style: { margin: '8px 0 12px' } }, s.missing.join(', ')),
      h('div', { class: 'stack', style: { gap: '8px' } }, s.pull_commands.map((c) => codeBlock(c, async () => toast(await copyText(c) ? 'Команда скопирована' : 'Не удалось скопировать', { type: 'info' })))));
  }
  const again = button({
    label: 'Проверить снова', icon: 'refresh-cw', variant: 'secondary', size: 'sm',
    onClick: async () => {
      again.disabled = true;
      try { await refreshStatus(true); } catch (e) { toast(e.message, { type: 'error' }); }
      again.disabled = false;
    },
  });
  body.push(h('div', { class: 'row row--between', style: { marginTop: '16px', gap: '12px' } },
    h('span', { class: 'field__hint' }, 'Документы обрабатываются только на этом компьютере.'), again));
  return body;
}

function openPopover() {
  popover = h('div', { class: 'popover', role: 'dialog', 'aria-label': 'Статус Ollama', style: { right: 0, top: 'calc(100% + 10px)' } }, popoverContent());
  statusWrap.appendChild(popover);
  statusBtn.setAttribute('aria-expanded', 'true');
  setTimeout(() => {
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', onEsc);
  });
}
function closePopover() {
  popover?.remove();
  popover = null;
  statusBtn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('mousedown', outside);
  document.removeEventListener('keydown', onEsc);
}
const outside = (e) => { if (popover && !statusWrap.contains(e.target)) closePopover(); };
const onEsc = (e) => { if (e.key === 'Escape') { closePopover(); statusBtn.focus(); } };

// тема
const themeBtn = h('button', { class: 'iconbtn', type: 'button', onClick: () => { toggleTheme(); renderThemeBtn(); } });
function renderThemeBtn() {
  const dark = getTheme() === 'dark';
  themeBtn.title = dark ? 'Включить светлую тему' : 'Включить тёмную тему';
  themeBtn.setAttribute('aria-label', themeBtn.title);
  mount(themeBtn, icon(dark ? 'sun' : 'moon', 18));
}

// активная фоновая задача
const jobChip = h('a', { class: 'jobchip hidden' });
function renderJobChip() {
  const j = jobs.get().job;
  if (!j || j.state !== 'running') { jobChip.classList.add('hidden'); return; }
  jobChip.classList.remove('hidden');
  jobChip.href = JOB_PAGES[j.kind] || '#/archive';
  const p = jobPercent(j);
  const sp = icon('loader-circle', 17);
  sp.classList.add('spin');
  mount(jobChip, sp, h('span', null, JOB_LABELS[j.kind] || 'Задача'), p ? h('span', { class: 'jobchip__pct' }, `${Math.round(p * 100)}%`) : null);
}

const view = h('main', { class: 'view', id: 'view', tabIndex: -1 });
const shell = h('div', { class: 'app' },
  h('header', { class: 'topbar' },
    h('a', { class: 'brand', href: '#/archive', 'aria-label': 'DocAnalyzer AMR/RMS — на главную' },
      h('span', { class: 'brand__mark' }, brandMark()),
      h('span', { class: 'brand__text' }, h('span', { class: 'brand__name' }, 'DocAnalyzer'), h('span', { class: 'brand__sub' }, 'AMR / RMS'))),
    nav,
    h('div', { class: 'topbar__right' }, jobChip, statusWrap, themeBtn)),
  view,
  h('footer', { class: 'foot' }, icon('badge-check', 15), 'Документы обрабатываются только на этом компьютере'));

// ───────── роутер ─────────
let current = null;

function parseHash() {
  const raw = location.hash.slice(1) || '/archive';
  const [path, qs] = raw.split('?');
  return { path, query: new URLSearchParams(qs || '') };
}

function render() {
  const { path, query } = parseHash();
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  closeOverlay();
  closePopover();
  current?.destroy?.();
  nav.querySelectorAll('.pillnav__item').forEach((a) => {
    const on = a.dataset.path === route.path;
    a.classList.toggle('is-active', on);
    on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current');
  });
  document.title = `${route.label} · DocAnalyzer AMR/RMS`;
  current = route.create({ query });
  mount(view, current.el);
  view.classList.remove('view'); // перезапуск анимации появления
  void view.offsetWidth;
  view.classList.add('view');
  window.scrollTo({ top: 0 });
}

function showFatal(err) {
  const retry = button({ label: 'Повторить', icon: 'refresh-cw', variant: 'primary', onClick: () => location.reload() });
  mount(view, h('section', { class: 'card' }, emptyState({
    icon: 'triangle-alert', title: 'Нет связи с сервером',
    text: `${err.message} Запустите run_web.bat или команду python server.py и обновите страницу.`, action: retry,
  })));
}

// ───────── запуск ─────────
async function boot() {
  document.getElementById('app').replaceWith(shell);
  renderThemeBtn();
  renderStatusChip();
  renderJobChip();

  app.subscribe(() => { renderStatusChip(); if (popover) mount(popover, ...popoverContent()); });
  jobs.subscribe(renderJobChip);
  jobEvents.on('finish', (job) => {
    const label = JOB_LABELS[job.kind] || 'Задача';
    if (job.state === 'error') {
      toast(`${label}: ${job.error || 'ошибка'}`, { type: 'error' });
      return;
    }
    const here = '#' + parseHash().path;
    const target = (JOB_PAGES[job.kind] || '').split('?')[0];
    if (target && target !== here) toast(`${label}: готово`, { action: { label: 'Открыть', href: JOB_PAGES[job.kind] } });
    refreshStatus().catch(() => {});
  });

  try {
    await Promise.all([loadMeta(), refreshStatus()]);
  } catch (e) {
    showFatal(e);
    return;
  }
  resume();
  window.addEventListener('hashchange', render);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatus().catch(() => {}); });
  setInterval(() => refreshStatus().catch(() => {}), 60000);
  render();
}

boot();
