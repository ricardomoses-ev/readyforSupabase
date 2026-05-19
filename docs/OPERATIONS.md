# Operations Scripts

This project has one-off operational scripts for data backfills and admin tasks.

Use the consolidated runner:

```powershell
python .\ops_cli.py list
```

Run any operation:

```powershell
python .\ops_cli.py import-partners -- --dry-run
python .\ops_cli.py backfill-partner-referral -- --dry-run
python .\ops_cli.py backfill-lead-magnet -- --dry-run
python .\ops_cli.py reset-password -- --user-id <uuid> --password "NewPass123!"
```

Notes:
- By default, `ops_cli.py` preloads `dashboard/.env` if it exists.
- You can override env loading with `--dotenv-path`.
- Script arguments are passed through after `--`.
