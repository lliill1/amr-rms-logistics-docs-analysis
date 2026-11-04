// Общие элементы интерфейса.
import { h } from './dom.js';
import { icon } from './icons.js';
import { fmtDuration } from './format.js';
import { JOB_LABELS } from './jobs.js';

export const cls = (...a) => a.filter(Boolean).join(' ');

/** Кнопка: button({label, icon, variant, size, onClick, disabled, title}) */
export function button({ label, icon: ic, variant = 'secondary', size, onClick, disabled, title, type = 'button', block, iconOnly, ariaLabel } = {}) {
  return h('button', {
    class: cls('btn', `btn--${variant}`, size && `btn--${size}`, block && 'btn--block', iconOnly && 'btn--icon'),
    type, onClick, disabled, title, 'aria-label': ariaLabel || (iconOnly ? label : null),
  }, ic && icon(ic, size === 'sm' ? 15 : 17), !iconOnly && label);
}

/** Выполняет async-действие и блокирует кнопку со спиннером, пока оно идёт. */
export async function withBusy(btn, fn) {
  if (btn.dataset.busy) return;
  btn.dataset.busy = '1';
  const prev = btn.innerHTML;
  const wasDisabled = btn.disabled;
  btn.disabled = true;
  const sp = icon('loader-circle', 16);
  sp.classList.add('spin');
  btn.prepend(sp);
  try {
    return await fn();
  } finally {
    delete btn.dataset.busy;
    if (btn.isConnected) { btn.innerHTML = prev; btn.disabled = wasDisabled; }
  }
}

export function pill(text, tone, { dot = true, sm = false, title } = {}) {
  return h('span', { class: cls('pill', tone && `pill--${tone}`, dot && 'pill--dot', sm && 'pill--sm'), title }, text);
}

export function tag(text, accent) {
  return h('span', { class: cls('tag', accent && 'tag--accent') }, text);
}

/** «Штрихкод» из макета: доля заполненных тиков. */
export function ticks(ratio, { count = 28, tone, xs } = {}) {
  const on = Math.round(Math.max(0, Math.min(1, ratio || 0)) * count);
  const el = h('div', { class: cls('ticks', tone && `ticks--${tone}`, xs && 'ticks--xs'), 'aria-hidden': 'true' });
  for (let i = 0; i < count; i++) el.appendChild(h('i', { class: i < on ? 'on' : '' }));
  return el;
}

/** Уровень релевантности 0–3 */
export function level(n) {
  const v = Number(n) || 0;
  return h('span', { class: 'level', title: `AMR/RMS: ${v} из 3 (0 — не относится, 3 — ключевой)` },
    h('span', { class: 'level__bars' }, [1, 2, 3].map((i) => h('i', { class: i <= v ? 'on' : '' }))),
    h('span', { class: 'level__n' }, String(v)));
}

export function notice({ tone, icon: ic, title, body, actions } = {}) {
  const iconName = ic || { info: 'info', success: 'circle-check', warning: 'triangle-alert', danger: 'circle-alert' }[tone] || 'info';
  return h('div', { class: cls('notice', tone && `notice--${tone}`), role: tone === 'danger' ? 'alert' : null },
    icon(iconName, 18),
    h('div', { class: 'notice__body' }, title && h('div', { class: 'notice__title' }, title), body),
    actions);
}

export function emptyState({ icon: ic = 'archive', title, text, action } = {}) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty__icon' }, icon(ic, 26, 1.6)),
    h('div', { class: 'empty__title' }, title),
    text && h('p', { class: 'empty__text' }, text),
    action);
}

export function field(label, control, hint) {
  return h('label', { class: 'field' },
    label && h('span', { class: 'field__label' }, label),
    control,
    hint && h('span', { class: 'field__hint' }, hint));
}

export function selectEl({ options, value, onChange, small, id, ariaLabel, disabled }) {
  const sel = h('select', { class: cls('select', small && 'select--sm'), id, 'aria-label': ariaLabel, disabled, onChange: (e) => onChange?.(e.target.value) },
    options.map((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      const el = h('option', { value: opt.value }, opt.label);
      if (opt.value === value) el.selected = true;
      return el;
    }));
  return sel;
}

export function searchBox({ placeholder, value = '', onInput, round, ariaLabel }) {
  const input = h('input', { class: 'input', type: 'search', placeholder, value, 'aria-label': ariaLabel || placeholder, autocomplete: 'off', onInput: (e) => onInput?.(e.target.value) });
  return h('div', { class: cls('searchbox', round && 'searchbox--round') }, icon('search', 17), input);
}

/** Сегментированный переключатель (radiogroup). */
export function segmented({ options, value, onChange, label, block }) {
  const btns = new Map();
  let current = value;
  const root = h('div', { class: cls('segmented', block && 'segmented--block'), role: 'radiogroup', 'aria-label': label },
    options.map((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      const b = h('button', {
        class: 'segmented__btn', type: 'button', role: 'radio', 'aria-checked': String(opt.value === current),
        title: opt.title,
        onClick: () => { set(opt.value); onChange?.(opt.value); },
      }, opt.label);
      btns.set(opt.value, b);
      return b;
    }));
  function set(v) {
    current = v;
    btns.forEach((b, k) => b.setAttribute('aria-checked', String(k === v)));
  }
  root.set = set;
  root.value = () => current;
  return root;
}

