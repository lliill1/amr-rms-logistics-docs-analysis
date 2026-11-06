// Вкладка «Противоречия» на экране «Факты и ревью» (версия 2).
// Противоречия между документами, изменения между редакциями и пробелы (решения не найдены).
// Действия: принять вариант, вынести вопрос заказчику, отметить «не противоречие».
import { api } from '../api.js';
import { button, chipGroup, emptyState, pill, segmented, skeletonRows, withBusy } from '../components.js';
import { h, mount } from '../dom.js';
import { icon } from '../icons.js';
import { toast, toastError } from '../toast.js';

const KIND_ICON = { contradiction: 'git-compare-arrows', revision: 'history', gap: 'circle-help' };
const STATUS_TONE = { open: 'warning', resolved: 'success', dismissed: null, question: 'info' };
const KINDS = [
  { value: 'contradiction', label: 'Противоречия' },
  { value: 'revision', label: 'Изменения между редакциями' },
  { value: 'gap', label: 'Пробелы' },
];

export function createConflictsPane({ onCount, onQuestion } = {}) {
  const st = { items: [], show: 'open', kinds: [], loading: true };
  const listEl = h('div', { class: 'conflicts' });
  const showEl = segmented({ label: 'Показывать', value: st.show, options: [{ value: 'open', label: 'Неразобранные' }, { value: 'all', label: 'Все' }],
    onChange: (v) => { st.show = v; render(); } });
  const kindChips = chipGroup({ label: 'Вид', selected: st.kinds, options: KINDS.map((k) => ({ ...k, count: 0 })), onChange: (v) => { st.kinds = v; render(); } });
  const el = h('div', { class: 'stack', style: { gap: '16px' } },
    h('p', { class: 'muted', style: { maxWidth: '78ch' } },
      'Сервис сравнивает факты из разных документов: кто за что отвечает, какие обмены и протоколы описаны. ',
      'Противоречие — актуальные документы называют разные системы; изменение — новая редакция поменяла решение; пробел — решения в документах нет.'),
    h('div', { class: 'row row--wrap', style: { gap: '12px' } }, showEl, kindChips),
    listEl);

  function render() {
    const counts = {};
    st.items.filter((c) => c.status === 'open').forEach((c) => { counts[c.kind] = (counts[c.kind] || 0) + 1; });
    kindChips.setCounts(Object.fromEntries(KINDS.map((k) => [k.value, counts[k.value] || 0])));
    onCount?.(st.items.filter((c) => c.status === 'open').length);
    if (st.loading) { mount(listEl, skeletonRows(3)); return; }
    let list = st.show === 'open' ? st.items.filter((c) => c.status === 'open') : st.items;
    if (st.kinds.length) list = list.filter((c) => st.kinds.includes(c.kind));
    if (!st.items.length) {
      mount(listEl, h('section', { class: 'card' }, emptyState({ icon: 'git-compare-arrows', title: 'Сравнивать пока нечего',
        text: 'Противоречия и пробелы появятся после извлечения вида «Обмены и ответственность».' })));
      return;
    }
    if (!list.length) {
      mount(listEl, h('section', { class: 'card' }, emptyState({ icon: 'circle-check', title: st.show === 'open' ? 'Всё разобрано' : 'Под фильтр ничего не попало',
        text: st.show === 'open' ? 'Неразобранных противоречий и пробелов нет. Решённые — в режиме «Все».' : '' })));
      return;
    }
    mount(listEl, list.map(card));
  }

  function card(c) {
    const det = c.details || {};
    const done = c.status !== 'open';
    const canChoose = det.variants?.length > 1 && (det.area === 'responsibility' || det.area === 'protocol');
    const variants = (det.variants || []).map((v) => {
      const chosen = c.status === 'resolved' && c.resolution === v.value;
      const choose = canChoose && c.status !== 'resolved' && button({
        label: `Принять «${v.value}»`, icon: 'check', variant: 'soft', size: 'sm',
        onClick: (e) => withBusy(e.currentTarget, () => act(c, 'resolve', { value: v.value },
          det.area === 'responsibility' ? `Принято: ${v.value}. Остальные варианты отклонены.` : `Принято: ${v.value}`)),
      });
      return h('div', { class: `variant${chosen ? ' is-chosen' : ''}` },
        h('div', { class: 'variant__value' }, v.value, !v.current && pill('устаревшая редакция', 'info', { dot: false }),
          chosen && pill('принято', 'success', { dot: false })),
        v.sources.map((s) => h('div', { class: 'variant__src' },
          h('span', null, s.label, s.doc_date ? ` · ${s.doc_date.split('-').reverse().join('.')}` : '', s.side && s.side !== 'не указано' ? ` · ${s.side}` : '',
            s.approval && s.approval !== 'не указано' ? ` · ${s.approval}` : ''),
          s.quote && h('q', null, s.quote))),
        choose);
    });
    const gapSources = (det.sources || []).map((s) => h('div', { class: 'variant__src' }, s.label, s.quote && h('q', null, s.quote)));

    const actions = [];
    if (c.question_id) {
      actions.push(h('a', { class: 'btn btn--ghost btn--sm', href: '#/facts?tab=questions' }, icon('circle-help', 15), `Вопрос заказчику #${c.question_id}`));
    } else if (c.status !== 'resolved') {
      actions.push(button({ label: 'В вопросы заказчику', icon: 'send', variant: c.kind === 'gap' ? 'primary' : 'secondary', size: 'sm',
        onClick: (e) => withBusy(e.currentTarget, async () => { await act(c, 'question', undefined, 'Вопрос добавлен в «Открытые вопросы»'); onQuestion?.(); }) }));
    }
    if (c.status === 'open') {
      actions.push(button({ label: c.kind === 'gap' ? 'Не нужно' : 'Не противоречие', icon: 'x', variant: 'ghost', size: 'sm',
        onClick: (e) => withBusy(e.currentTarget, () => act(c, 'dismiss', undefined, 'Отмечено')) }));
    } else {
      actions.push(button({ label: 'Вернуть в работу', icon: 'refresh-cw', variant: 'ghost', size: 'sm',
        onClick: (e) => withBusy(e.currentTarget, () => act(c, 'reopen', undefined, 'Возвращено в работу')) }));
    }

    return h('section', { class: `card conflict${done ? ' is-done' : ''}` },
      h('div', { class: 'conflict__head' },
        h('span', { class: `conflict__icon conflict__icon--${c.kind}` }, icon(KIND_ICON[c.kind] || 'circle-help', 18)),
        h('div', { class: 'conflict__title' }, c.title),
        pill(c.kind_label, null, { dot: false }),
        pill(c.status_label + (c.status === 'resolved' && c.resolution ? `: ${c.resolution}` : ''), STATUS_TONE[c.status])),
      variants.length ? h('div', { class: 'conflict__variants' }, variants) : null,
      gapSources.length ? h('div', { class: 'variant' }, h('div', { class: 'variant__value', style: { fontSize: '13px' } }, 'Найденные обмены'), gapSources) : null,
      h('div', { class: 'conflict__actions' }, actions));
  }

  async function act(c, what, body, message) {
    try {
      const updated = await api.post(`/conflicts/${c.id}/${what}`, body);
      st.items = st.items.map((x) => (x.id === c.id ? updated : x));
      toast(message);
      render();
    } catch (e) { toastError(e); }
  }

  async function load() {
    try { st.items = (await api.get('/conflicts')).items; } catch (e) { toastError(e); }
    st.loading = false;
    render();
  }

  render();
  return { el, load };
}
