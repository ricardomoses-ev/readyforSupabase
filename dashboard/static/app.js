/* ── Supabase Dashboard – frontend logic ──────────────────────── */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ── State ────────────────────────────────────────────────────────
const state = {
  tables: [],
  currentTable: null,
  schema: [],
  schemaSql: "",
  schemaModalView: "grid",
  rows: [],
  total: 0,
  offset: 0,
  limit: 50,
  orderCol: null,
  orderAsc: true,
  searchColumn: "",
  searchOperator: "contains",
  searchValue: "",
  authPage: 1,
  authUsers: [],
  authOffset: 0,
  authLimit: 50,
  authSearchColumn: "partner_name",
  authSearchValue: "",
  importRunning: false,
};

// ── Init ─────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  bindNav();
  bindModals();
  bindSQL();
  bindAuth();
  bindTableSearch();
  loadTables();
});

// ── Navigation ───────────────────────────────────────────────────
function bindNav() {
  $$(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".nav-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const view = btn.dataset.view;
      $$(".view").forEach((v) => v.classList.remove("active"));
      $(`#view-${view}`).classList.add("active");
      if (view === "auth") loadAuthUsers();
    });
  });
}

// ── Tables list ──────────────────────────────────────────────────
async function loadTables() {
  try {
    state.tables = await api("/api/tables");
    renderTableList();
  } catch (e) {
    toast("Failed to load tables: " + e.message, "error");
  }
}

function renderTableList() {
  const el = $("#table-list");
  if (!state.tables.length) {
    el.innerHTML = '<p class="placeholder" style="padding:16px;font-size:12px">No tables found</p>';
    return;
  }
  el.innerHTML = state.tables
    .map(
      (t) =>
        `<div class="table-list-item${state.currentTable === t.table_name ? " active" : ""}" data-table="${t.table_name}">
          <span>${t.table_name}</span>
          <span class="badge">${t.row_count.toLocaleString()} rows</span>
        </div>`
    )
    .join("");
  $$(".table-list-item").forEach((item) =>
    item.addEventListener("click", () => selectTable(item.dataset.table))
  );
}

async function selectTable(name) {
  state.currentTable = name;
  state.offset = 0;
  state.orderCol = null;
  state.searchColumn = "";
  state.searchOperator = "contains";
  state.searchValue = "";
  renderTableList();

  $$(".nav-btn").forEach((b) => b.classList.remove("active"));
  $$(".nav-btn")[0].classList.add("active");
  $$(".view").forEach((v) => v.classList.remove("active"));
  $("#view-tables").classList.add("active");

  $("#table-title").textContent = name;
  $("#btn-schema").style.display = "";
  $("#btn-import-csv").style.display = "";
  $("#btn-add-row").style.display = "";
  $("#table-search").style.display = "";

  try {
    state.schema = await api(`/api/tables/${name}/schema`);
    setupTableSearch();
  } catch (e) {
    toast("Failed to load schema: " + e.message, "error");
  }

  await loadRows();
}

// ── Rows ─────────────────────────────────────────────────────────
async function loadRows() {
  const name = state.currentTable;
  if (!name) return;

  const wrap = $("#table-grid-wrap");
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  const params = new URLSearchParams({
    offset: String(state.offset),
    limit: String(state.limit),
  });
  if (state.orderCol) {
    params.set("order", `${state.orderCol}.${state.orderAsc ? "asc" : "desc"}`);
  }
  if (state.searchColumn && state.searchValue.trim()) {
    params.set("filter_column", state.searchColumn);
    params.set("filter_op", state.searchOperator);
    params.set("filter_value", state.searchValue.trim());
  }
  const url = `/api/tables/${name}/rows?${params.toString()}`;

  try {
    const data = await api(url);
    state.rows = data.rows;
    state.total = data.total ?? 0;
    renderDataGrid(wrap, state.rows, { editable: true });
    renderPagination();
  } catch (e) {
    wrap.innerHTML = `<p class="placeholder">${escHtml(e.message)}</p>`;
  }
}

