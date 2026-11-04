// Подписи и цвета меток для значений, которые приходят с сервера.
import { pill } from './components.js';

// Статус обработки документа (documents.status)
const DOC_STATUS = {
  parsed: ['прочитан', 'success'],
  new: ['в очереди', null],
  needs_ocr: ['скан без текста', 'warning'],
  unsupported: ['формат не поддерживается', 'warning'],
  error: ['ошибка чтения', 'danger'],
};
export const docStatusPill = (s) => {
  const [text, tone] = DOC_STATUS[s] || [s, null];
  return pill(text, tone, { dot: false });
};

const APPROVAL_TONE = { 'утверждён': 'success', 'проект': 'warning' };
export const approvalPill = (v) => pill(v || 'не указано', APPROVAL_TONE[v] || null, { dot: false });

const SIDE_TONE = { 'заказчик': 'info', 'подрядчик': 'accent' };
export const sidePill = (v) => pill(v || 'не указано', SIDE_TONE[v] || null, { dot: false });

// Требования
const REQ_STATUS_TONE = { auto: 'warning', confirmed: 'success', edited: 'info', rejected: 'danger' };
export const reqStatusPill = (status, label) => pill(label || status, REQ_STATUS_TONE[status]);

const OBLIGATION_TONE = { 'обязательно': 'accent', 'желательно': null };
export const obligationPill = (v) => pill(v, OBLIGATION_TONE[v] || null, { dot: false });

// Открытые вопросы
export const QUESTION_LABELS = {
  open: 'Открыт',
  sent: 'Отправлен заказчику',
  answered: 'Получен ответ',
  closed: 'Закрыт',
};
const QUESTION_TONE = { open: 'warning', sent: 'info', answered: 'success', closed: null };
export const questionStatusPill = (s) => pill(QUESTION_LABELS[s] || s, QUESTION_TONE[s]);

// Состояния разделов отчёта (tone приходит с сервера)
const SECTION_TONE = { green: 'success', orange: 'warning', gray: null };
export const sectionPill = (state, tone) => pill(state, SECTION_TONE[tone] ?? null);
