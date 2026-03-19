(function () {
  'use strict';

  /*
   * Which config fields apply to each offer type.
   * All other config fields are hidden.
   */
  var FIELD_MAP = {
    PERCENTAGE:  ['discount_percent'],
    BOGO:        [],                                                     // no extra config
    BUY_X_GET_Y: ['buy_quantity', 'get_quantity', 'get_discount_percent'],
    MIN_QTY:     ['min_quantity', 'discount_percent'],
  };

  var ALL_CONFIG_FIELDS = [
    'discount_percent',
    'buy_quantity',
    'get_quantity',
    'get_discount_percent',
    'min_quantity',
  ];

  /* Labels shown inside the fieldset description for each type */
  var HINTS = {
    PERCENTAGE:  'Set <strong>Discount %</strong> — applied to all matching items.',
    BOGO:        '<strong>No extra configuration needed.</strong> For every 2 matching items, the cheaper one is free.',
    BUY_X_GET_Y: 'Set <strong>Buy quantity</strong>, <strong>Get quantity</strong>, and <strong>Get discount %</strong> (100 = completely free).',
    MIN_QTY:     'Set <strong>Min quantity</strong> (threshold to qualify) and <strong>Discount %</strong> (applied to all matching items).',
  };

  function getRow(fieldName) {
    /* Django admin wraps each field in a div with class "field-{name}" */
    return document.querySelector('.field-' + fieldName);
  }

  function updateFields() {
    var select = document.getElementById('id_offer_type');
    if (!select) return;

    var type   = select.value;
    var show   = FIELD_MAP[type] || [];
    var hint   = HINTS[type] || '';

    /* Show / hide individual field rows */
    ALL_CONFIG_FIELDS.forEach(function (field) {
      var row = getRow(field);
      if (!row) return;
      row.style.display = show.indexOf(field) !== -1 ? '' : 'none';
    });

    /* Update the fieldset description to show only the relevant hint */
    var desc = document.querySelector('.fieldset-discount-config .help, .fieldset-discount-config p.help, fieldset[class*="discount"] .description, fieldset .module p');
    /* Fallback: find the description paragraph inside the config fieldset */
    if (!desc) {
      var fieldsets = document.querySelectorAll('fieldset');
      fieldsets.forEach(function (fs) {
        var h2 = fs.querySelector('h2');
        if (h2 && h2.textContent.trim().toLowerCase().indexOf('discount') !== -1) {
          var p = fs.querySelector('div.description, p.description, .help');
          if (p) desc = p;
        }
      });
    }
    if (desc && hint) desc.innerHTML = hint;

    /* If BOGO: hide the entire "Discount Configuration" section header but keep fieldset */
    var configFieldset = null;
    document.querySelectorAll('fieldset').forEach(function (fs) {
      var h2 = fs.querySelector('h2');
      if (h2 && h2.textContent.indexOf('Discount Configuration') !== -1) {
        configFieldset = fs;
      }
    });
    if (configFieldset) {
      var hasVisibleField = show.length > 0;
      /* Hide the whole fieldset for BOGO (nothing to configure) */
      configFieldset.style.display = hasVisibleField ? '' : 'none';
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var select = document.getElementById('id_offer_type');
    if (!select) return;
    select.addEventListener('change', updateFields);
    updateFields();  /* run immediately on page load */
  });
})();
