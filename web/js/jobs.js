// Фоновые задачи сервера (обработка архива, эмбеддинги, извлечение требований).
// Одновременно работает одна: интерфейс опрашивает /api/jobs/{id} и показывает прогресс везде.
import { api } from './api.js';
import { createBus, createStore } from './store.js';

export const JOB_LABELS = {
  process: 'Обработка архива',
  embeddings: 'Расчёт эмбеддингов',
  extraction: 'Извлечение фактов',
};
export const JOB_PAGES = { process: '#/archive', embeddings: '#/archive', extraction: '#/facts' };

/** state.job — активная или последняя задача; state.results[kind] — итог последней завершённой за эту сессию */
export const jobs = createStore({ job: null, results: {} });
export const jobEvents = createBus();

let timer = null;

export function jobPercent(job) {
  const stages = job?.stages || [];
  if (!stages.length) return 0;
  const s = stages[stages.length - 1];
  return s.total ? Math.min(1, s.done / s.total) : 0;
}

async function poll(id) {
  clearTimeout(timer);
  try {
    const job = await api.get(`/jobs/${id}`);
    const prev = jobs.get().job;
    jobs.set({ job });
    if (job.state === 'running') {
      timer = setTimeout(() => poll(id), 700);
    } else {
      jobs.set((s) => ({ results: { ...s.results, [job.kind]: job } }));
      if (!prev || prev.id !== job.id || prev.state === 'running') jobEvents.emit('finish', job);
    }
  } catch {
    timer = setTimeout(() => poll(id), 2000); // сервер мог на секунду отвлечься
  }
}

export function track(job) {
  jobs.set({ job });
  if (job.state === 'running') poll(job.id);
}

/** Запустить задачу через API и начать следить. При занятости сервера — подхватываем уже идущую. */
export async function startJob(starter) {
  try {
    const job = await starter();
    track(job);
    return job;
  } catch (e) {
    if (e.status === 409) await resume();
    throw e;
  }
}

/** После перезагрузки страницы подхватываем задачу, которая ещё идёт на сервере. */
export async function resume() {
  try {
    const job = await api.get('/jobs/current');
    if (job && job.state === 'running') track(job);
  } catch { /* сервер недоступен — покажем ошибку в другом месте */ }
}

export const isRunning = (kind) => {
  const j = jobs.get().job;
  return !!j && j.state === 'running' && (!kind || j.kind === kind);
};
