// Экран 1. Архив и обработка: загрузка, ход конвейера, каталог документов с ручной правкой карточек.
import { api } from '../api.js';
import { app, llmOk } from '../app-state.js';
import {
  button, emptyState, field, jobProgress, level, notice, pill, searchBox, segmented, selectEl, skeletonRows, switchEl, tag, ticks, withBusy,
} from '../components.js';
import { debounce, h, mount } from '../dom.js';
import { fmtBytes, fmtDate, plural, splitPath, withNoun } from '../format.js';
import { icon } from '../icons.js';
import { jobEvents, jobs, startJob, track } from '../jobs.js';
import { approvalPill, docStatusPill, sidePill } from '../labels.js';
import { openDrawer } from '../overlay.js';
import { toast, toastError } from '../toast.js';

const PAGE = 100;
const SOURCE_KEY = 'da.source';

export function createArchivePage(ctx) {
  const st = {
    summary: null,
    docs: [],
    total: 0,
    query: '',
    onlyRelevant: false,
    hideOutdated: false,
    limit: PAGE,
    loading: true,
    activeId: null,
    resultDismissed: false,
    uploading: null, // {loaded, total}
  };
  let reqToken = 0;
  const cleanup = [];

  // ───────── каркас ─────────
  const el = {
    head: h('div', { class: 'page-head' }),
    source: h('section', { class: 'card', 'aria-labelledby': 'src-title' }),
    job: h('div'),
    body: h('div', { class: 'stack', style: { gap: '16px' } }),
  };
  const root = h('div', null, el.head, el.source, el.job, h('div', { style: { height: '16px' } }), el.body);

  // ───────── шапка страницы ─────────
  function renderHead() {
    const s = st.summary;
    let sub = 'Загрузите проектную документацию заказчика и подрядчика: приложение извлечёт текст, составит карточки и найдёт устаревшие редакции.';
    if (s && s.files) {
      const parts = [`${withNoun(s.files, ['документ', 'документа', 'документов'])} в каталоге`];
      if (s.need_manual) parts.push(`${s.need_manual} ${plural(s.need_manual, ['требует', 'требуют', 'требуют'])} ручной обработки`);
      sub = parts.join(', ') + '.';
    }
    mount(el.head, h('div', null, h('h1', { class: 'page-title' }, 'Архив и обработка'), h('p', { class: 'page-sub' }, sub)));
  }

  // ───────── источник документов ─────────
  const pathInput = h('input', {
    class: 'input', id: 'src-path', type: 'text', autocomplete: 'off', spellcheck: false,
    placeholder: 'D:\\Projects\\AMR\\archive', 'aria-label': 'Путь к папке или архиву',
    value: (() => { try { return localStorage.getItem(SOURCE_KEY) || ''; } catch { return ''; } })(),
    onKeydown: (e) => { if (e.key === 'Enter') runBtn.click(); },
  });
  const runBtn = button({
    label: 'Запустить обработку', icon: 'play', variant: 'primary',
    onClick: () => withBusy(runBtn, async () => {
      const path = pathInput.value.trim().replace(/^"|"$/g, '');
      if (!path) { pathInput.focus(); toast('Укажите путь к папке или архиву', { type: 'info' }); return; }
      try { localStorage.setItem(SOURCE_KEY, path); } catch { /* ignore */ }
      try {
        st.resultDismissed = true;
        await startJob(() => api.post('/archive/process', { path }));
      } catch (e) { toastError(e); }
    }),
  });
  const fileInput = h('input', { type: 'file', accept: '.zip,application/zip', class: 'sr-only', tabIndex: -1, 'aria-hidden': 'true', onChange: (e) => { const f = e.target.files[0]; e.target.value = ''; if (f) uploadZip(f); } });
  const dropzone = h('button', { class: 'dropzone', type: 'button', onClick: () => fileInput.click() });
  ['dragenter', 'dragover'].forEach((t) => dropzone.addEventListener(t, (e) => { e.preventDefault(); dropzone.classList.add('is-over'); }));
  ['dragleave', 'drop'].forEach((t) => dropzone.addEventListener(t, (e) => { e.preventDefault(); dropzone.classList.remove('is-over'); }));
  dropzone.addEventListener('drop', (e) => { const f = e.dataTransfer?.files?.[0]; if (f) uploadZip(f); });

  async function uploadZip(file) {
    if (!/\.zip$/i.test(file.name)) { toast('Нужен файл .zip', { type: 'error' }); return; }
    st.uploading = { name: file.name, loaded: 0, total: file.size };
    st.resultDismissed = true;
    syncSource();
    try {
      const job = await api.upload('/archive/upload', file, (loaded, total) => {
        st.uploading = { name: file.name, loaded, total };
        renderDropzone();
      });
      track(job);
    } catch (e) {
      toastError(e);
    } finally {
      st.uploading = null;
      syncSource();
    }
  }

  function renderDropzone() {
    const u = st.uploading;
    const busy = jobs.get().job?.state === 'running';
    dropzone.disabled = !!u || busy;
    dropzone.title = busy && !u ? 'Дождитесь завершения текущей задачи' : '';
    if (u) {
      const ratio = u.total ? u.loaded / u.total : 0;
      mount(dropzone,
        h('span', { class: 'dropzone__icon' }, icon('loader-circle', 20)),
        h('span', { style: { flex: 1, minWidth: 0 } },
          h('span', { class: 'dropzone__title truncate', style: { display: 'block' } }, `Загрузка ${u.name}`),
          h('span', { class: 'progress', style: { marginTop: '8px' } }, h('span', { class: 'progress__track' }, h('span', { class: 'progress__bar', style: { width: `${ratio * 100}%`, display: 'block' } }))),
          h('span', { class: 'dropzone__hint' }, `${fmtBytes(u.loaded)} из ${fmtBytes(u.total)}`)));
      dropzone.firstChild.firstChild.classList.add('spin');
      return;
    }
    mount(dropzone,
      h('span', { class: 'dropzone__icon' }, icon('file-up', 20)),
      h('span', null,
        h('span', { class: 'dropzone__title', style: { display: 'block' } }, '…или загрузите zip-архив сюда'),
        h('span', { class: 'dropzone__hint' }, 'Перетащите файл или нажмите, чтобы выбрать. Вложенные архивы и русские имена файлов поддерживаются.')));
  }

  function syncSource() {
    const busy = jobs.get().job?.state === 'running';
    runBtn.disabled = busy || !!st.uploading;
    runBtn.title = busy ? 'Дождитесь завершения текущей задачи' : '';
    renderDropzone();
  }

  function renderSource() {
    mount(el.source,
      h('div', { class: 'card__head' },
        h('span', { class: 'card__icon card__icon--accent' }, icon('folder-open', 18)),
        h('div', null,
          h('h2', { class: 'card__title', id: 'src-title' }, 'Источник документов'),
          h('p', { class: 'card__sub' }, 'Укажите папку или архив (.zip) на этом компьютере. Файлы никуда не копируются и не отправляются. Повторный запуск добавит только новые файлы.'))),
      h('div', { class: 'row row--wrap', style: { gap: '10px' } }, h('div', { style: { flex: '1 1 320px' } }, pathInput), runBtn),
      h('div', { style: { marginTop: '12px' } }, dropzone, fileInput));
    syncSource();
  }

  // ───────── ход задачи и итог обработки ─────────
  let progressEl = null;
  function renderJob() {
    const job = jobs.get().job;
    const process = job && (job.kind === 'process' || job.kind === 'embeddings');
    if (process && job.state === 'running') {
      if (!progressEl || progressEl.dataset.id !== job.id) {
        progressEl = jobProgress(job);
        progressEl.dataset.id = job.id;
        mount(el.job, h('div', { class: 'card', style: { marginTop: '16px' } }, progressEl));
      } else progressEl.update(job);
      return;
    }
    progressEl = null;
    if (process && job.state === 'error' && !st.resultDismissed) {
      mount(el.job, h('div', { style: { marginTop: '16px' } }, notice({
        tone: 'danger', title: 'Не получилось завершить задачу', body: job.error,
        actions: button({ label: 'Закрыть', variant: 'ghost', size: 'sm', onClick: () => { st.resultDismissed = true; renderJob(); } }),
      })));
      return;
    }
    const res = jobs.get().results.process;
    if (res && res.state === 'done' && !st.resultDismissed) {
      mount(el.job, resultCard(res.result));
    } else if (job?.kind === 'embeddings' && job.state === 'done' && !st.resultDismissed) {
      mount(el.job, h('div', { style: { marginTop: '16px' } }, notice({
        tone: 'success', title: 'Эмбеддинги посчитаны', body: `Обработано фрагментов: ${job.result?.embedded ?? 0}. Смысловой поиск включён.`,
        actions: button({ label: 'Закрыть', variant: 'ghost', size: 'sm', onClick: () => { st.resultDismissed = true; renderJob(); } }),
      })));
    } else mount(el.job);
  }

  function resultCard(r) {
    const items = [
      ['Найдено файлов', r.found], ['Новых', r.added], ['Повторов', r.duplicates], ['Прочитано', r.parsed],
      ['Сканов', r.needs_ocr], ['Неподдерживаемых', r.unsupported], ['Ошибок', r.errors], ['Устаревших редакций', r.superseded],
    ];
    return h('section', { class: 'card', style: { marginTop: '16px' }, 'aria-label': 'Итог обработки' },
      h('div', { class: 'card__head' },
        h('span', { class: 'card__icon', style: { background: 'var(--success-soft)', color: 'var(--success)' } }, icon('circle-check', 18)),
        h('div', null, h('h2', { class: 'card__title' }, 'Обработка завершена'), h('p', { class: 'card__sub' }, r.source)),
        h('span', { class: 'spacer' }),
        button({ iconOnly: true, icon: 'x', variant: 'ghost', size: 'sm', label: 'Скрыть итог', onClick: () => { st.resultDismissed = true; renderJob(); } })),
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '12px' } },
        items.map(([label, v]) => h('div', null,
          h('div', { class: 'stat__label' }, label),
          h('div', { class: 'num', style: { fontSize: '26px', fontWeight: 300, letterSpacing: '-0.03em', color: (label === 'Ошибок' || label === 'Сканов') && v ? 'var(--warning)' : 'var(--text)' } }, String(v ?? 0))))),
      r.messages?.length ? h('div', { class: 'stack', style: { marginTop: '16px', gap: '8px' } }, r.messages.map((m) => notice({ tone: 'warning', body: m }))) : null,
      r.skipped?.length ? h('details', { class: 'disclosure', style: { marginTop: '16px' } },
        h('summary', null, icon('archive', 17), `Пропущено: ${r.skipped.length}`, icon('chevron-down', 16)),
        h('div', { class: 'table-wrap', style: { marginTop: '10px', maxHeight: '280px' } }, h('table', { class: 'table' },
          h('thead', null, h('tr', null, h('th', null, 'Файл'), h('th', null, 'Причина'))),
          h('tbody', null, r.skipped.map((s) => h('tr', null, h('td', { class: 'cell-main' }, s.file), h('td', { class: 'muted' }, s.reason))))))) : null);
  }

  // ───────── сводка, каталог, проблемные документы ─────────
  const statsEl = h('div');
  const embedEl = h('div');
  const catalogEl = h('section', { class: 'card card--flush', 'aria-label': 'Документы' });
  const problemsEl = h('div');
  mount(el.body, statsEl, embedEl, catalogEl, problemsEl);

  function statTile(label, value, { ratio, tone, hint, alert }) {
    return h('div', { class: ['card', 'stat', alert && value > 0 ? 'stat--alert' : ''].join(' ') },
      h('div', { class: 'stat__label' }, label),
      h('div', { class: 'stat__value' }, String(value)),
      h('div', { class: 'stat__foot' }, ticks(ratio, { tone }), hint && h('div', { class: 'stat__hint' }, hint)));
  }

  function renderStats() {
    const s = st.summary;
    if (!s || !s.files) { mount(statsEl); return; }
    const r = (n) => (s.files ? n / s.files : 0);
    mount(statsEl, h('div', { class: 'stats' },
      statTile('Файлов', s.files, { ratio: 1, hint: `${s.chunks} ${plural(s.chunks, ['фрагмент', 'фрагмента', 'фрагментов'])} для поиска` }),
      statTile('Прочитано', s.parsed, { ratio: r(s.parsed), tone: 'ok', hint: `из ${s.files}` }),
      statTile('Про AMR/RMS', s.relevant, { ratio: r(s.relevant), hint: 'актуальные редакции' }),
      statTile('Устаревших редакций', s.outdated, { ratio: r(s.outdated), tone: 'warn', hint: 'заменены новыми' }),
      statTile('Требуют ручной работы', s.need_manual, { ratio: r(s.need_manual), tone: 'danger', alert: true, hint: s.need_manual ? 'скан, формат или ошибка' : 'всё прочитано' })));
  }

  let embedSig = '';
  function renderEmbeddings() {
    const s = st.summary;
    const running = jobs.get().job?.state === 'running';
    const sig = s?.embeddings_pending ? `${s.vectors}/${s.chunks}/${running}/${llmOk()}` : '';
    if (sig === embedSig) return;
    embedSig = sig;
    if (!s?.embeddings_pending) { mount(embedEl); return; }
    const b = button({
      label: 'Досчитать эмбеддинги', icon: 'sparkles', variant: 'soft', disabled: !llmOk() || running,
      title: !llmOk() ? 'Нужна запущенная Ollama с моделью эмбеддингов' : '',
      onClick: () => withBusy(b, async () => {
        try { st.resultDismissed = false; await startJob(() => api.post('/archive/embeddings')); } catch (e) { toastError(e); }
      }),
    });
    mount(embedEl, h('div', { class: 'card card--accent', style: { display: 'flex', alignItems: 'center', gap: '18px', flexWrap: 'wrap', padding: '16px 20px' } },
      h('span', { class: 'card__icon card__icon--accent' }, icon('layers', 18)),
      h('div', { style: { flex: '1 1 320px', minWidth: 0 } },
        h('div', { class: 'card__title' }, 'Смысловой поиск готов не полностью'),
        h('div', { class: 'card__sub' }, `Готово для ${s.vectors} из ${s.chunks} фрагментов. Пока работает поиск по словам.`)),
      h('div', { style: { width: '160px' } }, ticks(s.chunks ? s.vectors / s.chunks : 0, { count: 24 })),
      b));
  }

  // каталог
  const searchInput = searchBox({ placeholder: 'ПР_EWM, протокол, маппинг…', ariaLabel: 'Поиск по названию и пути', onInput: debounce((v) => { st.query = v; st.limit = PAGE; loadDocs(); }, 250) });
  const countEl = h('span', { class: 'muted num', style: { marginLeft: 'auto', fontSize: '12.5px' } });
  const tableHost = h('div', { class: 'table-wrap' });
  const footHost = h('div');
  mount(catalogEl,
    h('div', { class: 'toolbar' },
      h('h2', { class: 'card__title', style: { marginRight: '8px' } }, 'Документы'),
      searchInput,
      switchEl({ label: 'Только про AMR/RMS', checked: st.onlyRelevant, onChange: (v) => { st.onlyRelevant = v; st.limit = PAGE; loadDocs(); } }),
      switchEl({ label: 'Скрыть устаревшие', checked: st.hideOutdated, onChange: (v) => { st.hideOutdated = v; st.limit = PAGE; loadDocs(); } }),
      countEl),
    tableHost, footHost);

  function subsystemTags(text) {
    const list = String(text || '').split(',').map((x) => x.trim()).filter(Boolean);
    if (!list.length) return h('span', { class: 'faint' }, '—');
    return h('div', { class: 'row row--wrap', style: { gap: '4px' } }, list.map((x) => tag(x)));
  }

  function docRow(d) {
    return h('tr', {
      class: ['is-click', d.id === st.activeId ? 'is-active' : '', d.is_current ? '' : 'is-dim'].join(' '),
      tabIndex: 0, title: d.rel_path,
      onClick: () => openDoc(d),
      onKeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDoc(d); } },
    },
      h('td', { class: 'muted num' }, String(d.id)),
      h('td', null, h('div', { class: 'cell-path' }, d.rel_path)),
      h('td', { class: 'muted' }, d.doc_type || '—'),
      h('td', { class: 'nowrap' }, d.doc_date || h('span', { class: 'faint' }, '—')),
      h('td', { class: 'nowrap' }, d.revision || h('span', { class: 'faint' }, '—')),
      h('td', null, approvalPill(d.approval)),
      h('td', null, sidePill(d.side)),
      h('td', null, subsystemTags(d.subsystems)),
      h('td', { class: 'num' }, String(d.relevance ?? 0)),
      h('td', null, d.is_current
        ? pill('да', 'success', { dot: false })
        : pill('нет', 'warning', { dot: false, title: d.superseded_by_path ? `Заменён: ${d.superseded_by_path}` : '' })),
      h('td', null, docStatusPill(d.status)));
  }

  function renderTable() {
    countEl.textContent = st.loading ? '' : `Показано ${Math.min(st.limit, st.docs.length)} из ${st.total}`;
    if (st.loading && !st.docs.length) { mount(tableHost, skeletonRows(6)); mount(footHost); return; }
    if (!st.docs.length) {
      mount(tableHost, emptyState({ icon: 'search', title: 'Под фильтр ничего не попало', text: 'Измените запрос или снимите фильтры.' }));
      mount(footHost);
      return;
    }
    const shown = st.docs.slice(0, st.limit);
    mount(tableHost, h('table', { class: 'table table--docs' },
      h('thead', null, h('tr', null, ['ID', 'Документ', 'Тип', 'Дата', 'Ред.', 'Статус', 'Сторона', 'Подсистемы', 'AMR/RMS', 'Актуальный', 'Обработка'].map((t) => h('th', null, t)))),
      h('tbody', null, shown.map(docRow))));
    mount(footHost, st.docs.length > st.limit && h('div', { class: 'table-foot' },
      h('span', null, `Показано ${st.limit} из ${st.docs.length}`),
      button({ label: `Показать ещё ${Math.min(PAGE, st.docs.length - st.limit)}`, size: 'sm', variant: 'secondary', onClick: () => { st.limit += PAGE; renderTable(); } })));
  }

  function renderProblems() {
    const p = st.summary?.problems || [];
    if (!p.length) { mount(problemsEl); return; }
    mount(problemsEl, h('details', { class: 'card disclosure' },
      h('summary', null, h('span', { class: 'card__icon', style: { background: 'var(--warning-soft)', color: 'var(--warning)' } }, icon('file-warning', 18)),
        `Документы, требующие ручной обработки: ${p.length}`, icon('chevron-down', 17)),
      h('div', { class: 'table-wrap', style: { marginTop: '14px' } }, h('table', { class: 'table' },
        h('thead', null, h('tr', null, h('th', null, 'Документ'), h('th', null, 'Причина'), h('th', null, 'Подробности'))),
        h('tbody', null, p.map((x) => h('tr', null, h('td', { class: 'cell-main' }, x.rel_path), h('td', null, pill(x.reason, 'warning')), h('td', { class: 'muted' }, x.details || '—'))))))));
  }

  function renderEmptyArchive() {
    mount(el.body, h('section', { class: 'card' }, emptyState({
      icon: 'archive', title: 'Архив ещё не загружен',
      text: h('span', null, 'Для пробы можно создать вымышленный демо-архив командой ', h('code', null, 'python scripts/make_demo_archive.py'), ' и указать путь ', h('code', null, 'data\\demo_archive'), '.'),
    })));
  }

  // ───────── карточка документа ─────────
  function openDoc(d) {
    st.activeId = d.id;
    renderTable();
    const types = app.get().meta?.doc_types || [];
    const approvals = app.get().meta?.approvals || [];
    const sides = app.get().meta?.sides || [];
    const form = { doc_type: d.doc_type, doc_date: d.doc_date || '', revision: d.revision || '', approval: d.approval, side: d.side, relevance: d.relevance ?? 0, is_current: !!d.is_current };
    const save = button({ label: 'Сохранить', variant: 'primary', icon: 'check', disabled: true, onClick: () => withBusy(save, doSave) });
    const markDirty = () => { save.disabled = !Object.keys(diff()).length; };
    const diff = () => {
      const out = {};
      const norm = (v) => (v === '' ? null : v);
      if (form.doc_type !== d.doc_type) out.doc_type = form.doc_type;
      if (norm(form.doc_date) !== (d.doc_date ?? null)) out.doc_date = norm(form.doc_date);
      if (norm(form.revision) !== (d.revision ?? null)) out.revision = norm(form.revision);
      if (form.approval !== d.approval) out.approval = form.approval;
      if (form.side !== d.side) out.side = form.side;
      if (Number(form.relevance) !== Number(d.relevance ?? 0)) out.relevance = Number(form.relevance);
      if (form.is_current !== !!d.is_current) out.is_current = form.is_current;
      return out;
    };
    async function doSave() {
      try {
        const updated = await api.patch(`/documents/${d.id}`, diff());
        const i = st.docs.findIndex((x) => x.id === d.id);
        if (i >= 0) st.docs[i] = updated;
        drawer.close();
        toast('Сохранено. Эта карточка больше не будет меняться автоматически.');
        loadSummary();
        renderTable();
      } catch (e) { toastError(e); }
    }

    const info = h('dl', { class: 'kv' },
      h('dt', null, 'Путь'), h('dd', null, d.rel_path),
      h('dt', null, 'Обработка'), h('dd', null, docStatusPill(d.status), d.error && h('div', { class: 'cell-sub', style: { marginTop: '4px' } }, d.error)),
      h('dt', null, 'Фрагменты'), h('dd', null, `${d.n_chunks} для поиска, ${d.n_vec} с эмбеддингами`),
      h('dt', null, 'Подсистемы'), h('dd', null, subsystemTags(d.subsystems)),
      d.superseded_by_path && h('dt', null, 'Заменён'), d.superseded_by_path && h('dd', null, d.superseded_by_path),
      h('dt', null, 'Карточка'), h('dd', null, d.card_source === 'manual' ? 'поправлена вручную: авто-разбор её не меняет' : 'составлена автоматически'));

    const body = [
      info,
      h('div', { class: 'form-grid' },
        h('div', { class: 'span-2' }, field('Тип документа', selectEl({ options: types, value: form.doc_type, onChange: (v) => { form.doc_type = v; markDirty(); } }))),
        field('Дата', h('input', { class: 'input', type: 'text', placeholder: 'ГГГГ-ММ-ДД', value: form.doc_date, onInput: (e) => { form.doc_date = e.target.value.trim(); markDirty(); } })),
        field('Редакция', h('input', { class: 'input', type: 'text', placeholder: '2.3', value: form.revision, onInput: (e) => { form.revision = e.target.value.trim(); markDirty(); } })),
        h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Статус документа'),
          segmented({ options: approvals, value: form.approval, label: 'Статус документа', block: true, onChange: (v) => { form.approval = v; markDirty(); } })),
        h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Сторона-автор'),
          segmented({ options: sides, value: form.side, label: 'Сторона', block: true, onChange: (v) => { form.side = v; markDirty(); } })),
        h('div', { class: 'span-2 field' }, h('span', { class: 'field__label' }, 'Релевантность AMR/RMS'),
          segmented({
            options: [{ value: 0, label: '0 · нет', title: 'Не относится' }, { value: 1, label: '1' }, { value: 2, label: '2' }, { value: 3, label: '3 · ключевой' }],
            value: form.relevance, label: 'Релевантность', block: true, onChange: (v) => { form.relevance = v; markDirty(); },
          })),
        h('div', { class: 'span-2' }, switchEl({ label: 'Актуальная редакция', checked: form.is_current, onChange: (v) => { form.is_current = v; markDirty(); } }))),
    ];
    const drawer = openDrawer({
      title: splitPath(d.rel_path).name, subtitle: d.title && d.title !== splitPath(d.rel_path).name ? d.title : splitPath(d.rel_path).dir,
      body,
      footer: [save, button({ label: 'Отмена', variant: 'ghost', onClick: () => drawer.close() })],
      onClose: () => { st.activeId = null; renderTable(); },
    });
  }

  // ───────── загрузка данных ─────────
  async function loadSummary() {
    try {
      st.summary = await api.get('/archive/summary');
      renderHead(); renderStats(); renderEmbeddings(); renderProblems();
    } catch (e) { toastError(e); }
  }

  async function loadDocs() {
    const token = ++reqToken;
    st.loading = true;
    renderTable();
    try {
      const res = await api.get('/documents', { query: st.query, only_relevant: st.onlyRelevant, hide_outdated: st.hideOutdated });
      if (token !== reqToken) return;
      st.docs = res.items;
      st.total = res.total;
    } catch (e) {
      if (token === reqToken) toastError(e);
    } finally {
      if (token === reqToken) { st.loading = false; renderTable(); }
    }
  }

  async function loadAll() {
    await Promise.all([loadSummary(), loadDocs()]);
    if (!st.summary?.files) renderEmptyArchive();
    else if (!catalogEl.isConnected) mount(el.body, statsEl, embedEl, catalogEl, problemsEl);
  }

  // ───────── подписки ─────────
  cleanup.push(jobs.subscribe(() => { renderJob(); syncSource(); renderEmbeddings(); }));
  cleanup.push(jobEvents.on('finish', (job) => {
    if (job.kind === 'process' || job.kind === 'embeddings') { st.resultDismissed = false; }
    renderJob();
    loadAll();
  }));
  cleanup.push(app.subscribe(() => renderEmbeddings()));

  renderHead(); renderSource(); renderJob(); renderTable();
  loadAll();

  return { el: root, destroy: () => cleanup.forEach((fn) => fn()) };
}
