// Версия 2. Общее для ревью фактов (процессы, обмены, ответственность):
// цитата с проверкой по источнику, боковая панель с правкой, загрузка схем в SVG.
import { api } from './api.js';
import { app } from './app-state.js';
import { button, emptyState, field, selectEl, skeleton, ticks, withBusy } from './components.js';
import { h, mount } from './dom.js';
import { pct } from './format.js';
import { icon } from './icons.js';
import { openDrawer } from './overlay.js';
import { toast, toastError } from './toast.js';

export const meta = () => app.get().meta || {};
export const statusLabel = (s) => meta().requirement_statuses?.[s] || s;
export const statusOptions = () => Object.entries(meta().requirement_statuses || {}).map(([value, label]) => ({ value, label }));
export const systemOptions = () => (meta().systems || []).map((s) => ({ value: s, label: s }));

/** Цитата и весь фрагмент документа с подсветкой. */
export function quoteSection(r) {
  const score = r.quote_score ?? 0;
  return h('section', null,
    h('div', { class: 'row row--between', style: { marginBottom: '8px' } },
      h('div', { class: 'row', style: { gap: '8px' } }, icon('quote', 16), h('strong', null, 'Цитата из документа')),
      h('span', { class: 'row muted', style: { gap: '10px', fontSize: '12.5px' }, title: 'Насколько точно цитата совпадает с текстом документа' },
        'совпадение', ticks(score, { count: 12, xs: true, tone: score >= 0.9 ? 'ok' : 'warn' }), h('strong', { class: 'num' }, pct(score)))),
    h('blockquote', { class: 'quote' }, r.quote || '—'),
    r.chunk_text && h('details', { class: 'disclosure', style: { marginTop: '12px' } },
      h('summary', { style: { fontSize: '13px' } }, 'Весь фрагмент документа', icon('chevron-down', 16)),
      h('div', { class: 'fragment' }, highlight(r.chunk_text, r.quote))));
}

function highlight(text, quote) {
  const q = (quote || '').trim();
  const i = q ? text.indexOf(q) : -1;
  if (i < 0) return text;
  return [text.slice(0, i), h('mark', null, q), text.slice(i + q.length)];
}

export function textInput(value, onInput, placeholder = '') {
  const el = h('input', { class: 'input', type: 'text', placeholder, autocomplete: 'off', onInput: (e) => onInput(e.target.value) });
  el.value = value || '';
  return el;
}

export function textArea(value, onInput, { rows = 3, placeholder = '' } = {}) {
  const el = h('textarea', { class: 'textarea', rows, placeholder, onInput: (e) => onInput(e.target.value) });
  el.value = value || '';
  return el;
}

/**
 * Боковая панель проверки факта.
 *   endpoint — '/processes' | '/interfaces' | '/responsibilities'
 *   ids()    — порядок для «предыдущий / следующий»
 *   title(r) — заголовок
 *   fields(r, form, sync) — элементы формы; form — изменяемые поля (ключи как в API)
 *   top(r)   — что показать над цитатой (например, схему процесса)
 *   onSaved(updated) — после сохранения
 */
