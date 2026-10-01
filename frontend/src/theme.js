// Dark/light theme: sets <html data-theme="..."> (the tokens live in
// styles.css) and remembers the choice in this browser.
const KEY = "theme";

export function getTheme() {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
}

export function setTheme(theme) {
  applyTheme(theme);
  try { localStorage.setItem(KEY, theme); } catch { /* storage unavailable -- applies for this visit only */ }
}
