(() => {
  'use strict';

  const storageKey = 'metal-waltz-theme';
  const root = document.documentElement;

  function savedPreference() {
    try {
      const value = window.localStorage.getItem(storageKey);
      return value === 'light' || value === 'dark' ? value : null;
    } catch {
      return null;
    }
  }

  let preference = savedPreference();
  let systemLight;
  try {
    systemLight = window.matchMedia('(prefers-color-scheme: light)');
  } catch {
    // Browsers without a color preference use the dark palette.
  }

  function applyTheme() {
    const theme = preference || (systemLight?.matches ? 'light' : 'dark');
    root.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      'content', theme === 'dark' ? '#142332' : '#f4f3ee',
    );

    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    const nextMode = theme === 'dark' ? 'light' : 'dark';
    const label = `Switch to ${nextMode} mode`;
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
    document.getElementById('theme-toggle-label').textContent =
      nextMode === 'light' ? 'Light mode' : 'Dark mode';
    toggle.hidden = false;
  }

  // Run before the stylesheet loads so saved and system preferences paint immediately.
  applyTheme();

  function initializeToggle() {
    applyTheme();
    document.getElementById('theme-toggle')?.addEventListener('click', () => {
      preference = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try {
        window.localStorage.setItem(storageKey, preference);
      } catch {
        // The toggle still works for this visit when storage is unavailable.
      }
      applyTheme();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeToggle, { once: true });
  } else {
    initializeToggle();
  }

  if (systemLight?.addEventListener) {
    systemLight.addEventListener('change', applyTheme);
  } else if (systemLight?.addListener) {
    systemLight.addListener(applyTheme);
  }

  window.addEventListener('storage', (event) => {
    if (event.key === storageKey || event.key === null) {
      preference = savedPreference();
      applyTheme();
    }
  });
})();