function renderDataGrid(container, rows, opts = {}) {
  if (!rows.length) {
    container.innerHTML = '<p class="placeholder">No rows</p>';
    return;
  }
  const cols = Object.keys(rows[0]);
  const showActions = opts.editable;

  let html = '<table class="data-grid"><thead><tr>';
  cols.forEach((c) => {
    const arrow =
      state.orderCol === c ? (state.orderAsc ? " &#9650;" : " &#9660;") : "";
    html += `<th data-col="${escAttr(c)}">${escHtml(c)}${arrow}</th>`;
  });
  if (showActions) html += "<th>Actions</th>";
  html += "</tr></thead><tbody>";

  rows.forEach((row, idx) => {
    html += "<tr>";
    cols.forEach((c) => {
      const val = row[c];
      if (val === null || val === undefined) {
        html += '<td><span class="null-cell">NULL</span></td>';
      } else if (typeof val === "object") {
        html += `<td>${escHtml(JSON.stringify(val))}</td>`;
      } else {
        html += `<td>${escHtml(String(val))}</td>`;
      }
    });
    if (showActions) {
      html += `<td class="actions">
        <button class="btn btn-secondary btn-sm btn-edit" data-idx="${idx}">Edit</button>
        <button class="btn btn-danger btn-sm btn-delete" data-idx="${idx}">Del</button>
      </td>`;
    }
    html += "</tr>";
  });
  html += "</tbody></table>";
  container.innerHTML = html;

  // Sortable headers
  container.querySelectorAll("th[data-col]").forEach((th) =>
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (state.orderCol === col) {
        state.orderAsc = !state.orderAsc;
      } else {
        state.orderCol = col;
        state.orderAsc = true;
      }
      loadRows();
    })
  );

  // Edit/delete buttons
  if (showActions) {
    container.querySelectorAll(".btn-edit").forEach((btn) =>
      btn.addEventListener("click", () => openRowModal("edit", state.rows[+btn.dataset.idx]))
    );
    container.querySelectorAll(".btn-delete").forEach((btn) =>
      btn.addEventListener("click", () => deleteRow(state.rows[+btn.dataset.idx]))
    );
  }
}

function renderPagination() {
  const el = $("#table-pagination");
  el.style.display = "flex";
  const from = state.total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + state.limit, state.total);
  el.innerHTML = `
    <div class="page-info">
      Showing <strong>${from}-${to}</strong> of <strong>${state.total}</strong>
    </div>
    <div style="display:flex;gap:6px">
      <button id="pg-prev" ${state.offset === 0 ? "disabled" : ""}>Previous</button>
      <button id="pg-next" ${state.offset + state.limit >= state.total ? "disabled" : ""}>Next</button>
    </div>
  `;
  $("#pg-prev")?.addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadRows();
  });
  $("#pg-next")?.addEventListener("click", () => {
    state.offset += state.limit;
    loadRows();
  });
}

// ── Row CRUD modals ──────────────────────────────────────────────
function bindModals() {
  $("#btn-schema").addEventListener("click", openSchemaModal);
  $("#schema-modal-close").addEventListener("click", () => ($("#schema-modal").style.display = "none"));
  $("#schema-modal").addEventListener("click", (e) => {
    if (e.target === $("#schema-modal")) $("#schema-modal").style.display = "none";
  });
  $("#btn-view-schema-sql")?.addEventListener("click", toggleSchemaSqlView);
  $("#btn-copy-schema-sql")?.addEventListener("click", copySchemaSql);

  $("#btn-add-row").addEventListener("click", () => openRowModal("add"));
  $("#row-modal-close").addEventListener("click", closeRowModal);
  $("#row-modal-cancel").addEventListener("click", closeRowModal);
  $("#row-modal").addEventListener("click", (e) => {
    if (e.target === $("#row-modal")) closeRowModal();
  });
  $("#row-modal-save").addEventListener("click", saveRow);

  $("#btn-import-csv").addEventListener("click", openImportModal);
  $("#import-modal-close").addEventListener("click", closeImportModal);
  $("#import-modal-cancel").addEventListener("click", closeImportModal);
  $("#import-modal").addEventListener("click", (e) => {
    if (e.target === $("#import-modal")) closeImportModal();
  });
  $("#import-modal-run").addEventListener("click", runCsvImport);
}

function bindTableSearch() {
  $("#btn-search-apply").addEventListener("click", applyTableSearch);
  $("#btn-search-clear").addEventListener("click", clearTableSearch);
  $("#search-value").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      applyTableSearch();
    }
  });
}

