/* ── Admin table → cards on mobile ── */
(function () {
    function applyDataLabels() {
        const table = document.getElementById('result_list');
        if (!table) return;

        /* Collect header labels from <thead> */
        const headers = Array.from(table.querySelectorAll('thead th')).map(th => {
            /* Unfold wraps text in a <span> — get only the visible text */
            const span = th.querySelector('span:not(.sr-only)');
            const text = span ? span.textContent.trim() : th.textContent.trim();
            return text.replace(/\s+/g, ' ').trim();
        });

        /* Stamp data-label on every <td> */
        table.querySelectorAll('tbody tr').forEach(row => {
            Array.from(row.querySelectorAll('td')).forEach((td, i) => {
                if (headers[i]) td.setAttribute('data-label', headers[i]);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyDataLabels);
    } else {
        applyDataLabels();
    }
})();