export function openFactDrawer({ endpoint, id, ids, title, fields, top, onSaved, onClose }) {
  let currentId = id;
  const drawer = openDrawer({ title: '…', subtitle: 'Проверка по источнику', body: [h('div', { class: 'stack' }, skeleton('100%', 60), skeleton('100%', 140))],
    onClose: () => onClose?.() });
  show(id);

  async function show(nextId) {
    currentId = nextId;
    drawer.setBody(h('div', { class: 'stack' }, skeleton('100%', 60), skeleton('100%', 140)));
    drawer.setFooter();
    drawer.setHeaderExtra();
    let r;
    try { r = await api.get(`${endpoint}/${nextId}`); } catch (e) { toastError(e); drawer.close(); return; }
    if (currentId !== nextId) return;
    draw(r);
  }

  function draw(r) {
    const form = {};
    const initial = {};
    const sync = () => {
      const dirty = Object.keys(form).some((k) => JSON.stringify(form[k] ?? '') !== JSON.stringify(initial[k] ?? ''));
      const list = ids();
      const i = list.indexOf(r.id);
      counter.textContent = i >= 0 ? `${i + 1} из ${list.length}` : '';
      saveBtn.disabled = !dirty;
      prev.disabled = dirty || i <= 0;
      next.disabled = dirty || i < 0 || i >= list.length - 1;
      prev.title = next.title = dirty ? 'Сначала сохраните или отмените правки' : '';
    };
    const track = (key, value) => { initial[key] = value; form[key] = value; };
    const statusSel = selectEl({ options: statusOptions(), value: r.status, onChange: (v) => { form.status = v; sync(); } });
    track('status', r.status);
    const commentArea = textArea(r.comment, (v) => { form.comment = v; sync(); }, { rows: 2, placeholder: 'Заметка для себя или коллег' });
    track('comment', r.comment || '');

    const saveBtn = button({ label: 'Сохранить', icon: 'check', variant: 'primary', disabled: true, onClick: () => withBusy(saveBtn, () => save()) });
    const confirmBtn = button({ label: 'Подтвердить', icon: 'badge-check', variant: 'soft', onClick: () => withBusy(confirmBtn, () => save('confirmed')) });
    const rejectBtn = button({ label: 'Отклонить', icon: 'circle-x', variant: 'danger', onClick: () => withBusy(rejectBtn, () => save('rejected')) });
    const prev = button({ iconOnly: true, icon: 'chevron-left', variant: 'ghost', size: 'sm', label: 'Предыдущий', onClick: () => go(-1) });
    const next = button({ iconOnly: true, icon: 'chevron-right', variant: 'ghost', size: 'sm', label: 'Следующий', onClick: () => go(1) });
    const counter = h('span', { class: 'muted num', style: { fontSize: '12.5px', minWidth: '54px', textAlign: 'center' } });

    function go(step) {
      const list = ids();
      const i = list.indexOf(r.id) + step;
      if (i >= 0 && i < list.length) show(list[i]);
    }

    async function save(forceStatus) {
      const patch = {};
      for (const k of Object.keys(form)) if (JSON.stringify(form[k] ?? '') !== JSON.stringify(initial[k] ?? '')) patch[k] = form[k];
      if (patch.comment === '') patch.comment = null;
      if (forceStatus && r.status !== forceStatus) patch.status = forceStatus;
      if (!Object.keys(patch).length) { if (forceStatus) go(1); return; }
      try {
        const updated = await api.patch(`${endpoint}/${r.id}`, patch);
        toast(forceStatus === 'confirmed' ? 'Подтверждено' : forceStatus === 'rejected' ? 'Отклонено' : 'Сохранено');
        await onSaved?.(updated);
        const list = ids();
        const i = list.indexOf(r.id);
        if (forceStatus && i >= 0 && i < list.length - 1) show(list[i + 1]); else draw(updated);
      } catch (e) { toastError(e); }
    }

    drawer.setTitle(`${r.code} · ${statusLabel(r.status)}`, r.source + (r.is_current === false ? ' · устаревшая редакция' : ''));
    drawer.setBody(
      top?.(r),
      h('div', { class: 'stack', style: { gap: '18px' } },
        quoteSection(r),
        h('div', { class: 'form-grid' },
          fields(r, form, sync, track),
          field('Статус', statusSel),
          h('div', { class: 'span-2' }, field('Комментарий', commentArea)))));
    drawer.setHeaderExtra(prev, counter, next);
    drawer.setFooter(rejectBtn, h('span', { class: 'spacer' }), confirmBtn, saveBtn);
    sync();
  }
  return drawer;
}

/** Загружает схему (SVG) с сервера и вставляет в контейнер. 404 — понятная пустая заглушка. */
export async function loadDiagram(host, url, { emptyTitle = 'Схемы пока нет', emptyAction, wide = false } = {}) {
  mount(host, skeleton('100%', 260));
  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'image/svg+xml' } });
  } catch {
    mount(host, emptyState({ icon: 'triangle-alert', title: 'Нет связи с сервером' }));
    return;
  }
  if (res.status === 404) {
    let text = '';
    try { text = (await res.json()).detail; } catch { /* ignore */ }
    mount(host, emptyState({ icon: 'network', title: emptyTitle, text, action: emptyAction }));
    return;
  }
  if (!res.ok) { mount(host, emptyState({ icon: 'triangle-alert', title: `Ошибка сервера (${res.status})` })); return; }
  const svgText = await res.text();
  const note = decodeURIComponent(res.headers.get('X-Diagram-Note') || '');
  const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  const svg = doc.documentElement;
  if (svg.nodeName !== 'svg') { mount(host, emptyState({ icon: 'triangle-alert', title: 'Не удалось показать схему' })); return; }
  const wrap = h('div', { class: wide ? 'diagram diagram--wide' : 'diagram' });
  wrap.appendChild(document.importNode(svg, true));
  mount(host, wrap, note && h('p', { class: 'diagram__note' }, note));
}

export const extractAction = () => h('a', { class: 'btn btn--soft', href: '#/facts' }, icon('sparkles', 17), 'К извлечению фактов');