/** Вкладки (tablist) в виде сегментированного переключателя. */
export function tabs({ items, value, onChange, label }) {
  const btns = new Map();
  const root = h('div', { class: 'segmented', role: 'tablist', 'aria-label': label },
    items.map((it) => {
      const b = h('button', {
        class: 'segmented__btn', type: 'button', role: 'tab', 'aria-selected': String(it.value === value),
        onClick: () => { root.set(it.value); onChange?.(it.value); },
      }, it.label, it.count != null && h('span', { class: 'chip__count', style: { marginLeft: '8px' } }, String(it.count)));
      btns.set(it.value, { b, it });
      return b;
    }));
  root.set = (v) => btns.forEach(({ b, it }) => b.setAttribute('aria-selected', String(it.value === v)));
  root.setCount = (v, n) => {
    const entry = btns.get(v);
    const c = entry?.b.querySelector('.chip__count');
    if (c) c.textContent = String(n);
  };
  return root;
}

export function switchEl({ label, checked, onChange, id }) {
  const input = h('input', { type: 'checkbox', checked: !!checked, id, onChange: (e) => onChange?.(e.target.checked) });
  return h('label', { class: 'switch' }, input, h('span', { class: 'switch__track' }), h('span', null, label));
}

export function checkEl({ label, checked, onChange }) {
  const input = h('input', { type: 'checkbox', checked: !!checked, onChange: (e) => onChange?.(e.target.checked) });
  return h('label', { class: 'check' }, input, h('span', null, label));
}

/** Чипы-фильтры с мультивыбором. */
export function chipGroup({ options, selected, onChange, label }) {
  const sel = new Set(selected);
  const btns = new Map();
  const root = h('div', { class: 'chips', role: 'group', 'aria-label': label },
    options.map((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      const b = h('button', {
        class: 'chip', type: 'button', 'aria-pressed': String(sel.has(opt.value)),
        onClick: () => {
          sel.has(opt.value) ? sel.delete(opt.value) : sel.add(opt.value);
          b.setAttribute('aria-pressed', String(sel.has(opt.value)));
          onChange?.([...sel]);
        },
      }, opt.label, opt.count != null && h('span', { class: 'chip__count' }, String(opt.count)));
      btns.set(opt.value, b);
      return b;
    }));
  root.setCounts = (counts) => btns.forEach((b, k) => {
    let c = b.querySelector('.chip__count');
    if (counts[k] == null) { c?.remove(); return; }
    if (!c) { c = h('span', { class: 'chip__count' }); b.appendChild(c); }
    c.textContent = String(counts[k]);
  });
  return root;
}

export function skeleton(w = '100%', hgt = 14) {
  return h('div', { class: 'skeleton', style: { width: w, height: `${hgt}px` } });
}

export function skeletonRows(n = 5) {
  return h('div', { class: 'stack', style: { padding: '18px', gap: '16px' } }, Array.from({ length: n }, () => skeleton('100%', 18)));
}

/** Блок кода с кнопкой копирования. */
export function codeBlock(text, onCopy) {
  return h('div', { class: 'codeblock' }, h('code', null, text),
    h('button', { class: 'iconbtn iconbtn--sm', type: 'button', title: 'Копировать', 'aria-label': 'Копировать', onClick: onCopy }, icon('copy', 15)));
}

/** Полоски прогресса фоновой задачи: по одной на этап. update(job) обновляет на месте. */
export function jobProgress(job) {
  const root = h('div', { class: 'stack', style: { gap: '14px' } });
  const rows = new Map();
  const head = h('div', { class: 'row row--between' },
    h('div', { class: 'row', style: { gap: '10px' } }, h('span', { class: 'spin', style: { display: 'inline-grid', color: 'var(--accent-text)' } }, icon('loader-circle', 18)),
      h('strong', null, `${JOB_LABELS[job.kind] || 'Задача'}…`)),
    h('span', { class: 'muted num' }, ''));
  root.appendChild(head);

  function update(j) {
    const elapsed = (j.finished_at || Date.now() / 1000) - j.started_at;
    head.lastChild.textContent = fmtDuration(elapsed);
    const stages = j.stages?.length ? j.stages : [{ name: 'Подготовка', done: 0, total: 0 }];
    for (const s of stages) {
      let row = rows.get(s.name);
      if (!row) {
        row = {
          label: h('span', { class: 'progress__label' }, s.name),
          val: h('span', { class: 'progress__val' }),
          bar: h('div', { class: 'progress__bar' }),
        };
        row.el = h('div', { class: 'progress progress--running' },
          h('div', { class: 'progress__row' }, row.label, row.val),
          h('div', { class: 'progress__track', role: 'progressbar', 'aria-label': s.name }, row.bar));
        rows.set(s.name, row);
        root.appendChild(row.el);
      }
      const indeterminate = !s.total;
      row.el.classList.toggle('progress--indeterminate', indeterminate);
      row.val.textContent = s.total ? `${s.done} из ${s.total}` : '';
      row.bar.style.width = s.total ? `${Math.min(100, (s.done / s.total) * 100)}%` : '';
      const done = s.total && s.done >= s.total;
      row.el.classList.toggle('progress--running', !done && j.state === 'running');
    }
  }
  update(job);
  root.update = update;
  return root;
}