function setupTableSearch() {
  const cols = (state.schema || []).map((c) => c.column_name).filter(Boolean).sort((a, b) => a.localeCompare(b));
  const select = $("#search-column");
  if (!cols.length) {
    select.innerHTML = '<option value="">No columns available</option>';
    select.value = "";
    state.searchColumn = "";
    return;
  }

  select.innerHTML = cols
    .map((col) => `<option value="${escAttr(col)}">${escHtml(col)}</option>`)
    .join("");

  if (!state.searchColumn || !cols.includes(state.searchColumn)) {
    state.searchColumn = cols[0];
  }
  select.value = state.searchColumn;
  $("#search-operator").value = state.searchOperator;
  $("#search-value").value = state.searchValue;
}

function applyTableSearch() {
  if (!state.currentTable) return;
  state.searchColumn = $("#search-column").value;
  state.searchOperator = $("#search-operator").value;
  state.searchValue = $("#search-value").value;
  state.offset = 0;
  loadRows();
}

function clearTableSearch() {
  if (!state.currentTable) return;
  state.searchValue = "";
  state.searchOperator = "contains";
  $("#search-operator").value = state.searchOperator;
  $("#search-value").value = "";
  state.offset = 0;
  loadRows();
}

function openSchemaModal() {
  $("#schema-modal-title").textContent = `${state.currentTable} — Schema`;
  const body = $("#schema-modal-body");

  state.schemaSql = generateCreateTableSql(state.currentTable, state.schema || []);
  state.schemaModalView = "grid";

  const canGenerate = Boolean(state.schemaSql);
  $("#btn-view-schema-sql").disabled = !canGenerate;
  $("#btn-copy-schema-sql").disabled = true;
  $("#btn-view-schema-sql").textContent = "View schema as SQL";

  if (!state.schema.length) body.innerHTML = '<p class="placeholder">No schema info</p>';
  else renderDataGrid(body, state.schema, { editable: false });

  $("#schema-modal").style.display = "flex";
}

function toggleSchemaSqlView() {
  if (!state.schemaSql) return;

  const body = $("#schema-modal-body");
  const viewBtn = $("#btn-view-schema-sql");
  const copyBtn = $("#btn-copy-schema-sql");

  if (state.schemaModalView === "grid") {
    state.schemaModalView = "sql";
    viewBtn.textContent = "View schema (table)";
    copyBtn.disabled = false;

    body.innerHTML = `<pre class="schema-sql-pre" id="schema-sql-pre">${escHtml(state.schemaSql)}</pre>`;
    return;
  }

  // Back to table/grid view
  state.schemaModalView = "grid";
  viewBtn.textContent = "View schema as SQL";
  copyBtn.disabled = true;

  if (!state.schema.length) body.innerHTML = '<p class="placeholder">No schema info</p>';
  else renderDataGrid(body, state.schema, { editable: false });
}

async function copySchemaSql() {
  const sql = state.schemaSql || "";
  if (!sql) return;

  try {
    await navigator.clipboard.writeText(sql);
    toast("SQL copied to clipboard", "success");
    return;
  } catch {
    // fall back to execCommand for non-secure contexts
  }

  try {
    const ta = document.createElement("textarea");
    ta.value = sql;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("SQL copied to clipboard", "success");
  } catch (e) {
    toast("Copy failed: " + e.message, "error");
  }
}

function generateCreateTableSql(tableName, columns) {
  const safeIdent = /^[a-z_][a-z0-9_]*$/;
  const quoteIdent = (ident) => {
    ident = String(ident);
    if (safeIdent.test(ident)) return ident;
    return `"${ident.replace(/"/g, '""')}"`;
  };

  const normalizePgType = (t) => {
    const s = String(t || "").toLowerCase();
    if (!s || s === "unknown") return "text";
    if (s === "string") return "text";
    if (s === "number") return "numeric";
    if (s === "integer") return "integer";
    if (s === "boolean") return "boolean";
    if (s === "date-time") return "timestamptz";
    if (s === "date") return "date";
    return s;
  };

  if (!tableName || !columns || !columns.length) return "";

  const pkCols = columns
    .filter((c) => c && (c.is_primary_key === true || String(c.is_primary_key) === "true"))
    .map((c) => c.column_name)
    .filter(Boolean);

  const defs = columns.map((c) => {
    const colName = c.column_name;
    const dataType = normalizePgType(c.data_type);
    const defaultClause = c.column_default !== null && c.column_default !== undefined ? ` DEFAULT ${c.column_default}` : "";
    const notNullClause = c.is_nullable === "NO" ? " NOT NULL" : "";
    return `  ${quoteIdent(colName)} ${dataType}${defaultClause}${notNullClause}`;
  });

  if (pkCols.length) {
    defs.push(`  PRIMARY KEY (${pkCols.map(quoteIdent).join(", ")})`);
  }

  const lines = defs.join(",\n");
  return `CREATE TABLE IF NOT EXISTS ${quoteIdent(tableName)} (\n${lines}\n);`;
}

