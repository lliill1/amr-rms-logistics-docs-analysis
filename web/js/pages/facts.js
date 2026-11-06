// Экран 3. Факты и ревью: извлечение требований моделью и подтверждение аналитиком; открытые вопросы.
import { api } from '../api.js';
import { app, llmOk } from '../app-state.js';
import {
  button, chipGroup, emptyState, field, jobProgress, notice, searchBox, segmented, selectEl, skeleton, skeletonRows, tabs, tag, ticks, withBusy,
} from '../components.js';
import { debounce, h, mount } from '../dom.js';
import { pct, splitPath, withNoun } from '../format.js';
import { icon } from '../icons.js';
import { jobEvents, jobs, startJob } from '../jobs.js';
import { obligationPill, QUESTION_LABELS, questionStatusPill, reqStatusPill } from '../labels.js';
import { confirmDialog, openDrawer } from '../overlay.js';
import { toast, toastError } from '../toast.js';
import { createConflictsPane } from './conflicts.js';

const PAGE = 100;
const Q_STATUSES = ['open', 'sent', 'answered', 'closed'];

export function createFactsPage(ctx) {
  const st = {
    tab: ['questions', 'conflicts'].includes(ctx.query.get('tab')) ? ctx.query.get('tab') : 'requirements',
    // извлечение (версия 2: можно выбрать виды фактов)
    pending: null, limit: 20, resultDismissed: false, kinds: ['requirements', 'processes', 'integration'],
    // требования
    reqs: [], reqTotal: 0, byStatus: {}, loadingReqs: true,
    fTargets: [], fStatuses: ['auto', 'confirmed', 'edited'], fText: '', shown: PAGE, activeId: null,
    // вопросы
    questions: [], loadingQ: true, qFilter: [],
  };
  const cleanup = [];
  let reqToken = 0;
  const meta = () => app.get().meta;
  const statusLabel = (s) => meta()?.requirement_statuses?.[s] || s;

  // ───────── каркас ─────────
  const tabsEl = tabs({
    label: 'Раздел', value: st.tab,
    items: [{ value: 'requirements', label: 'Требования к AMR/RMS' }, { value: 'conflicts', label: 'Противоречия', count: 0 }, { value: 'questions', label: 'Открытые вопросы', count: 0 }],
    onChange: (v) => { st.tab = v; history.replaceState(null, '', v === 'requirements' ? '#/facts' : `#/facts?tab=${v}`); renderTab(); },
  });
  const reqPane = h('div', { class: 'stack', style: { gap: '16px' } });
  const qPane = h('div', { class: 'stack', style: { gap: '16px' } });
  const conflictsPane = createConflictsPane({ onCount: (n) => tabsEl.setCount('conflicts', n), onQuestion: () => loadQuestions() });
  const root = h('div', null,
    h('div', { class: 'page-head' },
      h('div', null, h('h1', { class: 'page-title' }, 'Факты и ревью'),
        h('p', { class: 'page-sub' }, 'Модель предлагает, аналитик подтверждает. В итоговый отчёт попадают только подтверждённые требования.')),
      h('div', { class: 'page-head__aside' }, tabsEl)),
    reqPane, conflictsPane.el, qPane);

  function renderTab() {
    tabsEl.set(st.tab);
    reqPane.hidden = st.tab !== 'requirements';
    qPane.hidden = st.tab !== 'questions';
    reqPane.classList.toggle('hidden', st.tab !== 'requirements');
    qPane.classList.toggle('hidden', st.tab !== 'questions');
    conflictsPane.el.hidden = st.tab !== 'conflicts';
    conflictsPane.el.classList.toggle('hidden', st.tab !== 'conflicts');
  }

  // ═════════════════════════ ТРЕБОВАНИЯ ═════════════════════════

  // ───────── запуск извлечения ─────────
  const extractCard = h('section', { class: 'card', 'aria-label': 'Извлечение требований' });
  const extractJob = h('div');
  const reviewCard = h('section', { class: 'card card--flush', 'aria-label': 'Требования' });
  mount(reqPane, extractCard, extractJob, reviewCard);

  const limitInput = h('input', {
    class: 'input input--num', type: 'number', min: 1, step: 1, value: st.limit, 'aria-label': 'Сколько фрагментов обработать за раз',
    onInput: (e) => { st.limit = Math.max(1, parseInt(e.target.value, 10) || 1); },
  });
  const runBtn = button({
    label: 'Извлечь факты', icon: 'sparkles', variant: 'primary',
    onClick: () => withBusy(runBtn, async () => {
      try {
        st.resultDismissed = true;
        await startJob(() => api.post('/extraction/run', { limit: Math.min(st.limit, pendingSelected() || st.limit), kinds: st.kinds }));
      } catch (e) { toastError(e); }
    }),
  });

  // виды фактов (версия 2): сколько фрагментов ждут обработки хотя бы одним выбранным видом
  const KIND_LABELS = { requirements: 'Требования', processes: 'Процессы', integration: 'Обмены и ответственность' };
  const pendingSelected = () => {
    const by = st.pending?.by_kind || { requirements: st.pending?.pending || 0 };
    return Math.max(0, ...st.kinds.map((k) => by[k] || 0));
  };
  const kindChips = chipGroup({
    label: 'Что извлекать', selected: st.kinds,
    options: Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label, count: 0 })),
    onChange: (v) => { st.kinds = v; extractSig = ''; renderExtractCard(); },
  });

  let extractSig = '';
  function renderExtractCard() {
    const p = st.pending;
    const running = jobs.get().job?.state === 'running';
    const count = p ? pendingSelected() : 0;
    const sig = `${p ? JSON.stringify(p.by_kind) : 'x'}/${st.kinds.join()}/${running}/${llmOk()}`;
    if (sig === extractSig) return;
    extractSig = sig;
    kindChips.setCounts(p?.by_kind || {});
    runBtn.disabled = !count || !st.kinds.length || !llmOk() || running;
    runBtn.title = running ? 'Дождитесь завершения текущей задачи' : !llmOk() ? 'Нужна запущенная Ollama с моделью' : !st.kinds.length ? 'Выберите, что извлекать' : '';
    limitInput.max = Math.max(1, count);
    limitInput.disabled = !count;
    if (count) st.limit = Math.min(st.limit, count);
    limitInput.value = count ? st.limit : '';
    mount(extractCard,
      h('div', { class: 'row row--wrap', style: { gap: '20px', alignItems: 'center' } },
        h('span', { class: 'card__icon card__icon--accent' }, icon('sparkles', 18)),
        h('div', { style: { flex: '1 1 340px', minWidth: 0 } },
          h('div', null, 'Необработанных фрагментов про AMR/RMS: ', h('strong', { class: 'num', style: { fontSize: '18px' } }, p ? String(count) : '…')),
          h('p', { class: 'card__sub', style: { marginTop: '4px' } }, 'Каждый выбранный вид — отдельный запрос к модели на фрагмент. На ноутбуке без видеокарты один запрос идёт от нескольких секунд до минуты: большой архив обрабатывайте частями или оставьте на ночь.'),
          h('div', { style: { marginTop: '10px' } }, kindChips),
          !llmOk() && h('p', { class: 'card__sub', style: { marginTop: '6px', color: 'var(--warning)' } }, 'Для извлечения нужна запущенная Ollama с моделью (см. статус вверху).')),
        h('label', { class: 'field', style: { alignItems: 'flex-start' } }, h('span', { class: 'field__label' }, 'Фрагментов за раз'), limitInput),
        runBtn));
  }

  let extractProgress = null;
  function renderExtractJob() {
    const job = jobs.get().job;
    if (job && job.kind === 'extraction' && job.state === 'running') {
      if (!extractProgress || extractProgress.dataset.id !== job.id) {
        extractProgress = jobProgress(job);
        extractProgress.dataset.id = job.id;
        mount(extractJob, h('div', { class: 'card' }, extractProgress));
      } else extractProgress.update(job);
      return;
    }
    extractProgress = null;
    const res = jobs.get().results.extraction;
    if (st.resultDismissed || !res) { mount(extractJob); return; }
    if (res.state === 'error') {
      mount(extractJob, notice({ tone: 'danger', title: 'Извлечение прервано', body: res.error,
        actions: button({ label: 'Закрыть', variant: 'ghost', size: 'sm', onClick: () => { st.resultDismissed = true; renderExtractJob(); } }) }));
      return;
    }
    const r = res.result || {};
    mount(extractJob, h('section', { class: 'card', 'aria-label': 'Итог извлечения' },
      h('div', { class: 'card__head' },
        h('span', { class: 'card__icon', style: { background: 'var(--success-soft)', color: 'var(--success)' } }, icon('circle-check', 18)),
        h('h2', { class: 'card__title' }, 'Извлечение завершено'), h('span', { class: 'spacer' }),
        button({ iconOnly: true, icon: 'x', variant: 'ghost', size: 'sm', label: 'Скрыть итог', onClick: () => { st.resultDismissed = true; renderExtractJob(); } })),
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px' } },
        [['Найдено кандидатов', r.found], ['Принято', r.accepted], ['Отброшено: цитата не подтверждена', r.rejected_quotes], ['Ошибок', r.errors]].map(([label, v]) =>
          h('div', null, h('div', { class: 'stat__label' }, label),
            h('div', { class: 'num', style: { fontSize: '26px', fontWeight: 300, letterSpacing: '-0.03em', color: label === 'Ошибок' && v ? 'var(--danger)' : 'var(--text)' } }, String(v ?? 0))))),
      r.by_kind && h('div', { class: 'row row--wrap', style: { gap: '8px', marginTop: '14px' } },
        Object.entries(r.by_kind).map(([k, v]) => tag(`${KIND_LABELS[k] || k}: принято ${v.accepted}`, k === 'requirements')),
        (r.by_kind.processes?.accepted || r.by_kind.integration?.accepted) ? h('a', { class: 'btn btn--soft btn--sm', href: '#/systems' }, icon('network', 15), 'Логистика и системы') : null)));
  }

  // ───────── таблица ревью ─────────
    let statusChips = null;
  const toolbarHost = h('div', { class: 'toolbar' });
  const tableHost = h('div', { class: 'table-wrap' });
  const footHost = h('div');
  mount(reviewCard, toolbarHost, tableHost, footHost);
  const searchEl = searchBox({ placeholder: 'Поиск по тексту и цитате', onInput: debounce((v) => { st.fText = v; st.shown = PAGE; loadReqs(); }, 250) });
  const confirmAllBtn = button({ label: 'Подтвердить все показанные', icon: 'badge-check', variant: 'soft', size: 'sm', onClick: confirmShown });
  const countEl = h('span', { class: 'muted num', style: { fontSize: '12.5px' } });

  function buildToolbar() {
    const m = meta();
    const targets = chipGroup({ label: 'Кому адресовано', options: m?.targets || [], selected: st.fTargets, onChange: (v) => { st.fTargets = v; st.shown = PAGE; loadReqs(); } });
    statusChips = chipGroup({
      label: 'Статус',
      options: Object.entries(m?.requirement_statuses || {}).map(([value, label]) => ({ value, label, count: st.byStatus[value] ?? 0 })),
      selected: st.fStatuses, onChange: (v) => { st.fStatuses = v; st.shown = PAGE; loadReqs(); },
    });
    mount(toolbarHost,
      h('div', { class: 'row row--wrap', style: { width: '100%', gap: '12px' } }, h('div', { style: { flex: '1 1 260px', maxWidth: '380px' } }, searchEl), targets, h('span', { class: 'spacer' }), countEl, confirmAllBtn),
      h('div', { style: { width: '100%' } }, statusChips));
  }

  function shownAuto() { return st.reqs.filter((r) => r.status === 'auto'); }

  async function confirmShown() {
    const ids = shownAuto().map((r) => r.id);
    if (!ids.length) return;
    const ok = await confirmDialog({
      title: `Подтвердить ${withNoun(ids.length, ['требование', 'требования', 'требований'])}?`,
      text: 'Все показанные требования со статусом «найдено моделью» станут подтверждёнными и попадут в итоговый отчёт.',
      confirmLabel: 'Подтвердить',
    });
    if (!ok) return;
    try {
      const res = await api.post('/requirements/confirm', { ids });
      toast(`Подтверждено: ${res.confirmed}`);
      loadReqs({ silent: true });
    } catch (e) { toastError(e); }
  }

  function renderReqTable() {
    const auto = shownAuto().length;
    confirmAllBtn.disabled = !auto;
    confirmAllBtn.lastChild.textContent = auto ? `Подтвердить все показанные · ${auto}` : 'Подтвердить все показанные';
    countEl.textContent = st.loadingReqs ? '' : `Показано ${Math.min(st.shown, st.reqs.length)} из ${st.reqTotal}`;
    statusChips?.setCounts(Object.fromEntries(Object.keys(meta()?.requirement_statuses || {}).map((k) => [k, st.byStatus[k] || 0])));
    if (st.loadingReqs && !st.reqs.length) { mount(tableHost, skeletonRows(5)); mount(footHost); return; }
    if (!st.reqTotal) {
      mount(tableHost, emptyState({ icon: 'list-checks', title: 'Требований пока нет', text: 'Запустите извлечение: модель найдёт требования к AMR/RMS и приведёт цитату из документа для каждого.' }));
      mount(footHost);
      return;
    }
    if (!st.reqs.length) {
      mount(tableHost, emptyState({ icon: 'search', title: 'Под фильтр ничего не попало', text: 'Измените статус, адресата или текст запроса.' }));
      mount(footHost);
      return;
    }
    const rows = st.reqs.slice(0, st.shown);
    mount(tableHost, h('table', { class: 'table' },
      h('thead', null, h('tr', null, ['ID', 'Требование', 'Кому · категория', 'Обязательность', 'Статус', 'Источник', ''].map((t) => h('th', null, t)))),
      h('tbody', null, rows.map(reqRow))));
    mount(footHost, st.reqs.length > st.shown && h('div', { class: 'table-foot' },
      h('span', null, `Показано ${st.shown} из ${st.reqs.length}`),
      button({ label: `Показать ещё ${Math.min(PAGE, st.reqs.length - st.shown)}`, size: 'sm', onClick: () => { st.shown += PAGE; renderReqTable(); } })));
  }

  function reqRow(r) {
    const rowActions = h('td', { class: 'cell-actions' },
      r.status !== 'confirmed' && h('button', { class: 'iconbtn iconbtn--sm iconbtn--ok', type: 'button', title: 'Подтвердить', 'aria-label': `Подтвердить ${r.code}`, onClick: (e) => { e.stopPropagation(); quickStatus(r, 'confirmed'); } }, icon('check', 16, 2.2)),
      r.status !== 'rejected' && h('button', { class: 'iconbtn iconbtn--sm iconbtn--bad', type: 'button', title: 'Отклонить', 'aria-label': `Отклонить ${r.code}`, onClick: (e) => { e.stopPropagation(); quickStatus(r, 'rejected'); } }, icon('x', 16, 2.2)));
    return h('tr', {
      class: ['is-click', r.id === st.activeId ? 'is-active' : '', r.status === 'rejected' ? 'is-dim' : ''].join(' '),
      tabIndex: 0, onClick: () => openReq(r.id),
      onKeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openReq(r.id); },
    },
      h('td', { class: 'col-narrow' }, h('span', { class: 'tag' }, r.code)),
      h('td', { style: { minWidth: '280px' } },
        h('div', { class: 'clamp-2', style: { maxWidth: '560px', fontWeight: 550 }, title: r.text }, r.text),
        r.comment && h('div', { class: 'cell-sub row', style: { gap: '6px', marginTop: '4px' } }, icon('square-pen', 12), h('span', { class: 'truncate', style: { maxWidth: '480px' } }, r.comment))),
      h('td', null, h('div', { class: 'row', style: { gap: '8px' } }, tag(r.target, true), h('span', { class: 'muted nowrap' }, r.category))),
      h('td', null, obligationPill(r.obligation)),
      h('td', null, reqStatusPill(r.status, statusLabel(r.status))),
      h('td', { style: { maxWidth: '220px' } }, h('div', { class: 'truncate muted', title: r.source }, splitPath(r.rel_path).name), r.page != null && h('div', { class: 'cell-sub' }, `с. ${Math.trunc(r.page)}`)),
      rowActions);
  }

  async function quickStatus(r, status) {
    try {
      const updated = await api.patch(`/requirements/${r.id}`, { status });
      patchLocal(updated);
      renderReqTable();
      loadReqs({ silent: true });
    } catch (e) { toastError(e); }
  }

  function patchLocal(updated) {
    const i = st.reqs.findIndex((x) => x.id === updated.id);
    if (i >= 0) { const { chunk_text, quote, ...slim } = updated; st.reqs[i] = { ...st.reqs[i], ...slim }; }
  }

  // ───────── панель «Проверка по источнику» ─────────
  async function openReq(id) {
    let index = st.reqs.findIndex((r) => r.id === id);
    if (index < 0) return;
    const idxNow = () => st.reqs.findIndex((r) => r.id === st.activeId);
    st.activeId = id;
    renderReqTable();
    const drawer = openDrawer({ title: st.reqs[index].code, subtitle: 'Проверка по источнику', body: [h('div', { class: 'stack' }, skeleton('100%', 60), skeleton('100%', 120))],
      onClose: () => { st.activeId = null; st.onReqsChanged = null; renderReqTable(); } });
    await show(index);

    async function show(i) {
      index = i;
      const base = st.reqs[i];
      st.activeId = base.id;
      renderReqTable();
      drawer.setTitle(`${base.code} · ${statusLabel(base.status)}`, base.source);
      drawer.setBody(h('div', { class: 'stack' }, skeleton('100%', 60), skeleton('100%', 140)));
      drawer.setHeaderExtra();
      drawer.setFooter();
      let r;
      try { r = await api.get(`/requirements/${base.id}`); } catch (e) { toastError(e); drawer.close(); return; }
      if (st.activeId !== base.id) return; // пока грузилось, перешли на другое
      drawReq(r);
    }

    function drawReq(r) {
      const m = meta();
      const form = { text: r.text, target: r.target, category: r.category, obligation: r.obligation, comment: r.comment || '', status: r.status };
      const diff = () => {
        const out = {};
        for (const k of Object.keys(form)) if (String(form[k]) !== String(k === 'comment' ? (r.comment || '') : r[k])) out[k] = form[k];
        return out;
      };
      const saveBtn = button({ label: 'Сохранить', icon: 'check', variant: 'primary', disabled: true, onClick: () => withBusy(saveBtn, () => save()) });
      const confirmBtn = button({ label: 'Подтвердить', icon: 'badge-check', variant: 'soft', onClick: () => withBusy(confirmBtn, () => save('confirmed')) });
      const rejectBtn = button({ label: 'Отклонить', icon: 'circle-x', variant: 'danger', onClick: () => withBusy(rejectBtn, () => save('rejected')) });
      const prev = button({ iconOnly: true, icon: 'chevron-left', variant: 'ghost', size: 'sm', label: 'Предыдущее требование', onClick: () => show(idxNow() - 1) });
      const next = button({ iconOnly: true, icon: 'chevron-right', variant: 'ghost', size: 'sm', label: 'Следующее требование', onClick: () => show(idxNow() + 1) });
      const counter = h('span', { class: 'muted num', style: { fontSize: '12.5px', minWidth: '54px', textAlign: 'center' } });
      const sync = () => {
        const dirty = Object.keys(diff()).length > 0;
        const i = idxNow();
        counter.textContent = i >= 0 ? `${i + 1} из ${st.reqs.length}` : '';
        saveBtn.disabled = !dirty;
        prev.disabled = dirty || i <= 0;
        next.disabled = dirty || i < 0 || i >= st.reqs.length - 1;
        const t = dirty ? 'Сначала сохраните или отмените правки' : '';
        prev.title = next.title = t;
      };
      async function save(forceStatus) {
        const patch = diff();
        if (forceStatus && form.status !== forceStatus) patch.status = forceStatus;
        if (!Object.keys(patch).length) {
          if (forceStatus && idxNow() < st.reqs.length - 1) show(idxNow() + 1);
          return;
        }
        try {
          const updated = await api.patch(`/requirements/${r.id}`, patch);
          patchLocal(updated);
          toast(forceStatus === 'confirmed' ? 'Требование подтверждено' : forceStatus === 'rejected' ? 'Требование отклонено' : 'Сохранено');
          renderReqTable();
          loadReqs({ silent: true });
          // при подтверждении сразу идём к следующему: так удобнее проверять подряд
          if (forceStatus && idxNow() < st.reqs.length - 1) show(idxNow() + 1); else drawReq(updated);
        } catch (e) { toastError(e); }
      }

      const quoteScore = r.quote_score ?? 0;
      const quoteBlock = h('section', null,
        h('div', { class: 'row row--between', style: { marginBottom: '8px' } },
          h('div', { class: 'row', style: { gap: '8px' } }, icon('quote', 16), h('strong', null, 'Цитата из документа')),
          h('span', { class: 'row muted', style: { gap: '10px', fontSize: '12.5px' }, title: 'Насколько точно цитата совпадает с текстом документа' }, 'совпадение', ticks(quoteScore, { count: 12, xs: true, tone: quoteScore >= 0.9 ? 'ok' : 'warn' }), h('strong', { class: 'num' }, pct(quoteScore)))),
        h('blockquote', { class: 'quote' }, r.quote || '—'),
        r.chunk_text && h('details', { class: 'disclosure', style: { marginTop: '12px' } },
          h('summary', { style: { fontSize: '13px' } }, 'Весь фрагмент документа', icon('chevron-down', 16)),
          h('div', { class: 'fragment' }, highlight(r.chunk_text, r.quote))));

      const textArea = h('textarea', { class: 'textarea', rows: 4, 'aria-label': 'Текст требования', onInput: (e) => { form.text = e.target.value; sync(); } }, r.text);
      const commentArea = h('textarea', { class: 'textarea', rows: 2, placeholder: 'Заметка для себя или коллег', onInput: (e) => { form.comment = e.target.value; sync(); } }, r.comment || '');
      textArea.value = r.text; commentArea.value = r.comment || '';
      const statusOptions = Object.entries(m?.requirement_statuses || {}).map(([value, label]) => ({ value, label }));

      drawer.setTitle(`${r.code} · ${statusLabel(r.status)}`, r.source);
      drawer.setBody(
        quoteBlock,
        h('div', { class: 'form-grid' },
          h('div', { class: 'span-2' }, field('Текст требования', textArea)),
          h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Кому адресовано'),
            segmented({ options: m?.targets || [], value: form.target, label: 'Кому адресовано', block: true, onChange: (v) => { form.target = v; sync(); } })),
          field('Категория', selectEl({ options: m?.categories || [], value: form.category, onChange: (v) => { form.category = v; sync(); } })),
          field('Статус', selectEl({ options: statusOptions, value: form.status, onChange: (v) => { form.status = v; sync(); } })),
          h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Обязательность'),
            segmented({ options: m?.obligations || [], value: form.obligation, label: 'Обязательность', block: true, onChange: (v) => { form.obligation = v; sync(); } })),
          h('div', { class: 'span-2' }, field('Комментарий', commentArea))));
      drawer.setHeaderExtra(prev, counter, next);
      drawer.setFooter(rejectBtn, h('span', { class: 'spacer' }), confirmBtn, saveBtn);
      st.onReqsChanged = sync;
      sync();
    }
  }

  // подсветка цитаты внутри фрагмента (если найдена дословно)
  function highlight(text, quote) {
    const q = (quote || '').trim();
    const i = q ? text.indexOf(q) : -1;
    if (i < 0) return text;
    return [text.slice(0, i), h('mark', null, q), text.slice(i + q.length)];
  }

  // ───────── загрузка требований ─────────
  async function loadPending() {
    try { st.pending = await api.get('/extraction/pending'); } catch (e) { toastError(e); }
    renderExtractCard();
  }

  async function loadReqs({ silent = false } = {}) {
    const token = ++reqToken;
    if (!silent) { st.loadingReqs = true; renderReqTable(); }
    try {
      const res = await api.get('/requirements', { target: st.fTargets, status: st.fStatuses, q: st.fText });
      if (token !== reqToken) return;
      st.reqs = res.items; st.reqTotal = res.total; st.byStatus = res.by_status;
    } catch (e) { if (token === reqToken) toastError(e); }
    finally { if (token === reqToken) { st.loadingReqs = false; renderReqTable(); st.onReqsChanged?.(); } }
  }

  // ═════════════════════════ ОТКРЫТЫЕ ВОПРОСЫ ═════════════════════════
  const qCard = h('section', { class: 'card card--flush', 'aria-label': 'Открытые вопросы' });
  mount(qPane, h('p', { class: 'muted', style: { maxWidth: '70ch' } }, 'Вопросы к заказчику: противоречия документов и отсутствующие решения.'), qCard);
  const qToolbar = h('div', { class: 'toolbar' });
  const qTable = h('div', { class: 'table-wrap' });
  mount(qCard, qToolbar, qTable);
  let qChips = null;

  function qCounts() {
    const c = {};
    st.questions.forEach((q) => { c[q.status] = (c[q.status] || 0) + 1; });
    return c;
  }

  function buildQToolbar() {
    qChips = chipGroup({
      label: 'Статус вопросов', selected: st.qFilter,
      options: Q_STATUSES.map((value) => ({ value, label: QUESTION_LABELS[value], count: 0 })),
      onChange: (v) => { st.qFilter = v; renderQ(); },
    });
    mount(qToolbar, qChips, h('span', { class: 'spacer' }),
      button({ label: 'Добавить вопрос', icon: 'plus', variant: 'primary', size: 'sm', onClick: () => openQuestion(null) }));
  }

  function renderQ() {
    const counts = qCounts();
    qChips?.setCounts(Object.fromEntries(Q_STATUSES.map((s) => [s, counts[s] || 0])));
    tabsEl.setCount('questions', st.questions.filter((q) => q.status !== 'closed').length);
    if (st.loadingQ) { mount(qTable, skeletonRows(3)); return; }
    if (!st.questions.length) {
      mount(qTable, emptyState({
        icon: 'circle-help', title: 'Открытых вопросов нет',
        text: 'Добавляйте вопросы к заказчику здесь или прямо из ответа на экране «Вопросы к архиву», когда находите противоречие или пробел.',
        action: button({ label: 'Добавить вопрос', icon: 'plus', variant: 'soft', onClick: () => openQuestion(null) }),
      }));
      return;
    }
    const list = st.qFilter.length ? st.questions.filter((q) => st.qFilter.includes(q.status)) : st.questions;
    if (!list.length) { mount(qTable, emptyState({ icon: 'search', title: 'Под фильтр ничего не попало' })); return; }
    mount(qTable, h('table', { class: 'table' },
      h('thead', null, h('tr', null, ['№', 'Вопрос', 'Источники', 'Статус', 'Ответ заказчика', ''].map((t) => h('th', null, t)))),
      h('tbody', null, list.map((q) => h('tr', { class: 'is-click', tabIndex: 0, onClick: () => openQuestion(q), onKeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openQuestion(q); } },
        h('td', { class: 'col-narrow' }, h('span', { class: 'tag' }, `#${q.id}`)),
        h('td', { style: { minWidth: '260px' } },
          h('div', { class: 'clamp-2', style: { maxWidth: '520px', fontWeight: 550 } }, q.text),
          q.context && h('div', { class: 'cell-sub truncate', style: { maxWidth: '520px', marginTop: '3px' } }, q.context)),
        h('td', { style: { maxWidth: '240px' } }, q.sources ? h('div', { class: 'cell-sub clamp-2' }, q.sources) : h('span', { class: 'faint' }, '—')),
        h('td', { onClick: (e) => e.stopPropagation() }, selectEl({
          small: true, ariaLabel: `Статус вопроса ${q.id}`, value: q.status,
          options: Q_STATUSES.map((value) => ({ value, label: QUESTION_LABELS[value] })),
          onChange: async (v) => {
            try { Object.assign(q, await api.patch(`/open-questions/${q.id}`, { status: v })); renderQ(); } catch (e) { toastError(e); renderQ(); }
          },
        })),
        h('td', { style: { maxWidth: '280px' } }, q.answer ? h('div', { class: 'clamp-2' }, q.answer) : h('span', { class: 'faint' }, '—')),
        h('td', { class: 'cell-actions' }, h('button', {
          class: 'iconbtn iconbtn--sm iconbtn--bad', type: 'button', title: 'Удалить', 'aria-label': `Удалить вопрос ${q.id}`,
          onClick: (e) => { e.stopPropagation(); removeQuestion(q); },
        }, icon('trash-2', 15))))))));
  }

  async function removeQuestion(q) {
    const ok = await confirmDialog({ title: 'Удалить вопрос?', text: q.text.length > 160 ? q.text.slice(0, 160) + '…' : q.text, confirmLabel: 'Удалить', danger: true });
    if (!ok) return;
    try {
      await api.del(`/open-questions/${q.id}`);
      st.questions = st.questions.filter((x) => x.id !== q.id);
      renderQ();
      toast('Вопрос удалён');
    } catch (e) { toastError(e); }
  }

  function openQuestion(q) {
    const creating = !q;
    const form = { text: q?.text || '', context: q?.context || '', sources: q?.sources || '', status: q?.status || 'open', answer: q?.answer || '' };
    const textArea = h('textarea', { class: 'textarea', rows: 4, placeholder: 'Что нужно уточнить у заказчика?', onInput: (e) => { form.text = e.target.value; sync(); } }, form.text);
    const ctxArea = h('textarea', { class: 'textarea', rows: 3, placeholder: 'В чём противоречие или пробел', onInput: (e) => { form.context = e.target.value; } }, form.context);
    const ansArea = h('textarea', { class: 'textarea', rows: 3, placeholder: 'Когда заказчик ответит, запишите ответ', onInput: (e) => { form.answer = e.target.value; sync(); } }, form.answer);
    textArea.value = form.text; ctxArea.value = form.context; ansArea.value = form.answer;
    const save = button({
      label: creating ? 'Добавить' : 'Сохранить', icon: 'check', variant: 'primary', disabled: creating,
      onClick: () => withBusy(save, async () => {
        const body = { text: form.text.trim(), context: form.context || null, sources: form.sources || null, status: form.status, answer: form.answer || null };
        try {
          if (creating) st.questions.push(await api.post('/open-questions', body));
          else Object.assign(q, await api.patch(`/open-questions/${q.id}`, body));
          renderQ();
          drawer.close();
          toast(creating ? 'Вопрос добавлен' : 'Сохранено');
        } catch (e) { toastError(e); }
      }),
    });
    const sync = () => { save.disabled = !form.text.trim(); };
    const drawer = openDrawer({
      title: creating ? 'Новый вопрос заказчику' : `Вопрос #${q.id}`, subtitle: creating ? '' : QUESTION_LABELS[q.status],
      body: [h('div', { class: 'form-grid' },
        h('div', { class: 'span-2' }, field('Вопрос', textArea)),
        h('div', { class: 'span-2' }, field('Контекст', ctxArea)),
        h('div', { class: 'span-2' }, field('Источники', h('input', { class: 'input', type: 'text', value: form.sources, placeholder: 'Документ, с. 5; другой документ', onInput: (e) => { form.sources = e.target.value; } }))),
        h('div', { class: 'span-2' }, field('Статус', selectEl({ options: Q_STATUSES.map((value) => ({ value, label: QUESTION_LABELS[value] })), value: form.status, onChange: (v) => { form.status = v; } }))),
        h('div', { class: 'span-2' }, field('Ответ заказчика', ansArea)))],
      footer: [save, button({ label: 'Отмена', variant: 'ghost', onClick: () => drawer.close() })],
    });
    setTimeout(() => textArea.focus(), 60);
  }

  async function loadQuestions() {
    try { st.questions = await api.get('/open-questions'); } catch (e) { toastError(e); }
    st.loadingQ = false;
    renderQ();
  }

  // ───────── подписки и старт ─────────
  cleanup.push(jobs.subscribe(() => { renderExtractJob(); renderExtractCard(); }));
  cleanup.push(jobEvents.on('finish', (job) => {
    if (job.kind === 'extraction') { st.resultDismissed = false; renderExtractJob(); loadPending(); loadReqs({ silent: true }); conflictsPane.load(); }
  }));
  cleanup.push(app.subscribe(() => renderExtractCard()));

  renderTab();
  renderExtractCard();
  renderExtractJob();
  buildToolbar();
  buildQToolbar();
  renderReqTable();
  renderQ();
  loadPending();
  loadReqs();
  loadQuestions();
  conflictsPane.load();

  return { el: root, destroy: () => cleanup.forEach((fn) => fn()) };
}
