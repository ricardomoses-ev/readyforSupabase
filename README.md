# Supabase-ready tabular file cleaner

A Python CLI that reads messy spreadsheets and delimited text files, cleans and normalizes them conservatively, and writes **UTF-8 CSV** plus a **JSON report** and an optional **SQL DDL** suggestion for [Supabase](https://supabase.com/) (PostgreSQL).

## What it does

- **Input:** `.csv`, `.tsv`, `.xlsx`, `.xls` (type detected from the extension).
- **Output (default directory `output/`):**
  - `*_cleaned.csv` — import-ready, UTF-8, Unix newlines.
  - `*_report.json` — counts, renames, warnings, inferred types and confidence.
  - `*_schema_suggestion.sql` — optional `CREATE TABLE` (use `--schema-out`).

The original file is **never modified**.

## Sharing with your team

**Option A — single file (easiest hand-off)**  
Send **`tabular_cleaner.py`** plus a short note to install dependencies:

```bash
pip install pandas openpyxl xlrd python-dateutil charset-normalizer typer
python tabular_cleaner.py path/to/file.xlsx --help
```

Your teammates can drop `tabular_cleaner.py` anywhere and run it with Python 3.10+.

**Option B — whole folder**  
Zip this project (or share via git). They use `requirements.txt` and run either entry point:

- `python tabular_cleaner.py …` (same code as the single file)
- `python main.py …` (thin launcher that calls `tabular_cleaner`)

**Option C — editable install from the folder**

```bash
pip install -e .
```

Then they still run `python tabular_cleaner.py` from the project directory (or copy the file elsewhere).

### Cleaning highlights

- Headers: trim invisibles, **snake_case**, PostgreSQL-friendly names, blank headers → `column_1`, …, duplicate bases → `name`, `name_2`, `name_3`.
- Cells: whitespace, zero-width / NBSP, control characters (keeping intentional newlines for CSV escaping), curly quotes and dash variants normalized.
- Null-like tokens (`""`, `null`, `N/A`, `none`, `NaN`, `-`, `--`, …) → consistent empty / missing.
- Booleans: closed sets only (`yes`/`no`, `y`/`n`, `true`/`false`, `1`/`0`, case-insensitive); other values stay text and are flagged.
- Rows: optional removal of all-empty rows; optional full-row deduplication.
- Dates: ISO and high-confidence parses → ISO-8601-style strings; ambiguous values stay as text and are listed in the report.
- Excel serials: converted only when heuristics suggest a real date (e.g. fractional day, or date-like column name), to avoid corrupting IDs.

Type inference favors **safety**: leading-zero and ID-like columns default to **text**; use `--force-text` if you want every column as `text` in the SQL suggestion.

## Install

From the folder that contains `tabular_cleaner.py`, `main.py`, and `requirements.txt`:

**1. Create and use a virtual environment (recommended)**

Windows (PowerShell):

```powershell
cd "C:\path\to\readyforSupabase"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

macOS / Linux:

```bash
cd /path/to/readyforSupabase
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Run the script only after the venv is activated** (so `python` uses the right packages).

---

## Commands to run the script

All examples assume your current directory is the project root (`readyforSupabase`). Use **`python tabular_cleaner.py`** or **`python main.py`** (same behavior).

### Help

```bash
python tabular_cleaner.py --help
```

### Basic run (writes to `output/`)

Produces `output/<filename_stem>_cleaned.csv` and `output/<filename_stem>_report.json`.

```bash
python tabular_cleaner.py path/to/file.xlsx
python tabular_cleaner.py path/to/file.csv
python tabular_cleaner.py path/to/file.tsv
python tabular_cleaner.py path/to/file.xls
```

**Windows** — use quotes if the path has spaces; use your real username and file extension:

```powershell
cd "C:\Users\YourName\Projects\Effer-ventures Projects\Effer Code\readyforSupabase"
python tabular_cleaner.py "C:\Users\YourName\Downloads\dealsheet.xlsx"
```

### Preview only (no files written)

```bash
python tabular_cleaner.py path/to/file.csv --preview
python tabular_cleaner.py path/to/file.xlsx --preview --sample-rows 15
```

### Custom output folder

```bash
python tabular_cleaner.py path/to/file.csv --output-dir C:\exports\cleaned
python tabular_cleaner.py path/to/file.csv --output-dir ./my_output
```

### Drop empty rows / deduplicate

Empty rows are removed **by default**. To keep them:

```bash
python tabular_cleaner.py path/to/file.csv --no-drop-empty-rows
```

Remove duplicate rows (full-row match):

```bash
python tabular_cleaner.py path/to/file.csv --dedupe
python tabular_cleaner.py path/to/file.csv --no-drop-empty-rows --dedupe
```

### SQL schema suggestion

```bash
python tabular_cleaner.py path/to/file.csv --schema-out
python tabular_cleaner.py path/to/file.xlsx --table-name my_table --schema-out
```

### Encoding (CSV/TSV only; skips auto-detection)

```bash
python tabular_cleaner.py path/to/file.csv --encoding cp1252
python tabular_cleaner.py path/to/file.csv --encoding latin-1
```

### Treat all columns as `text` in inferred DDL

```bash
python tabular_cleaner.py path/to/file.csv --force-text
python tabular_cleaner.py path/to/file.csv --force-text --schema-out
```

### Verbose logging

```bash
python tabular_cleaner.py path/to/file.csv -v
python tabular_cleaner.py path/to/file.csv --verbose
```

### Report format

Only JSON is supported in v1 (default). If you pass anything else, the CLI exits with an error.

```bash
python tabular_cleaner.py path/to/file.csv --report-format json
```

**Typer note:** Boolean options are **flags** (`--dedupe`, `--schema-out`), not `--option true` / `--option false`.

---

## CLI options (reference)

| Option | Description |
|--------|--------------|
| `input_path` | Path to `.csv`, `.tsv`, `.xlsx`, or `.xls`. |
| `--table-name` | Table name for SQL suggestion (default: snake_case of file stem). |
| `--dedupe` | Drop duplicate rows. |
| `--drop-empty-rows` / `--no-drop-empty-rows` | Drop rows that are entirely empty (default: on). |
| `--output-dir` | Output folder (default: `output`). |
| `--report-format` | Only `json` is supported in v1. |
| `--schema-out` / `--no-schema-out` | Write `*_schema_suggestion.sql`. |
| `--encoding` | Force encoding for CSV/TSV (skip sniffing). |
| `--preview` | Print summary and sample; do not write files. |
| `--force-text` | Infer all columns as `text` in the DDL. |
| `--sample-rows` | Rows to show in preview mode. |
| `-v`, `--verbose` | Debug logging. |

## Project layout

```text
tabular_cleaner.py   # all logic; copy this file alone to share
main.py              # launcher: imports tabular_cleaner
ops_cli.py           # unified runner for one-off ops scripts
requirements.txt
requirements-build.txt
packaging/
  readyforSupabase.spec
scripts/
  build_exe.ps1
docs/
  OPERATIONS.md
pyproject.toml
README.md
```

## One-off scripts (consolidated usage)

Instead of calling many ad-hoc script files directly, use:

```powershell
python .\ops_cli.py list
```

This gives a single command surface for all operational scripts and keeps usage consistent.
Detailed examples are in `docs/OPERATIONS.md`.

## Build a shareable Windows EXE

From project root in PowerShell:

```powershell
.\scripts\build_exe.ps1
```

This will:
- install build dependency from `requirements-build.txt` (`pyinstaller`)
- build `dist/readyforSupabase.exe`
- create a ready-to-share folder at `release/readyforSupabase-win64/`

Run the EXE directly:

```powershell
.\release\readyforSupabase-win64\readyforSupabase.exe --help
```

## Supported formats

| Extension | Engine / notes |
|-----------|----------------|
| `.csv` | Delimiter sniffing; UTF-8 / UTF-8-SIG / charset-normalizer fallback. |
| `.tsv` | Tab-separated. |
| `.xlsx` | `openpyxl`; all columns read as string first to preserve leading zeros. |
| `.xls` | `xlrd`; legacy binary Excel may fail on corrupt files. |

## Supabase import compatibility

- CSV is **UTF-8** without BOM by default (suitable for Supabase Table Editor and `COPY`).
- Column names are aligned with **PostgreSQL** unquoted identifier rules where possible; the SQL file uses sanitized names.
- Review the generated **`CREATE TABLE`** before applying: add primary keys, constraints, and RLS as needed.
- Workflow: create table (from suggestion or manually) → import cleaned CSV → verify types.

## Limitations

- **Memory:** Large files are loaded fully with pandas.
- **Dates:** Locale-ambiguous strings (e.g. `03/04/2024`) may stay as text or be parsed with a fixed policy; always check the report’s `dates_ambiguous` section.
- **Scientific notation** in CSV (e.g. `1E+10`) may affect how values look as strings.
- **`.xls`:** Older or damaged workbooks may not read; errors are surfaced in the CLI and can be recorded in `errors` in the report if you extend handling.

## License

Use and modify as needed for your project.
