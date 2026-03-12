/**
 * Auto-generate SKU codes in the SKU inline on the Product change page.
 * Format: P{productId}-{SIZE}-{COLOR_3}
 * Only fills if the sku_code field is currently empty.
 */
document.addEventListener("DOMContentLoaded", function () {
  // Extract product ID from the URL (/admin/store/product/42/change/)
  const match = window.location.pathname.match(/\/product\/(\d+)\//);
  const productId = match ? match[1] : "0";

  function generateCode(row) {
    const skuField   = row.querySelector('input[name$="-sku_code"]');
    const sizeField  = row.querySelector('input[name$="-size"], select[name$="-size"]');
    const colorField = row.querySelector('input[name$="-color"], select[name$="-color"]');

    if (!skuField || !sizeField || !colorField) return;
    if (skuField.value.trim()) return; // don't overwrite existing code

    const size  = sizeField.value.trim().toUpperCase();
    const color = colorField.value.trim().substring(0, 3).toUpperCase().replace(/\s/g, "");

    if (size && color) {
      skuField.value = `P${productId}-${size}-${color}`;
    }
  }

  // Trigger on blur of size or color fields inside any SKU inline row
  document.addEventListener("blur", function (e) {
    const field = e.target;
    if (
      field.matches('input[name$="-size"], select[name$="-size"]') ||
      field.matches('input[name$="-color"], select[name$="-color"]')
    ) {
      const row = field.closest("tr, .form-row, .dynamic-sku_set");
      if (row) generateCode(row);
    }
  }, true);
});
