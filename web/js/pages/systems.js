// Экран «Логистика и системы» (версия 2): схема взаимодействия систем, процессы с AMR,
// общая схема логистики, матрица ответственности. Всё строится из фактов, извлечённых моделью
// и проверенных аналитиком; переключатель «Только подтверждённое» показывает чистовой вариант.
import { api } from '../api.js';
import {
  button, chipGroup, emptyState, field, pill, searchBox, segmented, selectEl, skeleton, skeletonRows, switchEl, tabs, tag, withBusy,
} from '../components.js';
import { debounce, h, mount } from '../dom.js';
import { splitPath, withNoun } from '../format.js';
import { icon } from '../icons.js';
import { jobEvents } from '../jobs.js';
import { reqStatusPill } from '../labels.js';
import { confirmDialog, openDrawer } from '../overlay.js';
import { toast, toastError } from '../toast.js';
import {
  extractAction, loadDiagram, meta, openFactDrawer, quoteSection, statusLabel, systemOptions, textArea, textInput,
} from '../facts-common.js';

const TABS = [
  { value: 'systems', label: 'Схема систем' },
  { value: 'processes', label: 'Процессы с AMR' },
  { value: 'logistics', label: 'Схема логистики' },
  { value: 'matrix', label: 'Ответственность' },
];
const ROLE_TONE = { 'роботизирован': 'accent', 'планируется': 'info', 'обсуждается': 'warning', 'вручную': null };
const rolePill = (role) => pill(role || '—', ROLE_TONE[role] ?? null, { dot: false });
const DEFAULT_STATUSES = ['auto', 'confirmed', 'edited'];

