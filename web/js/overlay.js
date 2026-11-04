// Боковая панель (drawer) и диалог подтверждения. Одновременно открыто не больше одного окна.
import { h, mount } from './dom.js';
import { icon } from './icons.js';

let active = null;

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function attach(overlay, panel, { onClose, initialFocus } = {}) {
  const prevFocus = document.activeElement;
  document.body.style.overflow = 'hidden';
  document.body.classList.add('has-drawer');
  document.body.append(overlay, panel);

  let closed = false;
  const close = (silent = false) => {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey, true);
    document.body.style.overflow = '';
    document.body.classList.remove('has-drawer');
    overlay.classList.add('is-closing');
    panel.classList.add('is-closing');
    const finish = () => { overlay.remove(); panel.remove(); };
    if (silent) finish(); else setTimeout(finish, 170);
    if (active === handle) active = null;
    prevFocus?.isConnected && prevFocus.focus?.({ preventScroll: true });
    if (!silent) onClose?.();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
    } else if (e.key === 'Tab') {
      const items = [...panel.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  document.addEventListener('keydown', onKey, true);
  overlay.addEventListener('mousedown', () => close());
  const handle = { close, panel };
  active = handle;
  requestAnimationFrame(() => (initialFocus?.() || panel).focus?.({ preventScroll: true }));
  return handle;
}

/**
 * openDrawer({title, subtitle, body, footer, onClose})
 * Возвращает {close, setTitle, setHeaderExtra, setBody, setFooter, el}
 */
export function openDrawer({ title = '', subtitle = '', body = [], footer = [], onClose } = {}) {
  active?.close(true);
  const titleEl = h('h2', { class: 'drawer__title', id: 'drawer-title' }, title);
  const subEl = h('p', { class: 'drawer__sub' }, subtitle);
  const bodyEl = h('div', { class: 'drawer__body' }, body);
  const footEl = h('div', { class: 'drawer__foot' }, footer);
  const closeBtn = h('button', { class: 'iconbtn iconbtn--sm', type: 'button', 'aria-label': 'Закрыть', onClick: () => handle.close() }, icon('x', 16));
  const extraEl = h('div', { class: 'drawer__extra' });
  const panel = h('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'drawer-title', tabIndex: -1 },
    h('div', { class: 'drawer__head' }, h('div', { class: 'drawer__titles' }, titleEl, subEl), extraEl, closeBtn),
    bodyEl, footEl);
  const overlay = h('div', { class: 'overlay' });
  const handle = attach(overlay, panel, { onClose });
  Object.assign(handle, {
    el: panel,
    setTitle(t, s = '') { titleEl.textContent = t; subEl.textContent = s; subEl.hidden = !s; },
    setHeaderExtra(...nodes) { mount(extraEl, ...nodes); },
    setBody(...nodes) { mount(bodyEl, ...nodes); bodyEl.scrollTop = 0; },
    setFooter(...nodes) { mount(footEl, ...nodes); footEl.hidden = !nodes.flat().filter(Boolean).length; },
  });
  handle.setTitle(title, subtitle);
  handle.setFooter(...[].concat(footer));
  return handle;
}

/** confirmDialog({title, text, confirmLabel, danger}) → Promise<boolean> */
export function confirmDialog({ title, text = '', confirmLabel = 'Подтвердить', cancelLabel = 'Отмена', danger = false }) {
  return new Promise((resolve) => {
    active?.close(true);
    let answer = false;
    const ok = h('button', { class: `btn ${danger ? 'btn--danger' : 'btn--primary'}`, type: 'button', onClick: () => { answer = true; handle.close(); } }, confirmLabel);
    const panel = h('div', { class: 'dialog', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': 'dlg-title', tabIndex: -1 },
      h('h2', { class: 'dialog__title', id: 'dlg-title' }, title),
      text && h('p', { class: 'dialog__text' }, text),
      h('div', { class: 'dialog__actions' },
        h('button', { class: 'btn btn--secondary', type: 'button', onClick: () => handle.close() }, cancelLabel), ok));
    const overlay = h('div', { class: 'overlay' });
    const handle = attach(overlay, panel, { onClose: () => resolve(answer), initialFocus: () => ok });
  });
}

export function closeOverlay() {
  active?.close();
}
