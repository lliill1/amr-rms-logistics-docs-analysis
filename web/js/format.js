// Форматирование: русские числительные, даты, размеры.

/** plural(5, ['файл', 'файла', 'файлов']) → «файлов» */
export function plural(n, forms) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

export const withNoun = (n, forms) => `${n} ${plural(n, forms)}`;

export function fmtDate(value) {
  if (!value) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(value);
}

export function fmtDateTime(epochSeconds) {
  if (!epochSeconds) return '';
  return new Date(epochSeconds * 1000).toLocaleString('ru-RU', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

export function fmtBytes(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} Б`;
  const units = ['КБ', 'МБ', 'ГБ'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 10 ? v.toFixed(0) : v.toFixed(1).replace('.', ',')} ${units[i]}`;
}

export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export const pct = (x) => `${Math.round((x || 0) * 100)}%`;

/** «02_EWM/ПР.docx» → {dir: '02_EWM', name: 'ПР.docx'} */
export function splitPath(rel) {
  const p = String(rel || '').replace(/\\/g, '/');
  const i = p.lastIndexOf('/');
  return i < 0 ? { dir: '', name: p } : { dir: p.slice(0, i), name: p.slice(i + 1) };
}

export function pageLabel(from, to) {
  if (from && to && to !== from) return `с. ${from}–${to}`;
  if (from) return `с. ${from}`;
  return '';
}
