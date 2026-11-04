import { h } from './dom.js';
import { icon } from './icons.js';

let host;

function ensureHost() {
  if (!host || !host.isConnected) {
    host = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(host);
  }
  return host;
}

/** toast('Сохранено'); toast('Ошибка', {type: 'error'}); toast('…', {action: {label: 'Открыть', href: '#/facts'}}) */
export function toast(message, { type = 'success', action, timeout } = {}) {
  const iconName = type === 'error' ? 'triangle-alert' : type === 'info' ? 'info' : 'check';
  const el = h('div', { class: `toast toast--${type}` },
    h('span', { class: 'toast__icon' }, icon(iconName, 15, 2.4)),
    h('span', null, message),
    action && (action.href
      ? h('a', { class: 'toast__action', href: action.href, onClick: () => dismiss() }, action.label)
      : h('button', { class: 'toast__action', type: 'button', style: { background: 'none', border: 0 }, onClick: () => { action.onClick?.(); dismiss(); } }, action.label)),
  );
  function dismiss() {
    if (!el.isConnected) return;
    el.classList.add('is-closing');
    setTimeout(() => el.remove(), 180);
  }
  ensureHost().appendChild(el);
  setTimeout(dismiss, timeout ?? (type === 'error' ? 7000 : 4200));
  return dismiss;
}

export const toastError = (e) => toast(e?.message || String(e), { type: 'error' });
