// Данные, общие для всех страниц: справочники сервера и статус Ollama.
import { api } from './api.js';
import { createStore } from './store.js';

export const app = createStore({ meta: null, status: null });

export async function loadMeta() {
  const meta = await api.get('/meta');
  app.set({ meta });
  return meta;
}

export async function refreshStatus(force = false) {
  const status = await api.get('/status', { refresh: force });
  app.set({ status });
  return status;
}

export const llmOk = () => !!app.get().status?.ok;
