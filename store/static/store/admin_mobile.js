/* ── Admin table → cards on mobile ── */
(function () {
    function applyDataLabels() {
        const table = document.getElementById('result_list');
        if (!table) return;

        /* Build field-name → human label map from <thead> column classes */
        const labelMap = {};
        table.querySelectorAll('thead th').forEach(th => {
            const colClass = Array.from(th.classList).find(
                c => c.startsWith('column-') && c !== 'column-action-checkbox'
            );
            if (!colClass) return;
            const fieldName = colClass.replace('column-', '');

            /* Extract readable text — ignore Material icon spans */
            let label = '';
            const a = th.querySelector('a');
            const source = a || th;
            source.childNodes.forEach(node => {
                if (node.nodeType === 3) {
                    label += node.textContent;
                } else if (node.nodeType === 1 && !/material|icon/i.test(node.className || '')) {
                    label += node.textContent;
                }
            });
            label = label.replace(/\s+/g, ' ').trim();

            /* Fallback: derive from field name */
            if (!label) {
                label = fieldName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            }

            labelMap[fieldName] = label;
        });

        /* Stamp data-label on each <td> using its own field-* class (not position) */
        table.querySelectorAll('tbody tr').forEach(row => {
            row.querySelectorAll('td').forEach(td => {
                if (td.classList.contains('action-checkbox')) return;
                const fieldClass = Array.from(td.classList).find(c => c.startsWith('field-'));
                if (!fieldClass) return;
                const fieldName = fieldClass.replace('field-', '');
                const label = labelMap[fieldName]
                    || fieldName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                td.setAttribute('data-label', label);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyDataLabels);
    } else {
        applyDataLabels();
    }
})();
