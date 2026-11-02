/* Exact, bounded structured data display. All source values are HTML-escaped. */
(function (root) {
  "use strict";
  const esc = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const csvCell = value => {
    let text = String(value ?? "");
    const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text.trim());
    // Preserve zero-padded textual identifiers when a CSV is opened in a spreadsheet.
    if (typeof value === "string" && /^[+-]?0\d+$/.test(text.trim())) text = "'" + text;
    // Prevent spreadsheet formula execution when exporting untrusted file cells.
    if (typeof value === "string" && /^[\s]*[=+@]/.test(text) && !numeric) text = "'" + text;
    if (typeof value === "string" && /^[\s]*-/.test(text) && !numeric && !/^'[\s]*-/.test(text)) text = "'" + text;
    return `"${text.replace(/"/g, '""')}"`;
  };
  function toCsv(data) {
    if (!data || typeof data !== "object") return "";
    // CSV-mode results already contain a bounded, server-generated CSV page.
    // Prefer it because row values are deliberately not duplicated in JSON.
    if (typeof data.csv === "string") return data.csv;
    const rows = Array.isArray(data.rows) ? data.rows : Array.isArray(data.groups) ? data.groups : [];
    if (!rows.length) return "";
    const keys = data.rows ? Object.keys(rows[0].values || {}) : Object.keys(rows[0]).filter(k => !["aggregation_details", "aggregation_warnings"].includes(k));
    if (!keys.length) return "";
    return [keys.map(csvCell).join(","), ...rows.map(row => {
      const values = data.rows ? (row.values || {}) : row;
      return keys.map(key => csvCell(values[key])).join(",");
    })].join("\r\n") + "\r\n";
  }
  function render(data) {
    if (!data || typeof data !== "object") return "";
    const status = String(data.status || "");
    const label = status === "ok" ? "Structured result" : "Workspace data result";
    const description = typeof data.message === "string" ? data.message : "";
    if (status !== "ok") {
      const tables = Array.isArray(data.available_tables) ? data.available_tables : [];
      const details = tables.length ? `<ul>${tables.slice(0, 20).map(t => `<li>${esc(t.source || t.path || "Table")}${t.document_id ? ` <span class="mono">${esc(t.document_id)}</span>` : ""}</li>`).join("")}</ul>` : "";
      return `<section class="structured-result structured-status" aria-label="${esc(label)}"><b>${esc(status || "Unavailable")}</b><p>${esc(description || "KORA could not return a structured result.")}</p>${details}</section>`;
    }
    const csv = toCsv(data);
    let body = "";
    if (data.operation === "count") {
      body = `<div class="structured-metric"><strong>${esc(data.count)}</strong><span>matching records</span></div>`;
    } else if (data.operation === "aggregate") {
      const entries = Object.entries(data.aggregations || {});
      body = `<div class="structured-metrics">${entries.map(([key, value]) => `<div class="structured-metric"><strong>${esc(value)}</strong><span>${esc(key)}</span></div>`).join("")}</div>`;
      const details = data.aggregation_details && typeof data.aggregation_details === "object" ? data.aggregation_details : {};
      const notes = Object.entries(details).flatMap(([key, info]) => {
        if (!info || typeof info !== "object") return [];
        const parts = [];
        if (info.rounded) parts.push(`${key} is rounded to ${Number(info.precision_digits) || 50} significant digits.`);
        if (Number(info.values_skipped) > 0) parts.push(`${key} skipped ${Number(info.values_skipped)} non-numeric value(s).`);
        return parts;
      });
      if (notes.length) body += `<ul class="structured-notes">${notes.map(note => `<li>${esc(note)}</li>`).join("")}</ul>`;
    } else {
      const rows = Array.isArray(data.rows) ? data.rows : Array.isArray(data.groups) ? data.groups : [];
      if (data.format === "csv" && typeof data.csv === "string" && data.csv.length) {
        body = `<p class="structured-empty">CSV page ready. Copy or download the results below.</p>`;
      } else if (rows.length) {
        const isRows = Array.isArray(data.rows);
        const keys = isRows ? Object.keys(rows[0].values || {}) : Object.keys(rows[0]).filter(k => !["aggregation_details", "aggregation_warnings"].includes(k));
        body = `<div class="tbl structured-table"><table><thead><tr>${keys.map(k => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => {
          const values = isRows ? (row.values || {}) : row;
          return `<tr>${keys.map(k => `<td>${esc(values[k])}</td>`).join("")}</tr>`;
        }).join("")}</tbody></table></div>`;
      } else if (data.operation === "list" || data.operation === "group") {
        body = `<p class="structured-empty">No matching ${data.operation === "group" ? "groups" : "records"} on this page.</p>`;
      }
    }
    const returned = Number(data.returned ?? (Array.isArray(data.rows) ? data.rows.length : Array.isArray(data.groups) ? data.groups.length : 0));
    const total = Number(data.total_matches ?? data.total_groups ?? data.count ?? returned);
    const meta = data.operation === "count" || data.operation === "aggregate" ? "Exact result calculated over all selected rows." :
      `Showing ${Number.isFinite(returned) ? returned : 0} of ${Number.isFinite(total) ? total : "—"} matching ${data.operation === "group" ? "groups" : "records"}.`;
    const paging = data.has_more ? `<p class="structured-more">More results are available. Ask KORA for the next page.</p>` : "";
    const actions = csv ? `<div class="structured-actions"><button type="button" class="structured-copy">Copy CSV</button><button type="button" class="structured-download">Download CSV</button></div>` : "";
    return `<section class="structured-result" aria-label="Structured data result" data-csv="${esc(csv)}"><div class="structured-head"><b>Data from your workspace</b><span>${esc(meta)}</span></div>${body}${paging}${actions}</section>`;
  }
  root.KoraStructuredResult = Object.freeze({ render, toCsv });
})(typeof window !== "undefined" ? window : globalThis);
