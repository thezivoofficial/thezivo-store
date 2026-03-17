/**
 * Fix: CSRF 403 after logout → back button restores stale login page from bfcache.
 * When the browser restores any admin page from bfcache (event.persisted = true)
 * and the URL is the login page, force a hard reload to get a fresh CSRF token.
 */
window.addEventListener('pageshow', function (e) {
    if (e.persisted && window.location.pathname.indexOf('/admin/login') !== -1) {
        window.location.reload();
    }
});
