import { button, emptyState, pill } from '../components.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';

const DEMO_EVENTS = [
  { id: 1, date: '26.11.2026', time: '16:42:18', type: 'report', category: 'Отчёт', title: 'Сформирован итоговый отчёт', details: 'Сводный отчёт по анализу архива документов', target: 'report.pdf', status: 'Готово', tone: 'success', icon: 'file-text' },
  { id: 2, date: '26.11.2026', time: '16:18:04', type: 'analysis', category: 'Анализ', title: 'Выполнен анализ документов', details: 'Извлечение фактов, требований и связей между документами', target: 'Демонстрационный архив · 18 файлов', status: 'Завершено', tone: 'success', icon: 'scan-line' },
  { id: 3, date: '26.11.2026', time: '15:53:41', type: 'files', category: 'Файлы', title: 'Добавлен архив документов', details: 'Файлы подготовлены к обработке и индексированию', target: 'project_docs.zip', status: 'Загружено', tone: 'info', icon: 'folder-archive' },
  { id: 4, date: '26.11.2026', time: '15:49:12', type: 'analysis', category: 'Анализ', title: 'Проверены требования и факты', details: 'Сформирован список найденных фактов и замечаний', target: '12 фактов · 3 замечания', status: 'Завершено', tone: 'warning', icon: 'list-checks' },
];

const FILTERS = [
  { value: 'all', label: 'Все события' },
  { value: 'analysis', label: 'Анализ' },
  { value: 'files', label: 'Файлы' },
  { value: 'report', label: 'Отчёты' },
];

function eventRow(event) {
  return h('article', { class: 'history-row' },
    h('div', { class: `history-row__icon history-row__icon--${event.tone}` }, icon(event.icon, 18)),
    h('div', { class: 'history-row__body' },
      h('div', { class: 'history-row__heading' },
        h('strong', null, event.title),
        pill(event.status, event.tone, { dot: true, sm: true })),
      h('p', { class: 'history-row__details' }, event.details),
      h('div', { class: 'history-row__target' }, icon('file-text', 14), event.target)),
    h('div', { class: 'history-row__time' }, h('strong', null, event.time), h('span', null, event.date)));
}

export function createHistoryPage() {
  let selected = 'all';
  let search = '';

  const searchInput = h('input', { class: 'input history-search__input', type: 'search', placeholder: 'Поиск по событиям…', 'aria-label': 'Поиск по журналу', onInput: (event) => { search = event.target.value.toLocaleLowerCase('ru-RU'); renderRows(); } });
  const searchBox = h('label', { class: 'history-search' }, icon('search', 17), searchInput);
  const filterButtons = new Map();
  const filters = h('div', { class: 'history-filters', role: 'group', 'aria-label': 'Фильтр журнала' },
    FILTERS.map((filter) => {
      const btn = h('button', { class: `history-filter${filter.value === selected ? ' is-active' : ''}`, type: 'button', 'aria-pressed': String(filter.value === selected), onClick: () => { selected = filter.value; renderRows(); updateFilters(); } }, filter.label);
      filterButtons.set(filter.value, btn);
      return btn;
    }));
  const rows = h('div', { class: 'history-list' });

  function updateFilters() {
    filterButtons.forEach((btn, value) => {
      const active = value === selected;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
  }

  function renderRows() {
    const filtered = DEMO_EVENTS.filter((event) => {
      const matchesType = selected === 'all' || event.type === selected;
      const haystack = `${event.title} ${event.details} ${event.target} ${event.category}`.toLocaleLowerCase('ru-RU');
      return matchesType && haystack.includes(search);
    });
    rows.replaceChildren(...(filtered.length ? filtered.map(eventRow) : [emptyState({ icon: 'search', title: 'Ничего не найдено', text: 'Измените поисковый запрос или выберите другой фильтр.' })]));
  }

  renderRows();

  const el = h('div', { class: 'stack history-page', style: { gap: '22px' } },
    h('div', { class: 'page-head' },
      h('div', null,
        h('div', { class: 'eyebrow' }, 'АКТИВНОСТЬ'),
        h('h1', { class: 'page-title' }, 'Журнал действий'),
        h('p', { class: 'page-sub' }, 'История загрузок, анализа документов и сформированных отчётов.')),
      h('div', { class: 'page-head__aside' },
        button({ label: 'В личный кабинет', icon: 'user-round', variant: 'secondary', onClick: () => { location.hash = '#/profile'; } }))),
    h('div', { class: 'history-summary' },
      h('div', { class: 'history-summary__item' }, icon('activity', 17), h('div', null, h('strong', null, '04'), h('span', null, 'события в примере'))),
      h('div', { class: 'history-summary__item' }, icon('scan-line', 17), h('div', null, h('strong', null, '02'), h('span', null, 'операции анализа'))),
      h('div', { class: 'history-summary__item' }, icon('file-text', 17), h('div', null, h('strong', null, '01'), h('span', null, 'сформированный отчёт')))),
    h('section', { class: 'card history-card' },
      h('div', { class: 'history-card__head' },
        h('div', null, h('div', { class: 'card__title' }, 'Последние действия'), h('div', { class: 'card__sub' }, 'Демонстрационные записи журнала')),
        pill('Пример данных', 'info', { dot: true, sm: true })),
      h('div', { class: 'history-toolbar' }, searchBox, filters),
      rows),
    h('div', { class: 'history-disclaimer' }, icon('info', 16),
      h('span', null, 'Журнал пока работает как макет: записи не загружаются из PostgreSQL и не сохраняются после обновления страницы.')));

  return { el, destroy() {} };
}
