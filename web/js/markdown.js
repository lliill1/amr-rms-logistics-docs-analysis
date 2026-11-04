// Безопасный мини-рендер Markdown для ответов модели.
// Строит DOM-узлы (никакого innerHTML), поэтому текст от модели не может внедрить разметку.
// Ссылки на источники вида [1], [2, 3] превращаются в кнопки-ссылки (onCite).

import { h } from './dom.js';

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[(?:\d+\s*(?:[,;–-]\s*\d+\s*)*)\]|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|\*[^*\s][^*\n]*\*)/g;

function inline(text, onCite) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('**')) {
      out.push(h('strong', null, ...inline(tok.slice(2, -2), onCite)));
    } else if (tok.startsWith('`')) {
      out.push(h('code', null, tok.slice(1, -1)));
    } else if (/^\[\d/.test(tok)) {
      const nums = tok.slice(1, -1).split(/[,;–-]/).map((s) => parseInt(s, 10)).filter(Number.isFinite);
      for (const n of nums) {
        out.push(
          onCite
            ? h('button', { class: 'cite', type: 'button', title: `Источник ${n}`, onClick: () => onCite(n) }, String(n))
            : h('span', { class: 'cite' }, String(n)),
        );
      }
    } else if (tok.startsWith('[')) {
      const mm = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(tok);
      out.push(mm ? h('a', { href: mm[2], target: '_blank', rel: 'noopener noreferrer' }, mm[1]) : tok);
    } else {
      out.push(h('em', null, tok.slice(1, -1)));
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

export function renderMarkdown(src, { onCite } = {}) {
  const root = h('div', { class: 'md' });
  const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n');
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    // блок кода
    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      root.appendChild(h('pre', null, h('code', null, buf.join('\n'))));
      continue;
    }
    // заголовок
    const hd = /^(#{1,4})\s+(.*)$/.exec(line);
    if (hd) {
      root.appendChild(h(`h${Math.min(hd[1].length + 1, 4)}`, null, ...inline(hd[2], onCite)));
      i++;
      continue;
    }
    // линия
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { root.appendChild(h('hr')); i++; continue; }
    // таблица
    if (line.includes('|') && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] || '')) {
      const head = splitRow(line);
      i += 2;
      const body = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) body.push(splitRow(lines[i++]));
      root.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ...head.map((c) => h('th', null, ...inline(c, onCite))))),
        h('tbody', null, ...body.map((r) => h('tr', null, ...r.map((c) => h('td', null, ...inline(c, onCite)))))),
      )));
      continue;
    }
    // цитата
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
      root.appendChild(h('blockquote', null, ...inline(buf.join(' '), onCite)));
      continue;
    }
    // списки
    const ul = /^\s*[-*•]\s+/;
    const ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      const ordered = ol.test(line);
      const re = ordered ? ol : ul;
      const items = [];
      while (i < lines.length && re.test(lines[i])) {
        let text = lines[i++].replace(re, '');
        // продолжение пункта (строки с отступом)
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !ul.test(lines[i]) && !ol.test(lines[i])) text += ' ' + lines[i++].trim();
        items.push(h('li', null, ...inline(text, onCite)));
      }
      root.appendChild(h(ordered ? 'ol' : 'ul', null, ...items));
      continue;
    }
    // абзац
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>\s?|\s*[-*•]\s+|\s*\d+[.)]\s+)/.test(lines[i])) buf.push(lines[i++]);
    const nodes = [];
    buf.forEach((l, idx) => {
      if (idx) nodes.push(h('br'));
      nodes.push(...inline(l, onCite));
    });
    root.appendChild(h('p', null, ...nodes));
  }
  return root;
}