export function createSystemsPage(ctx) {
  const q = ctx.query.get('tab');
  const st = {
    tab: TABS.some((t) => t.value === q) ? q : 'systems',
    onlyConfirmed: false,
    ifs: [], ifStatuses: [...DEFAULT_STATUSES], ifText: '',
    procs: [], procStatuses: [...DEFAULT_STATUSES], procRoles: [], procText: '',
    overview: null,
  };
  const cleanup = [];
  const inc = () => ({ include_auto: !st.onlyConfirmed });
  const qs = () => (st.onlyConfirmed ? '?include_auto=false' : '');

  // ───────── каркас ─────────
  const tabsEl = tabs({
    label: 'Раздел', value: st.tab, items: TABS,
    onChange: (v) => { st.tab = v; history.replaceState(null, '', `#/systems?tab=${v}`); renderTab(); },
  });
  const onlyEl = switchEl({ label: 'Только подтверждённое', checked: st.onlyConfirmed, onChange: (v) => { st.onlyConfirmed = v; reloadVisible(); } });
  const panes = Object.fromEntries(TABS.map((t) => [t.value, h('div', { class: 'stack', style: { gap: '16px' } })]));
  const root = h('div', null,
    h('div', { class: 'page-head' },
      h('div', null, h('h1', { class: 'page-title' }, 'Логистика и системы'),
        h('p', { class: 'page-sub' }, 'Как должна работать роботизированная логистика: процессы, потоки, обмены между системами и кто за что отвечает. Всё со ссылками на документы.')),
      h('div', { class: 'page-head__aside stack', style: { gap: '10px', alignItems: 'flex-end' } }, tabsEl, onlyEl)),
    Object.values(panes));

  const loaded = {};
  function renderTab() {
    tabsEl.set(st.tab);
    for (const [k, el] of Object.entries(panes)) { el.hidden = k !== st.tab; el.classList.toggle('hidden', k !== st.tab); }
    if (!loaded[st.tab]) { loaded[st.tab] = true; LOADERS[st.tab](); }
  }
  function reloadVisible() {
    Object.keys(loaded).forEach((k) => { loaded[k] = false; });
    renderTab();
  }

  // ═════════════ 1. СХЕМА СИСТЕМ + ОБМЕНЫ ═════════════
  const sysDiagram = h('div');
  const ifToolbar = h('div', { class: 'toolbar' });
  const ifTable = h('div', { class: 'table-wrap' });
  mount(panes.systems,
    h('section', { class: 'card' },
      h('div', { class: 'card__head' },
        h('span', { class: 'card__icon card__icon--accent' }, icon('network', 18)),
        h('div', null, h('h2', { class: 'card__title' }, 'Взаимодействие систем'),
          h('p', { class: 'card__sub' }, 'Обмены между SAP EWM, ERP, MES, RMS, роботами и автоматикой по актуальным документам.')),
        h('span', { class: 'spacer' }),
        h('div', { class: 'diagram__legend' }, h('span', null, h('i', { class: 'is-accent' }), 'обмены с участием RMS'), h('span', null, h('i'), 'прочие'))),
      sysDiagram),
    h('section', { class: 'card card--flush', 'aria-label': 'Обмены между системами' }, ifToolbar, ifTable));

  const ifSearch = searchBox({ placeholder: 'Система, сообщение, протокол', onInput: debounce((v) => { st.ifText = v; loadIfs(); }, 250) });
  let ifChips = null;
  const ifConfirmBtn = button({ label: 'Подтвердить все показанные', icon: 'badge-check', variant: 'soft', size: 'sm', onClick: () => confirmShown('/interfaces', st.ifs, loadIfs) });

  function buildIfToolbar() {
    ifChips = chipGroup({
      label: 'Статус', selected: st.ifStatuses,
      options: Object.entries(meta().requirement_statuses || {}).map(([value, label]) => ({ value, label, count: 0 })),
      onChange: (v) => { st.ifStatuses = v; loadIfs(); },
    });
    mount(ifToolbar,
      h('div', { class: 'row row--wrap', style: { width: '100%', gap: '12px' } },
        h('h2', { class: 'card__title' }, 'Обмены'),
        h('div', { style: { flex: '1 1 240px', maxWidth: '360px' } }, ifSearch), h('span', { class: 'spacer' }), ifConfirmBtn),
      h('div', { style: { width: '100%' } }, ifChips));
  }

  async function loadIfs() {
    mount(ifTable, skeletonRows(4));
    let res;
    try { res = await api.get('/interfaces', { status: st.ifStatuses, q: st.ifText }); } catch (e) { toastError(e); return; }
    st.ifs = res.items;
    ifChips?.setCounts(res.by_status);
    const auto = st.ifs.filter((r) => r.status === 'auto').length;
    ifConfirmBtn.disabled = !auto;
    if (!res.total) { mount(ifTable, emptyState({ icon: 'arrow-right-left', title: 'Обменов пока нет', text: 'Запустите извлечение вида «Обмены и ответственность» на экране «Факты и ревью».', action: extractAction() })); return; }
    if (!st.ifs.length) { mount(ifTable, emptyState({ icon: 'search', title: 'Под фильтр ничего не попало' })); return; }
    mount(ifTable, h('table', { class: 'table' },
      h('thead', null, h('tr', null, ['ID', 'Откуда → куда', 'Что передаётся', 'Протокол', 'Статус', 'Источник', ''].map((t) => h('th', null, t)))),
      h('tbody', null, st.ifs.map((r) => factRow(r, '/interfaces', loadIfs, openIf, [
        h('td', null, h('span', { class: 'flow' }, r.source_system, icon('arrow-right', 15), r.target_system)),
        h('td', { style: { minWidth: '220px' } }, h('div', { class: 'clamp-2', style: { fontWeight: 550 } }, r.message),
          r.trigger && h('div', { class: 'cell-sub' }, r.trigger)),
        h('td', null, r.protocol ? tag(r.protocol) : h('span', { class: 'faint' }, 'не указан')),
      ])))));
  }

  function openIf(id) {
    openFactDrawer({
      endpoint: '/interfaces', id, ids: () => st.ifs.map((r) => r.id),
      onSaved: async () => { await loadIfs(); loadDiagram(sysDiagram, `/api/diagrams/systems.svg${qs()}`, { emptyAction: extractAction() }); },
      fields: (r, form, sync, track) => {
        ['source_system', 'target_system', 'message', 'protocol', 'trigger'].forEach((k) => track(k, r[k] || ''));
        return [
          field('Откуда', selectEl({ options: systemOptions(), value: r.source_system, onChange: (v) => { form.source_system = v; sync(); } })),
          field('Куда', selectEl({ options: systemOptions(), value: r.target_system, onChange: (v) => { form.target_system = v; sync(); } })),
          h('div', { class: 'span-2' }, field('Что передаётся', textInput(r.message, (v) => { form.message = v; sync(); }))),
          field('Протокол', textInput(r.protocol, (v) => { form.protocol = v; sync(); }, 'VDA 5050, REST, IDoc…')),
          field('Когда', textInput(r.trigger, (v) => { form.trigger = v; sync(); })),
        ];
      },
    });
  }

  // ═════════════ 2. ПРОЦЕССЫ С AMR ═════════════
  const procToolbar = h('div', { class: 'toolbar', style: { padding: 0, border: 0 } });
  const procGrid = h('div');
  mount(panes.processes,
    h('p', { class: 'muted', style: { maxWidth: '75ch' } }, 'Сценарии перемещения материалов из документов: что запускает процесс, кто его инициирует, какие шаги выполняют люди, системы и роботы. Нажмите на карточку, чтобы проверить процесс по источнику и увидеть его схему.'),
    procToolbar, procGrid);
  const procSearch = searchBox({ placeholder: 'Название, место, груз', onInput: debounce((v) => { st.procText = v; loadProcs(); }, 250) });
  let procStatusChips = null;
  let procRoleChips = null;

  function buildProcToolbar() {
    procRoleChips = chipGroup({ label: 'Роль роботов', selected: st.procRoles, options: (meta().amr_roles || []).map((v) => ({ value: v, label: v })),
      onChange: (v) => { st.procRoles = v; renderProcs(); } });
    procStatusChips = chipGroup({ label: 'Статус', selected: st.procStatuses,
      options: Object.entries(meta().requirement_statuses || {}).map(([value, label]) => ({ value, label, count: 0 })),
      onChange: (v) => { st.procStatuses = v; loadProcs(); } });
    mount(procToolbar,
      h('div', { class: 'row row--wrap', style: { width: '100%', gap: '12px' } },
        h('div', { style: { flex: '1 1 240px', maxWidth: '360px' } }, procSearch), procRoleChips,
        h('span', { class: 'spacer' }),
        button({ label: 'Подтвердить все показанные', icon: 'badge-check', variant: 'soft', size: 'sm', onClick: () => confirmShown('/processes', shownProcs(), loadProcs) })),
      h('div', { style: { width: '100%' } }, procStatusChips));
  }

  const shownProcs = () => (st.procRoles.length ? st.procs.filter((p) => st.procRoles.includes(p.amr_role)) : st.procs);

  async function loadProcs() {
    mount(procGrid, h('div', { class: 'proc-list' }, [1, 2, 3].map(() => h('div', { class: 'card' }, skeleton('100%', 120)))));
    let res;
    try { res = await api.get('/processes', { status: st.procStatuses, q: st.procText }); } catch (e) { toastError(e); return; }
    st.procs = res.items;
    st.procTotal = res.total;
    procStatusChips?.setCounts(res.by_status);
    renderProcs();
  }

  function renderProcs() {
    const list = shownProcs();
    if (!st.procTotal) { mount(procGrid, h('section', { class: 'card' }, emptyState({ icon: 'workflow', title: 'Процессов пока нет', text: 'Запустите извлечение вида «Процессы» на экране «Факты и ревью».', action: extractAction() }))); return; }
    if (!list.length) { mount(procGrid, h('section', { class: 'card' }, emptyState({ icon: 'search', title: 'Под фильтр ничего не попало' }))); return; }
    mount(procGrid, h('div', { class: 'proc-list' }, list.map((p) => h('button', {
      class: `card proc${p.status === 'rejected' ? ' is-dim' : ''}`, type: 'button', onClick: () => openProc(p.id),
    },
      h('div', { class: 'proc__head' }, h('span', { class: 'tag' }, p.code), h('div', { class: 'proc__name' }, p.name)),
      h('div', { class: 'row row--wrap', style: { gap: '6px' } }, rolePill(p.amr_role), reqStatusPill(p.status, statusLabel(p.status)),
        p.initiator && h('span', { class: 'muted', style: { fontSize: '12.5px' } }, `инициатор: ${p.initiator}`)),
      (p.from_location || p.to_location) && h('div', { class: 'proc__route' },
        icon('route', 15), p.from_location || '?', icon('arrow-right', 14), p.to_location || '?', p.cargo && h('span', { class: 'faint' }, `· ${p.cargo}`)),
      p.steps.length ? h('ol', { class: 'proc__steps' }, p.steps.slice(0, 4).map((s) => h('li', null, h('b', null, `${s.actor}: `), s.action)),
        p.steps.length > 4 && h('li', { class: 'faint' }, `ещё ${p.steps.length - 4}…`)) : null,
      h('div', { class: 'proc__foot' }, icon('file-text', 13), h('span', { class: 'truncate', title: p.source }, splitPath(p.rel_path).name))))));
  }

  function openProc(id) {
    openFactDrawer({
      endpoint: '/processes', id, ids: () => shownProcs().map((p) => p.id),
      onSaved: async () => { await loadProcs(); loaded.logistics = false; },
      top: (r) => h('div', { style: { marginBottom: '18px' } }, (() => {
        const host = h('div');
        if (r.steps.length > 1) loadDiagram(host, `/api/diagrams/process/${r.id}.svg`, { wide: true });
        return host;
      })()),
      fields: (r, form, sync, track) => {
        ['name', 'trigger', 'initiator', 'amr_role', 'from_location', 'to_location', 'cargo'].forEach((k) => track(k, r[k] || ''));
        track('steps', r.steps.map((s) => ({ actor: s.actor, action: s.action })));
        const stepsHost = h('div', { class: 'steps-edit' });
        const renderSteps = () => {
          mount(stepsHost,
            form.steps.map((s, i) => h('div', { class: 'steps-edit__row' },
              h('span', { class: 'steps-edit__n' }, String(i + 1)),
              selectEl({ options: systemOptions(), value: s.actor, small: true, ariaLabel: `Исполнитель шага ${i + 1}`, onChange: (v) => { form.steps = form.steps.map((x, j) => (j === i ? { ...x, actor: v } : x)); sync(); } }),
              textInput(s.action, (v) => { form.steps = form.steps.map((x, j) => (j === i ? { ...x, action: v } : x)); sync(); }),
              h('button', { class: 'iconbtn iconbtn--sm iconbtn--bad', type: 'button', 'aria-label': `Удалить шаг ${i + 1}`, title: 'Удалить шаг',
                onClick: () => { form.steps = form.steps.filter((_, j) => j !== i); renderSteps(); sync(); } }, icon('trash-2', 14)))),
            button({ label: 'Добавить шаг', icon: 'plus', variant: 'ghost', size: 'sm', onClick: () => { form.steps = [...form.steps, { actor: 'AMR', action: '' }]; renderSteps(); sync(); } }));
        };
        renderSteps();
        const roles = (meta().amr_roles || []).map((v) => ({ value: v, label: v }));
        return [
          h('div', { class: 'span-2' }, field('Название процесса', textInput(r.name, (v) => { form.name = v; sync(); }))),
          h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Роль роботов'),
            segmented({ options: roles, value: r.amr_role, label: 'Роль роботов', block: true, onChange: (v) => { form.amr_role = v; sync(); } })),
          field('Что запускает', textInput(r.trigger, (v) => { form.trigger = v; sync(); })),
          field('Кто инициирует', selectEl({ options: systemOptions(), value: r.initiator || 'Другое', onChange: (v) => { form.initiator = v; sync(); } })),
          field('Откуда', textInput(r.from_location, (v) => { form.from_location = v; sync(); })),
          field('Куда', textInput(r.to_location, (v) => { form.to_location = v; sync(); })),
          h('div', { class: 'span-2' }, field('Груз', textInput(r.cargo, (v) => { form.cargo = v; sync(); }))),
          h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Шаги процесса'), stepsHost),
        ];
      },
    });
  }

  // ═════════════ 3. СХЕМА ЛОГИСТИКИ ═════════════
  const logDiagram = h('div');
  const flowsTable = h('div', { class: 'table-wrap' });
  mount(panes.logistics,
    h('section', { class: 'card' },
      h('div', { class: 'card__head' },
        h('span', { class: 'card__icon card__icon--accent' }, icon('route', 18)),
        h('div', null, h('h2', { class: 'card__title' }, 'Материальные потоки'),
          h('p', { class: 'card__sub' }, 'Участки и потоки между ними из описаний процессов. Выделены потоки, где перевозку выполняют или планируют выполнять роботы.')),
        h('span', { class: 'spacer' }),
        h('div', { class: 'diagram__legend' }, h('span', null, h('i', { class: 'is-accent' }), 'AMR'), h('span', null, h('i', { class: 'is-dashed' }), 'обсуждается'), h('span', null, h('i'), 'вручную'))),
      logDiagram),
    h('section', { class: 'card card--flush', 'aria-label': 'Потоки' }, flowsTable));

  async function loadLogistics() {
    loadDiagram(logDiagram, `/api/diagrams/logistics.svg${qs()}`, { emptyTitle: 'Потоков пока нет', emptyAction: extractAction() });
    mount(flowsTable, skeletonRows(3));
    let res;
    try { res = await api.get('/processes', { status: st.onlyConfirmed ? ['confirmed', 'edited'] : DEFAULT_STATUSES }); } catch (e) { toastError(e); return; }
    st.procs = st.procs.length ? st.procs : res.items;
    const flows = res.items.filter((p) => p.from_location || p.to_location);
    if (!flows.length) { mount(flowsTable); return; }
    mount(flowsTable, h('table', { class: 'table' },
      h('thead', null, h('tr', null, ['ID', 'Поток', 'Откуда → куда', 'Груз', 'Роль AMR', 'Статус'].map((t) => h('th', null, t)))),
      h('tbody', null, flows.map((p) => h('tr', { class: 'is-click', tabIndex: 0, onClick: () => openFlow(p.id), onKeydown: (e) => { if (e.key === 'Enter') openFlow(p.id); } },
        h('td', { class: 'col-narrow' }, h('span', { class: 'tag' }, p.code)),
        h('td', { style: { fontWeight: 550 } }, p.name),
        h('td', null, h('span', { class: 'flow', style: { fontWeight: 500 } }, p.from_location || '?', icon('arrow-right', 14), p.to_location || '?')),
        h('td', { class: 'muted' }, p.cargo || '—'),
        h('td', null, rolePill(p.amr_role)),
        h('td', null, reqStatusPill(p.status, statusLabel(p.status))))))));
    function openFlow(id) {
      st.procs = res.items;
      openProc(id);
    }
  }

  // ═════════════ 4. МАТРИЦА ОТВЕТСТВЕННОСТИ ═════════════
  const matrixHost = h('div', { class: 'table-wrap' });
  const matrixHead = h('div', { class: 'card__head', style: { padding: '18px 20px 0' } });
  mount(panes.matrix, h('section', { class: 'card card--flush' }, matrixHead, matrixHost));
  let matrixData = null;

  async function loadMatrix() {
    mount(matrixHost, skeletonRows(6));
    let conflicts = 0;
    try {
      matrixData = await api.get('/model/matrix', inc());
      conflicts = (await api.get('/conflicts')).open;
    } catch (e) { toastError(e); return; }
    const m = matrixData;
    const nConflict = m.rows.filter((r) => r.state === 'conflict').length;
    const nGap = m.rows.filter((r) => r.state === 'gap').length;
    mount(matrixHead,
      h('span', { class: 'card__icon card__icon--accent' }, icon('grid-3x3', 18)),
      h('div', null, h('h2', { class: 'card__title' }, 'Кто за что отвечает'),
        h('p', { class: 'card__sub' }, `Функции роботизированной перевозки × системы. Противоречий: ${nConflict}, не указано: ${nGap}. Нажмите на ячейку, чтобы увидеть источники.`)),
      h('span', { class: 'spacer' }),
      conflicts ? h('a', { class: 'btn btn--soft btn--sm', href: '#/facts?tab=conflicts' }, icon('git-compare-arrows', 15), `Противоречия и пробелы · ${conflicts}`) : null);
    mount(matrixHost, h('table', { class: 'matrix' },
      h('thead', null, h('tr', null, h('th', null, 'Функция'), m.systems.map((s) => h('th', null, s)), h('th', null, 'Итог'))),
      h('tbody', null, m.rows.map((r) => h('tr', { class: r.state === 'conflict' ? 'is-conflict' : r.state === 'gap' ? 'is-gap' : '' },
        h('td', null, h('span', { class: 'matrix__fn' }, r.title), r.required && h('span', { class: 'matrix__req', title: 'Обязательная функция' }, '*')),
        m.systems.map((s) => {
          const cell = r.cells[s] || [];
          if (!cell.length) return h('td', null, '');
          const cur = cell.filter((x) => x.is_current);
          const cls = !cur.length ? 'mcell mcell--old' : r.state === 'conflict' ? 'mcell mcell--conflict' : 'mcell';
          return h('td', null, h('button', { class: cls, type: 'button', title: cell.map((x) => `${x.code} — ${x.label}`).join('\n'),
            'aria-label': `${r.title}: ${s}, ${cell.length} ${cell.length === 1 ? 'источник' : 'источника'}`, onClick: () => openCell(r, s) }, cur.length ? '●' + (cell.length > 1 ? ` ${cell.length}` : '') : '○'));
        }),
        h('td', null, stateLabel(r.state)))))),
      h('p', { class: 'field__hint', style: { padding: '12px 20px 18px' } }, '* — обязательная функция. ● — указано в актуальном документе (число — сколько источников), ○ — только в устаревшей редакции.'));
  }

  function stateLabel(state) {
    return {
      ok: pill('определено', 'success', { dot: false }), conflict: pill('противоречие', 'danger', { dot: false }),
      gap: pill('не указано', 'warning', { dot: false }), outdated: pill('только в старых редакциях', 'info', { dot: false }),
      empty: h('span', { class: 'faint' }, '—'),
    }[state];
  }

  function openCell(row, system) {
    const cell = row.cells[system] || [];
    const drawer = openDrawer({ title: `${row.title}`, subtitle: `${system} · ${withNoun(cell.length, ['источник', 'источника', 'источников'])}`, body: [skeleton('100%', 120)] });
    (async () => {
      const ids = (await api.get('/responsibilities', { q: row.key })).items.filter((x) => x.system === system);
      drawer.setBody(h('div', { class: 'stack', style: { gap: '20px' } }, ids.map((x) => {
        const detail = h('div', null, skeleton('100%', 60));
        api.get(`/responsibilities/${x.id}`).then((r) => mount(detail, quoteSection(r))).catch(toastError);
        const act = (status) => async (e) => {
          await withBusy(e.currentTarget, async () => {
            try { await api.patch(`/responsibilities/${x.id}`, { status }); toast(status === 'confirmed' ? 'Подтверждено' : 'Отклонено'); drawer.close(); loadMatrix(); } catch (err) { toastError(err); }
          });
        };
        return h('section', { class: 'card card--tinted stack', style: { gap: '12px' } },
          h('div', { class: 'row row--wrap', style: { gap: '8px' } }, h('span', { class: 'tag' }, x.code), reqStatusPill(x.status, statusLabel(x.status)),
            !x.is_current && pill('устаревшая редакция', 'info', { dot: false }), h('span', { class: 'muted truncate', style: { fontSize: '12.5px' }, title: x.source }, x.source)),
          detail,
          h('div', { class: 'row', style: { gap: '8px' } },
            x.status !== 'confirmed' && button({ label: 'Подтвердить', icon: 'badge-check', variant: 'soft', size: 'sm', onClick: act('confirmed') }),
            x.status !== 'rejected' && button({ label: 'Отклонить', icon: 'circle-x', variant: 'ghost', size: 'sm', onClick: act('rejected') })));
      })));
    })().catch(toastError);
  }

  // ───────── общее для таблиц ─────────
  function factRow(r, endpoint, reload, open, cells) {
    const quick = (status) => async (e) => {
      e.stopPropagation();
      try { await api.patch(`${endpoint}/${r.id}`, { status }); reload(); } catch (err) { toastError(err); }
    };
    return h('tr', { class: ['is-click', r.status === 'rejected' ? 'is-dim' : ''].join(' '), tabIndex: 0, onClick: () => open(r.id),
      onKeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) open(r.id); } },
      h('td', { class: 'col-narrow' }, h('span', { class: 'tag' }, r.code)),
      cells,
      h('td', null, reqStatusPill(r.status, statusLabel(r.status))),
      h('td', { style: { maxWidth: '220px' } }, h('div', { class: 'truncate muted', title: r.source }, splitPath(r.rel_path).name),
        r.page != null && h('div', { class: 'cell-sub' }, `с. ${Math.trunc(r.page)}`)),
      h('td', { class: 'cell-actions' },
        r.status !== 'confirmed' && h('button', { class: 'iconbtn iconbtn--sm iconbtn--ok', type: 'button', title: 'Подтвердить', 'aria-label': `Подтвердить ${r.code}`, onClick: quick('confirmed') }, icon('check', 16, 2.2)),
        r.status !== 'rejected' && h('button', { class: 'iconbtn iconbtn--sm iconbtn--bad', type: 'button', title: 'Отклонить', 'aria-label': `Отклонить ${r.code}`, onClick: quick('rejected') }, icon('x', 16, 2.2))));
  }

  async function confirmShown(endpoint, list, reload) {
    const ids = list.filter((r) => r.status === 'auto').map((r) => r.id);
    if (!ids.length) { toast('Среди показанных нет непроверенных', { type: 'info' }); return; }
    const ok = await confirmDialog({ title: `Подтвердить ${ids.length}?`, text: 'Все показанные записи со статусом «найдено моделью» станут подтверждёнными и попадут в отчёт.', confirmLabel: 'Подтвердить' });
    if (!ok) return;
    try { toast(`Подтверждено: ${(await api.post(`${endpoint}/confirm`, { ids })).confirmed}`); reload(); } catch (e) { toastError(e); }
  }

  const LOADERS = {
    systems: () => { loadDiagram(sysDiagram, `/api/diagrams/systems.svg${qs()}`, { emptyTitle: 'Обменов пока нет', emptyAction: extractAction() }); loadIfs(); },
    processes: () => loadProcs(),
    logistics: () => loadLogistics(),
    matrix: () => loadMatrix(),
  };

  // после извлечения фактов всё пересчитываем
  cleanup.push(jobEvents.on('finish', (job) => { if (job.kind === 'extraction' && job.state === 'done') reloadVisible(); }));

  buildIfToolbar();
  buildProcToolbar();
  renderTab();
  return { el: root, destroy: () => cleanup.forEach((fn) => fn()) };
}
