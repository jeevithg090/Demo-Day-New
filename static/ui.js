/**
 * Shared UI helpers: theme (light/dark) and toast notifications.
 *
 * Loaded in <head> without `defer` so the saved theme is applied before the
 * first paint (no flash of the wrong theme).
 */
(function () {
    const THEME_KEY = 'apollo_theme';
    const root = document.documentElement;
    const darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function readTheme() {
        try {
            return localStorage.getItem(THEME_KEY);
        } catch {
            return null;
        }
    }

    function writeTheme(value) {
        try {
            localStorage.setItem(THEME_KEY, value);
        } catch {
            /* storage unavailable (private mode): theme lasts for this page only */
        }
    }

    const saved = readTheme();
    if (saved === 'light' || saved === 'dark') {
        root.dataset.theme = saved;
    }

    function effectiveTheme() {
        if (root.dataset.theme === 'light' || root.dataset.theme === 'dark') return root.dataset.theme;
        return darkQuery && darkQuery.matches ? 'dark' : 'light';
    }

    function syncToggles() {
        const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
        document.querySelectorAll('.theme-toggle').forEach((btn) => {
            btn.setAttribute('aria-label', `Switch to ${next} theme`);
            btn.title = `Switch to ${next} theme`;
        });
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.content = effectiveTheme() === 'dark' ? '#0b1120' : '#1a56db';
    }

    function toggleTheme() {
        const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
        root.dataset.theme = next;
        writeTheme(next);
        syncToggles();
        document.dispatchEvent(new CustomEvent('themechange', { detail: { theme: next } }));
    }

    if (darkQuery && darkQuery.addEventListener) {
        darkQuery.addEventListener('change', () => {
            syncToggles();
            document.dispatchEvent(new CustomEvent('themechange', { detail: { theme: effectiveTheme() } }));
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('.theme-toggle').forEach((btn) => btn.addEventListener('click', toggleTheme));
        syncToggles();
    });

    // ------------------------------------------------------------------------
    // Toasts
    // ------------------------------------------------------------------------

    const ICONS = {
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
    };

    function region() {
        let el = document.getElementById('toastRegion');
        if (!el) {
            el = document.createElement('div');
            el.id = 'toastRegion';
            el.className = 'toast-region';
            document.body.appendChild(el);
        }
        return el;
    }

    /**
     * Show a transient message. Errors are announced assertively (role="alert"),
     * everything else politely (role="status").
     */
    function toast(message, type = 'info', timeout = 4500) {
        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.setAttribute('role', type === 'error' ? 'alert' : 'status');
        el.innerHTML = ICONS[type] || ICONS.info;
        const text = document.createElement('span');
        text.textContent = message;
        el.appendChild(text);
        region().appendChild(el);

        setTimeout(() => {
            el.classList.add('is-leaving');
            setTimeout(() => el.remove(), 250);
        }, timeout);
    }

    window.ui = { toast, effectiveTheme };
})();
