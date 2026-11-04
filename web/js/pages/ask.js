// Экран 2. Вопросы к архиву: ответ модели строго по найденным фрагментам, со ссылками на документ и страницу.
import { api } from '../api.js';
import { app, llmOk } from '../app-state.js';
import { button, emptyState, notice, pill, selectEl, skeleton, withBusy } from '../components.js';
import { copyText, h, mount } from '../dom.js';
import { icon } from '../icons.js';
import { renderMarkdown } from '../markdown.js';
import { approvalPill, sidePill } from '../labels.js';
import { toast, toastError } from '../toast.js';

const SCOPES = [
  { value: 'current', label: 'Только актуальные редакции' },
  { value: 'all', label: 'Все документы' },
];
const EXAMPLES = [
  'Какая система создаёт транспортное задание для AMR?',
  'Чем отличаются редакции проектного решения?',
  'Какие требования к безопасности предъявляются к AMR?',
];

// Последний ответ переживает переходы между экранами (как session_state в Streamlit)
let last = null;

export function createAskPage() {
  const st = { loading: false, scope: last?.scope || 'current', expanded: new Set() };

  const input = h('input', {
    class: 'input ask__input', type: 'text', autocomplete: 'off', value: last?.question || '',
    placeholder: 'Какая система создаёт транспортное задание для AMR?', 'aria-label': 'Вопрос к архиву',
  });
  const scopeSel = selectEl({ options: SCOPES, value: st.scope, ariaLabel: 'Область поиска', onChange: (v) => { st.scope = v; } });
  const submitBtn = button({ label: 'Спросить', icon: 'send', variant: 'primary', type: 'submit', size: 'lg' });
  const form = h('form', { class: 'ask__form', onSubmit: (e) => { e.preventDefault(); submit(input.value); } },
    h('div', { class: 'searchbox ask__search' }, icon('message-square-text', 19), input),
    h('div', { class: 'ask__scope' }, scopeSel),
    submitBtn);
  const hint = h('div');
  const results = h('div');

  const root = h('div', null,
    h('div', { class: 'page-head' }, h('div', null,
      h('h1', { class: 'page-title' }, 'Вопросы к архиву'),
      h('p', { class: 'page-sub' }, 'Ответ строится только по найденным фрагментам. Номера в скобках [1], [2] — ссылки на источники справа.'))),
    h('section', { class: 'card ask__card' }, form, hint),
    h('div', { style: { height: '16px' } }),
    results);

  function renderHint() {
    mount(hint, !llmOk() && h('div', { style: { marginTop: '12px' } }, notice({
      tone: 'info', body: 'Модель сейчас недоступна: вопросы работают по словам, а вместо сводного ответа вы увидите найденные фрагменты.',
    })));
  }

  async function submit(question) {
    question = (question || '').trim();
    if (!question || st.loading) { input.focus(); return; }
    st.loading = true;
    submitBtn.disabled = true;
    renderLoading(question);
    try {
      const res = await api.post('/ask', { question, only_current: st.scope === 'current' });
      last = { question, scope: st.scope, res };
      st.expanded = new Set(res.hits.slice(0, 2).map((x) => x.n));
    } catch (e) {
      toastError(e);
      last = null;
    } finally {
      st.loading = false;
      submitBtn.disabled = false;
      renderResults();
    }
  }

  function renderLoading(question) {
    mount(results, h('div', { class: 'ask__grid' },
      h('div', { class: 'stack', style: { gap: '14px' } },
        h('div', { class: 'ask__q' }, h('span', { class: 'muted' }, 'Вопрос: '), question),
        h('section', { class: 'card stack', 'aria-busy': 'true' },
          h('div', { class: 'row muted' }, h('span', { class: 'spin', style: { display: 'inline-grid' } }, icon('loader-circle', 18)), llmOk() ? 'Ищу фрагменты и формулирую ответ…' : 'Ищу фрагменты…'),
          skeleton('92%', 14), skeleton('84%', 14), skeleton('66%', 14))),
      h('div', { class: 'stack' }, skeleton('100%', 76), skeleton('100%', 76))));
  }

  // ───────── источники ─────────
  const sourceEls = new Map();
  function sourceItem(hit) {
    const open = st.expanded.has(hit.n);
    const badges = [];
    if (!hit.is_current) badges.push(pill('устаревшая редакция', 'warning'));
    if (hit.approval && hit.approval !== 'не указано') badges.push(approvalPill(hit.approval));
    if (hit.side && hit.side !== 'не указано') badges.push(sidePill(hit.side));
    const body = h('div', { class: 'src__body', hidden: !open },
      badges.length ? h('div', { class: 'row row--wrap', style: { gap: '6px' } }, badges) : null,
      hit.section ? h('div', { class: 'cell-sub' }, hit.section) : null,
      h('div', { class: 'src__text' }, hit.text));
    const head = h('button', {
      class: 'src__head', type: 'button', 'aria-expanded': String(open), 'aria-controls': `src-${hit.n}`,
      onClick: () => toggle(hit.n),
    },
      h('span', { class: 'src__n' }, String(hit.n)),
      h('span', { class: 'src__label' }, hit.label),
      icon('chevron-down', 17));
    body.id = `src-${hit.n}`;
    const item = h('article', { class: 'src card' + (open ? ' is-open' : ''), id: `source-${hit.n}` }, head, body);
    sourceEls.set(hit.n, { item, head, body });
    return item;
  }

  function toggle(n, force) {
    const e = sourceEls.get(n);
    if (!e) return;
    const open = force ?? !st.expanded.has(n);
    open ? st.expanded.add(n) : st.expanded.delete(n);
    e.body.hidden = !open;
    e.head.setAttribute('aria-expanded', String(open));
    e.item.classList.toggle('is-open', open);
  }

  function jumpTo(n) {
    const e = sourceEls.get(n);
    if (!e) return;
    toggle(n, true);
    e.item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    e.item.classList.remove('is-flash');
    void e.item.offsetWidth; // перезапустить анимацию
    e.item.classList.add('is-flash');
  }

  // ───────── результат ─────────
  function renderResults() {
    sourceEls.clear();
    if (!last) {
      mount(results, h('section', { class: 'card card--dashed' }, emptyState({
        icon: 'message-square-text', title: 'Спросите архив о чём угодно',
        text: 'Поиск идёт по словам и по смыслу, а ответ собирается только из найденных фрагментов со ссылками на документ и страницу.',
        action: h('div', { class: 'chips', style: { justifyContent: 'center', marginTop: '10px' } }, EXAMPLES.map((q) =>
          h('button', { class: 'chip', type: 'button', onClick: () => { input.value = q; submit(q); } }, q))),
      })));
      return;
    }
    const { question, res } = last;

    const answerCard = (() => {
      if (res.error) return notice({ tone: 'danger', title: 'Модель не ответила', body: res.error });
      if (res.text) {
        return h('section', { class: 'card ask__answer' },
          h('div', { class: 'card__head' },
            h('span', { class: 'card__icon card__icon--accent' }, icon('sparkles', 18)),
            h('h2', { class: 'card__title' }, 'Ответ'),
            h('span', { class: 'spacer' }),
            button({ iconOnly: true, icon: 'copy', variant: 'ghost', size: 'sm', label: 'Скопировать ответ', onClick: async () => toast(await copyText(res.text) ? 'Ответ скопирован' : 'Не удалось скопировать', { type: 'info' }) })),
          renderMarkdown(res.text, { onCite: jumpTo }));
      }
      if (res.hits.length) return notice({ tone: 'info', body: 'Модель недоступна — ниже найденные фрагменты без сводного ответа.' });
      return null;
    })();

    const toQuestion = res.hits.length ? (() => {
      const ta = h('textarea', { class: 'textarea', rows: 3, 'aria-label': 'Формулировка вопроса заказчику' }, question);
      ta.value = question;
      const btn = button({
        label: 'Добавить в открытые вопросы', icon: 'plus', variant: 'secondary',
        onClick: () => withBusy(btn, async () => {
          const text = ta.value.trim();
          if (!text) { ta.focus(); return; }
          try {
            await api.post('/open-questions', { text, context: (res.text || '').slice(0, 1000), sources: res.sources });
            ta.value = question;
            toast('Вопрос добавлен в открытые вопросы', { action: { label: 'Открыть', href: '#/facts?tab=questions' } });
          } catch (e) { toastError(e); }
        }),
      });
      return h('section', { class: 'card card--tinted' },
        h('div', { class: 'card__head', style: { marginBottom: '10px' } },
          h('span', { class: 'card__icon' }, icon('triangle-alert', 18)),
          h('div', null, h('h2', { class: 'card__title' }, 'Нашли противоречие или пробел?'),
            h('p', { class: 'card__sub' }, 'Отправьте вопрос заказчику в раздел «Факты и ревью → Открытые вопросы».'))),
        h('div', { class: 'stack', style: { gap: '10px' } }, ta, h('div', null, btn)));
    })() : null;

    const left = h('div', { class: 'stack', style: { gap: '14px' } },
      h('div', { class: 'ask__q' }, h('span', { class: 'muted' }, 'Вопрос: '), question),
      answerCard,
      !res.hits.length && h('section', { class: 'card' }, emptyState({
        icon: 'search', title: 'Ничего не нашлось',
        text: 'Попробуйте переформулировать вопрос или переключить область на «Все документы».',
      })),
      toQuestion);

    const right = res.hits.length ? h('aside', { class: 'ask__sources', 'aria-label': 'Найденные фрагменты' },
      h('div', { class: 'row', style: { marginBottom: '10px' } },
        h('h2', { class: 'card__title' }, 'Найденные фрагменты'), h('span', { class: 'tag' }, String(res.hits.length))),
      h('div', { class: 'stack', style: { gap: '10px' } }, res.hits.map(sourceItem))) : null;

    mount(results, h('div', { class: res.hits.length ? 'ask__grid' : '' }, left, right));
  }

  renderHint();
  renderResults();
  const unsub = app.subscribe(renderHint);
  input.focus({ preventScroll: true });
  return { el: root, destroy: unsub };
}
