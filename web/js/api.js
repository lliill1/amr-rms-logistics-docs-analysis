// Клиент HTTP API. Все маршруты лежат под /api (см. api/routers/*.py).

export class ApiError extends Error {
  constructor(message, status = 0, data = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

function errorText(data, status) {
  const d = data && typeof data === 'object' ? data.detail : data;
  if (typeof d === 'string' && d) return d;
  if (Array.isArray(d)) {
    // 422 от pydantic: [{loc: ['body', 'field'], msg: '…'}]
    return d.map((e) => `${(e.loc || []).slice(1).join('.') || 'поле'}: ${e.msg}`).join('; ');
  }
  if (typeof data === 'string' && data && data.length < 300) return data;
  return `Ошибка сервера (${status})`;
}

function buildUrl(path, query) {
  const url = new URL('/api' + path, location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v == null || v === '' || v === false) continue;
      if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
      else url.searchParams.set(k, v === true ? 'true' : v);
    }
  }
  return url;
}

async function request(method, path, { body, query } = {}) {
  const opts = { method, headers: { Accept: 'application/json' } };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(buildUrl(path, query), opts);
  } catch {
    throw new ApiError('Нет связи с сервером. Проверьте, что server.py запущен.', 0);
  }
  if (res.status === 204) return null;
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const data = isJson ? await res.json().catch(() => null) : await res.text().catch(() => '');
  if (!res.ok) throw new ApiError(errorText(data, res.status), res.status, data);
  return data;
}

export const api = {
  get: (path, query) => request('GET', path, { query }),
  post: (path, body, query) => request('POST', path, { body: body ?? {}, query }),
  patch: (path, body) => request('PATCH', path, { body }),
  del: (path) => request('DELETE', path),

  /** Загрузка zip с прогрессом (fetch не умеет отдавать прогресс отправки). */
  upload(path, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', buildUrl(path).toString());
      xhr.responseType = 'json';
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
      };
      xhr.onerror = () => reject(new ApiError('Нет связи с сервером. Проверьте, что server.py запущен.', 0));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
        else reject(new ApiError(errorText(xhr.response, xhr.status), xhr.status, xhr.response));
      };
      const form = new FormData();
      form.append('file', file, file.name);
      xhr.send(form);
    });
  },
};
