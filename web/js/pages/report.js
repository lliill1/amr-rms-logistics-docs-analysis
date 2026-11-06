// Экран 4. Отчёт: готовность разделов и выгрузка в Word и Excel.
import { api } from '../api.js';
import { button, checkEl, emptyState, field, notice, skeleton, withBusy } from '../components.js';
import { h, mount } from '../dom.js';
import { fmtBytes, fmtDateTime, withNoun } from '../format.js';
import { icon } from '../icons.js';
import { jobEvents } from '../jobs.js';
import { sectionPill } from '../labels.js';
import { toast, toastError } from '../toast.js';

// куда ведёт карточка раздела (если есть что открыть)
const SECTION_LINKS = { 0: '#/archive', 1: '#/systems?tab=logistics', 2: '#/systems?tab=processes', 3: '#/systems?tab=systems', 4: '#/systems?tab=matrix', 5: '#/facts?tab=conflicts' };

export function createReportPage() {
  const st = { data: null, includeAuto: false, project: '', fresh: null };
  const cleanup = [];

  const sectionsEl = h('div', { class: 'stack', style: { gap: '10px' } }, [1, 2, 3, 4].map(() => skeleton('100%', 76)));
  const exportEl = h('aside', { class: 'report__export' });
  const root = h('div', null,
    h('div', { class: 'page-head' }, h('div', null,
      h('h1', { class: 'page-title' }, 'Отчёт'),
      h('p', { class: 'page-sub' }, 'Разделы соответствуют «Образу результата». Готовность считается по подтверждённым данным.'))),
    h('div', { class: 'report__grid' }, sectionsEl, exportEl));

  // ───────── разделы ─────────
  function renderSections() {
    const d = st.data;
    if (!d) return;
    mount(sectionsEl, d.sections.map((s, i) => {
      const soon = s.tone === 'gray' && s.state === 'следующая версия';
      const link = SECTION_LINKS[i];
      const inner = [
        h('span', { class: `section__n section__n--${s.tone}` }, String(i + 1)),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { class: 'section__title' }, s.title.replace(/^\d+\.\s*/, '')),
          h('div', { class: 'card__sub' }, s.detail)),
        sectionPill(s.state, s.tone),
        link ? icon('arrow-up-right', 17) : null,
      ];
      return link
        ? h('a', { class: 'card section section--link', href: link, 'aria-label': `${s.title}: ${s.state}` }, inner)
        : h('div', { class: `card section${soon ? ' card--dashed' : ''}` }, inner);
    }));
  }

  // ───────── выгрузка ─────────
  const projectInput = h('input', { class: 'input', type: 'text', placeholder: 'Например, «Роботизация внутрицеховой логистики»', autocomplete: 'off', onInput: (e) => { st.project = e.target.value; } });

  async function build(kind, btn) {
    await withBusy(btn, async () => {
      try {
        const file = kind === 'docx'
          ? await api.post('/report/docx', { include_auto: st.includeAuto, project: st.project.trim() })
          : kind === 'diagrams'
            ? await api.post('/report/diagrams', { include_auto: st.includeAuto })
            : await api.post('/report/xlsx');
        st.fresh = file.name;
        toast(`Готово: ${file.name}`, { action: { label: 'Скачать', href: file.url } });
        await load();
      } catch (e) { toastError(e); }
    });
  }

  function renderExport() {
    const d = st.data;
    if (!d) { mount(exportEl, h('div', { class: 'card' }, skeleton('100%', 180))); return; }
    const docxBtn = button({ label: 'Сформировать Word', icon: 'file-text', variant: 'primary', disabled: !d.can_export, onClick: () => build('docx', docxBtn) });
    const xlsxBtn = button({ label: 'Таблицы в Excel', icon: 'file-spreadsheet', variant: 'secondary', disabled: !d.can_export, onClick: () => build('xlsx', xlsxBtn) });
    const dgBtn = button({ label: 'Схемы (draw.io, SVG)', icon: 'network', variant: 'secondary', disabled: !d.can_export, title: 'Архив со схемами в редактируемом виде', onClick: () => build('diagrams', dgBtn) });
    const unreviewedFacts = d.counts.unreviewed_facts || 0;
    const unreviewed = d.counts.unreviewed;
    const files = d.files || [];

    mount(exportEl,
      h('section', { class: 'card' },
        h('div', { class: 'card__head' },
          h('span', { class: 'card__icon card__icon--accent' }, icon('download', 18)),
          h('h2', { class: 'card__title' }, 'Параметры выгрузки')),
        h('div', { class: 'stack', style: { gap: '16px' } },
          h('div', { class: 'stack', style: { gap: '8px' } },
            checkEl({ label: 'Включить непроверенные факты (черновик)', checked: st.includeAuto, onChange: (v) => { st.includeAuto = v; renderExport(); } }),
            !st.includeAuto && unreviewedFacts > 0 && h('p', { class: 'field__hint', style: { paddingLeft: '30px' } },
              `Процессов, обменов и фактов ответственности без проверки: ${unreviewedFacts} — в схемы и разделы 2–5 они не попадут. `,
              h('a', { href: '#/systems' }, 'Проверить')),
            !st.includeAuto && unreviewed > 0 && h('p', { class: 'field__hint', style: { paddingLeft: '30px' } },
              `${withNoun(unreviewed, ['требование ждёт', 'требования ждут', 'требований ждут'])} проверки и в отчёт ${unreviewed % 10 === 1 && unreviewed % 100 !== 11 ? 'не попадёт' : 'не попадут'}. `,
              h('a', { href: '#/facts' }, 'Перейти к ревью'))),
          field('Название проекта для титула', projectInput),
          h('p', { class: 'field__hint' }, 'Файлы сохраняются в папку: ', h('code', null, d.output_dir)),
          !d.can_export && notice({ tone: 'info', body: 'Архив пуст: сначала загрузите документы на экране «Архив и обработка».' }),
          h('div', { class: 'row row--wrap', style: { gap: '10px' } }, docxBtn, xlsxBtn, dgBtn))),
      h('section', { class: 'card', style: { marginTop: '16px' } },
        h('div', { class: 'card__head' },
          h('span', { class: 'card__icon' }, icon('archive', 18)),
          h('div', null, h('h2', { class: 'card__title' }, 'Готовые файлы'), h('p', { class: 'card__sub' }, files.length ? withNoun(files.length, ['файл', 'файла', 'файлов']) : 'пока нет'))),
        files.length
          ? h('ul', { class: 'files' }, files.map((f) => h('li', { class: `file${f.name === st.fresh ? ' is-fresh' : ''}` },
            h('span', { class: 'file__icon' }, icon(f.name.endsWith('.xlsx') ? 'file-spreadsheet' : f.name.endsWith('.zip') ? 'network' : 'file-text', 18)),
            h('div', { class: 'file__meta' }, h('div', { class: 'file__name truncate', title: f.name }, f.name), h('div', { class: 'cell-sub' }, `${fmtBytes(f.size)} · ${fmtDateTime(f.modified)}`)),
            h('a', { class: 'btn btn--soft btn--sm', href: f.url, download: f.name }, icon('download', 15), 'Скачать'))))
          : emptyState({ icon: 'file-text', title: 'Отчётов ещё не было', text: 'Сформируйте Word или Excel, и файл появится здесь.' })));
  }

  async function load() {
    try {
      st.data = await api.get('/report/summary');
    } catch (e) { toastError(e); }
    renderSections();
    renderExport();
  }

  // данные раздела меняются после любой фоновой задачи
  cleanup.push(jobEvents.on('finish', () => load()));
  renderExport();
  load();
  return { el: root, destroy: () => cleanup.forEach((fn) => fn()) };
}
