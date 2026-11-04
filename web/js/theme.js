// Светлая и тёмная темы. Основная — светлая; выбор запоминается в браузере.
const KEY = 'da.theme';
const THEME_COLOR = { light: '#f1f0f4', dark: '#0e0d15' };

export function getTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* приватный режим: просто не запоминаем */ }
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
}

export function toggleTheme() {
  const next = getTheme() === 'dark' ? 'light' : 'dark';
  setTheme(next);
  return next;
}