let _editingRow = null;

function openRowModal(mode, row = null) {
  _editingRow = mode === "edit" ? row : null;
  $("#row-modal-title").textContent = mode === "edit" ? "Edit Row" : "Add Row";
  const form = $("#row-form");

  const cols = state.schema.length ? state.schema : (state.rows[0] ? Object.keys(state.rows[0]).map((c) => ({ column_name: c })) : []);

  form.innerHTML = cols
    .map((c) => {
      const name = c.column_name;
      const val = row ? (row[name] !== null && row[name] !== undefined ? (typeof row[name] === "object" ? JSON.stringify(row[name]) : String(row[name])) : "") : "";
      return `<label>
        ${escHtml(name)}
        <input name="${escAttr(name)}" value="${escAttr(val)}" />
      </label>`;
    })
    .join("");

  $("#row-modal").style.display = "flex";
}

function closeRowModal() {
  $("#row-modal").style.display = "none";
  _editingRow = null;
}

function openImportModal() {
  if (!state.currentTable) return;
  $("#import-modal-title").textContent = `Bulk Upload CSVs to ${state.currentTable}`;
  $("#import-source-format").value = state.currentTable === "leads" ? "close_csv" : "generic_csv";
  $("#import-files").value = "";
  $("#import-results").innerHTML =
    '<p class="placeholder">Select one or more CSV files to import into the currently selected table.</p>';
  $("#import-modal").style.display = "flex";
}

function closeImportModal() {
  if (state.importRunning) return;
  $("#import-modal").style.display = "none";
}

async function runCsvImport() {
  if (state.importRunning || !state.currentTable) return;
  const fileInput = $("#import-files");
  const files = [...fileInput.files];
  if (!files.length) {
    toast("Choose at least one CSV file.", "error");
    return;
  }

  const sourceFormat = $("#import-source-format").value;
  const body = new FormData();
  body.append("table_name", state.currentTable);
  body.append("source_format", sourceFormat);
  files.forEach((file) => body.append("files", file));

  state.importRunning = true;
  $("#import-modal-run").disabled = true;
  $("#import-results").innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  try {
    const result = await api("/api/import/csv", "POST", body);
    renderImportResults(result);
    toast(`Imported ${result.rows_inserted} rows into ${state.currentTable}.`, "success");
    await loadRows();
    await loadTables();
  } catch (e) {
    $("#import-results").innerHTML = `<p class="placeholder import-error">${escHtml(e.message)}</p>`;
    toast("Import failed: " + e.message, "error");
  } finally {
    state.importRunning = false;
    $("#import-modal-run").disabled = false;
  }
}

function renderImportResults(result) {
  const errorHtml = (result.errors || []).length
    ? `<div class="import-error-list"><strong>Insert errors</strong><pre>${escHtml(result.errors.join("\n\n"))}</pre></div>`
    : "";
  const filesHtml = (result.files || [])
    .map((file) => {
      const skipped = file.skipped_columns?.length ? file.skipped_columns.join(", ") : "None";
      const matched = file.matched_columns?.length ? file.matched_columns.join(", ") : "None";
      return `
        <div class="import-file-summary">
          <h4>${escHtml(file.filename)}</h4>
          <p>${file.rows_ready} of ${file.rows_read} rows prepared.</p>
          <p><strong>Matched columns:</strong> ${escHtml(matched)}</p>
          <p><strong>Skipped columns:</strong> ${escHtml(skipped)}</p>
        </div>
      `;
    })
    .join("");

  $("#import-results").innerHTML = `
    <div class="import-summary">
      <p><strong>Table:</strong> ${escHtml(result.table_name)}</p>
      <p><strong>Files processed:</strong> ${result.files_processed}</p>
      <p><strong>Rows ready:</strong> ${result.rows_ready}</p>
      <p><strong>Rows inserted:</strong> ${result.rows_inserted}</p>
      ${errorHtml}
      <div class="import-files-list">${filesHtml}</div>
    </div>
  `;
}

