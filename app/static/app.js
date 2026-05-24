// Codigo compartilhado entre todas as paginas
// API publica: window.app.{currentPlatform, setPlatform, jget, setDatalist, debounced}

(function () {
  const STORAGE_PLATFORM = 'relatorios_ubidots_platform';
  const STORAGE_THEME = 'theme';
  const PLATFORM_LABELS = {
    jkcontrol: 'JKControl',
    ubidots: 'Ubidots Industrial',
  };
  // Mensagens em pt-BR

  /* ---------- Platform ---------- */
  function currentPlatform() {
    return localStorage.getItem(STORAGE_PLATFORM) || 'ubidots';
  }

  function setPlatform(id) {
    localStorage.setItem(STORAGE_PLATFORM, id);
    updateActiveBadge();
  }

  function updateActiveBadge() {
    const el = document.getElementById('active-platform');
    if (!el) return;
    const id = currentPlatform();
    el.textContent = PLATFORM_LABELS[id] || id;
  }

  /* ---------- Theme ---------- */
  function currentTheme() {
    return localStorage.getItem(STORAGE_THEME) || 'light';
  }

  function setTheme(t) {
    localStorage.setItem(STORAGE_THEME, t);
    document.documentElement.setAttribute('data-theme', t);
    updateThemeIcon();
  }

  function toggleTheme() {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  }

  function updateThemeIcon() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const isDark = currentTheme() === 'dark';
    btn.innerHTML = isDark
      // moon
      ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
           <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
         </svg>`
      // sun
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
           <circle cx="12" cy="12" r="4"/>
           <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
         </svg>`;
  }

  /* ---------- Utilities ---------- */
  async function jget(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    return r.json();
  }

  function setDatalist(el, items) {
    if (!el) return;
    el.innerHTML = items.map(i =>
      `<option value="${i.value}">${i.text || ''}</option>`
    ).join('');
  }

  function debounced(fn, ms = 400) {
    let t;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  window.app = {
    currentPlatform,
    setPlatform,
    currentTheme,
    setTheme,
    jget,
    setDatalist,
    debounced,
    platformLabels: PLATFORM_LABELS,
  };

  document.addEventListener('DOMContentLoaded', () => {
    updateActiveBadge();
    updateThemeIcon();
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.addEventListener('click', toggleTheme);
  });
})();
