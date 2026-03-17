/**
 * Fix: CSRF 403 on admin login after logout — especially on mobile browsers.
 *
 * Two-pronged fix:
 * 1. bfcache: if the browser restores the login page from cache (back button /
 *    suspended tab), reload it so the form gets a fresh CSRF token.
 * 2. cookie-sync: before every form submit on the login page, read the
 *    current csrftoken cookie and update the hidden input — this covers cases
 *    where the in-page token is stale but the cookie is still valid.
 */
(function () {
    function getCsrfCookie() {
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : null;
    }

    function syncCsrfInput() {
        var token = getCsrfCookie();
        if (!token) return;
        document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach(function (el) {
            el.value = token;
        });
    }

    // 1. bfcache fix — reload if restored from back-forward cache
    window.addEventListener('pageshow', function (e) {
        if (e.persisted) {
            window.location.reload();
            return;
        }
        // 2. cookie-sync — keep the form token in sync with the live cookie
        syncCsrfInput();
    });

    // Also sync on DOM ready (covers normal page loads)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', syncCsrfInput);
    } else {
        syncCsrfInput();
    }

    // Sync again just before each form submit on the login page
    document.addEventListener('submit', function (e) {
        if (e.target && e.target.id === 'login-form') {
            syncCsrfInput();
        }
    }, true);
})();
