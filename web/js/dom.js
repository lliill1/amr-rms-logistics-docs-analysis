// Мини-помощник для построения DOM без фреймворка и без сборки.
// h('div', {class: 'card', onClick: fn}, 'текст', h('span', null, '…'))

const PROP_KEYS = new Set(['value', 'checked', 'disabled', 'selected', 'indeterminate', 'hidden', 'readOnly', 'required', 'open', 'min', 'max', 'step', 'type', 'name', 'id', 'placeholder', 'rows', 'tabIndex', 'htmlFor', 'href', 'download', 'target', 'rel', 'title']);

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, val] of Object.entries(props)) {
      if (val == null || val === false) continue;
      if (key === 'class') {
        el.className = Array.isArray(val) ? val.filter(Boolean).join(' ') : val;
      } else if (key === 'style' && typeof val === 'object') {
        Object.assign(el.style, val);
      } else if (key === 'dataset') {
        Object.assign(el.dataset, val);
      } else if (key.startsWith('on') && typeof val === 'function') {
        el.addEventListener(key.slice(2).toLowerCase(), val);
      } else if (PROP_KEYS.has(key)) {
        el[key] = val;
      } else {
        el.setAttribute(key, val === true ? '' : val);
      }
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children) {
    if (c == null || c === false || c === true) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
  return el;
}

/** Заменяет содержимое узла. */
export function mount(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export function $(sel, root = document) {
  return root.querySelector(sel);
}

export function debounce(fn, ms = 250) {
  let t;
  const wrapped = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h('textarea', { value: text, style: { position: 'fixed', opacity: '0' } });
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* ignore */ }
    ta.remove();
    return ok;
  }
}