async function saveRow() {
  const form = $("#row-form");
  const data = {};
  form.querySelectorAll("input").forEach((inp) => {
    const v = inp.value.trim();
    if (v === "") {
      data[inp.name] = null;
    } else if (v === "true") {
      data[inp.name] = true;
    } else if (v === "false") {
      data[inp.name] = false;
    } else if (!isNaN(v) && v !== "") {
      data[inp.name] = Number(v);
    } else {
      try {
        data[inp.name] = JSON.parse(v);
      } catch {
        data[inp.name] = v;
      }
    }
  });

  try {
    if (_editingRow) {
      const pk = guessPK();
      const match = {};
      pk.forEach((k) => (match[k] = _editingRow[k]));
      await api(`/api/tables/${state.currentTable}/rows`, "PATCH", { match, data });
      toast("Row updated", "success");
    } else {
      await api(`/api/tables/${state.currentTable}/rows`, "POST", data);
      toast("Row inserted", "success");
    }
    closeRowModal();
    await loadRows();
  } catch (e) {
    toast("Save failed: " + e.message, "error");
  }
}

async function deleteRow(row) {
  if (!confirm("Delete this row?")) return;
  const pk = guessPK();
  const match = {};
  pk.forEach((k) => (match[k] = row[k]));
  try {
    await api(`/api/tables/${state.currentTable}/rows`, "DELETE", { match });
    toast("Row deleted", "success");
    await loadRows();
  } catch (e) {
    toast("Delete failed: " + e.message, "error");
  }
}

function guessPK() {
  if (state.rows.length && "id" in state.rows[0]) return ["id"];
  const cols = Object.keys(state.rows[0] || {});
  return cols.length ? [cols[0]] : [];
}

// ── SQL Editor ───────────────────────────────────────────────────
function bindSQL() {
  $("#btn-run-sql").addEventListener("click", runSQL);
  $("#sql-input").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runSQL();
    }
  });
}

async function runSQL() {
  const sql = $("#sql-input").value.trim();
  if (!sql) return;
  const wrap = $("#sql-results");
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  try {
    const rows = await api("/api/sql", "POST", { sql });
    if (Array.isArray(rows) && rows.length) {
      renderDataGrid(wrap, rows, { editable: false });
    } else {
      wrap.innerHTML = '<p class="placeholder">Query executed. No rows returned.</p>';
    }
  } catch (e) {
    wrap.innerHTML = `<p class="placeholder" style="color:var(--danger)">${escHtml(e.message)}</p>`;
  }
}

// ── Auth Users ───────────────────────────────────────────────────
function bindAuth() {
  $("#btn-refresh-users").addEventListener("click", loadAuthUsers);
  $("#btn-auth-search-apply").addEventListener("click", applyAuthSearch);
  $("#btn-auth-search-clear").addEventListener("click", clearAuthSearch);
  $("#auth-search-value").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      applyAuthSearch();
    }
  });
}

async function loadAuthUsers() {
  const wrap = $("#auth-grid");
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  try {
    const data = await api("/api/auth/users?all_pages=true&per_page=100");
    const users = data.users || [];
    state.authOffset = 0;
    if (!users.length) {
      state.authUsers = [];
      wrap.innerHTML = '<p class="placeholder">No auth users found.</p>';
      $("#auth-pagination").style.display = "none";
      return;
    }
    state.authUsers = users.map((u) => ({
      id: u.id,
      partner_name:
        u.user_metadata?.partner_name ||
        u.user_metadata?.display_name ||
        u.user_metadata?.full_name ||
        u.user_metadata?.name ||
        "",
      email: u.email || "",
      phone: u.phone || "",
      provider: (u.app_metadata?.providers || []).join(", "),
      created_at: u.created_at ? new Date(u.created_at).toLocaleString() : "",
      last_sign_in: u.last_sign_in_at ? new Date(u.last_sign_in_at).toLocaleString() : "",
      confirmed: u.email_confirmed_at ? "Yes" : "No",
    }));
    renderFilteredAuthUsers();
  } catch (e) {
    wrap.innerHTML = `<p class="placeholder">${escHtml(e.message)}</p>`;
  }
}

function applyAuthSearch() {
  state.authSearchColumn = $("#auth-search-column").value;
  state.authSearchValue = $("#auth-search-value").value;
  state.authOffset = 0;
  renderFilteredAuthUsers();
}

function clearAuthSearch() {
  state.authSearchColumn = "partner_name";
  state.authSearchValue = "";
  state.authOffset = 0;
  $("#auth-search-column").value = state.authSearchColumn;
  $("#auth-search-value").value = "";
  renderFilteredAuthUsers();
}

function renderFilteredAuthUsers() {
  const wrap = $("#auth-grid");
  const term = (state.authSearchValue || "").trim().toLowerCase();
  const allRows = !term
    ? state.authUsers
    : state.authUsers.filter((user) => {
        const raw = user[state.authSearchColumn];
        return String(raw ?? "").toLowerCase().includes(term);
      });

  const total = allRows.length;
  if (state.authOffset >= total && total > 0) {
    state.authOffset = Math.max(0, total - state.authLimit);
  }
  const pageRows = allRows.slice(state.authOffset, state.authOffset + state.authLimit);
  renderAuthUsersGrid(wrap, pageRows);
  renderAuthPagination(total);
}

function renderAuthPagination(total) {
  const el = $("#auth-pagination");
  if (!total) {
    el.style.display = "none";
    el.innerHTML = "";
    return;
  }

  el.style.display = "flex";
  const from = state.authOffset + 1;
  const to = Math.min(state.authOffset + state.authLimit, total);
  el.innerHTML = `
    <div class="page-info">
      Showing <strong>${from}-${to}</strong> of <strong>${total}</strong>
    </div>
    <div style="display:flex;gap:6px">
      <button id="auth-pg-prev" ${state.authOffset === 0 ? "disabled" : ""}>Previous</button>
      <button id="auth-pg-next" ${state.authOffset + state.authLimit >= total ? "disabled" : ""}>Next</button>
    </div>
  `;

  $("#auth-pg-prev")?.addEventListener("click", () => {
    state.authOffset = Math.max(0, state.authOffset - state.authLimit);
    renderFilteredAuthUsers();
  });
  $("#auth-pg-next")?.addEventListener("click", () => {
    state.authOffset += state.authLimit;
    renderFilteredAuthUsers();
  });
}

function renderAuthUsersGrid(container, rows) {
  if (!rows.length) {
    container.innerHTML = '<p class="placeholder">No auth users found.</p>';
    return;
  }

  const cols = ["partner_name", "email", "phone", "provider", "created_at", "last_sign_in", "confirmed"];
  let html = '<table class="data-grid"><thead><tr>';
  cols.forEach((c) => {
    html += `<th>${escHtml(c)}</th>`;
  });
  html += "<th>Actions</th></tr></thead><tbody>";

  rows.forEach((row, idx) => {
    html += "<tr>";
    cols.forEach((c) => {
      const val = row[c];
      html += `<td>${escHtml(val === null || val === undefined ? "" : String(val))}</td>`;
    });
    html += `<td class="actions">
      <button class="btn btn-secondary btn-sm btn-auth-reset" data-idx="${idx}">Reset password</button>
    </td>`;
    html += "</tr>";
  });

  html += "</tbody></table>";
  container.innerHTML = html;

  container.querySelectorAll(".btn-auth-reset").forEach((btn) => {
    btn.addEventListener("click", () => resetAuthUserPassword(rows[+btn.dataset.idx]));
  });
}

async function resetAuthUserPassword(user) {
  const identifier = user.email || user.phone || user.id;
  const password = prompt(`Enter a new password for ${identifier}:`);
  if (password === null) return;

  const trimmed = password.trim();
  if (!trimmed) {
    toast("Password cannot be empty.", "error");
    return;
  }
  if (trimmed.length < 6) {
    toast("Password must be at least 6 characters.", "error");
    return;
  }

  try {
    await api(`/api/auth/users/${encodeURIComponent(user.id)}/reset-password`, "POST", {
      password: trimmed,
    });
    toast(`Password reset for ${identifier}.`, "success");
  } catch (e) {
    toast("Reset failed: " + e.message, "error");
  }
}

// ── API helper ───────────────────────────────────────────────────
async function api(url, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body) {
    if (body instanceof FormData) {
      opts.body = body;
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let msg;
    try {
      const j = await resp.json();
      msg = j.detail || JSON.stringify(j);
    } catch {
      msg = await resp.text();
    }
    throw new Error(msg);
  }
  return resp.json();
}

// ── Utilities ────────────────────────────────────────────────────
function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function escAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toast-container").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}
