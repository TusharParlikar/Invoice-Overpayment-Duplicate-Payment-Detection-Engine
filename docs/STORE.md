# Where to store the data

The rule: **code goes in the repository, company data never does.** `.gitignore` already keeps `*.db`, backups,
exchange rates and local samples out of git.

## Today: one machine

| What | Where | Why |
|---|---|---|
| Company records (the database) | Outside the project folder, e.g. `D:\InvoiceData\invoices.db`, on an encrypted disk (BitLocker, FileVault, LUKS) | Real financial data; outside the repo it can't be committed by mistake or deleted with the project |
| Backups | `data/backups/` (written by `python cli.py backup`), **plus** a copy on a second device or a private company drive (OneDrive, SharePoint) | A backup on the same disk is lost with the original |
| Demo and mock data | `demo/` in the repository | Fictional, linked from the README |
| Test receipts | `tests/fixtures/` in the repository | CI needs them |
| The 670 MB research dataset | Not stored: `python cli.py demo` downloads it | Too large for git, and anyone can download it again |
| Large files (videos, big samples) | Google Drive, linked from the README | Git is not built for large files |

Point the app at the database outside the project:

```powershell
$env:INVOICE_DB_PATH = "D:\InvoiceData\invoices.db"     # macOS/Linux: export INVOICE_DB_PATH=...
python -m streamlit run app.py
```

Only the database follows `INVOICE_DB_PATH`. Backups and exchange rates are still written to the project's `data/` folder
([engine/config.py](../engine/config.py)).

## Online: many stores sharing one system

When several stores or branches should check against the **same** records (so a duplicate paid at store A is caught at
store B), the data has to live online:

| Need | Recommended | Free tier | For larger scale |
|---|---|---|---|
| Shared database | [Supabase](https://supabase.com/) (managed PostgreSQL) | yes | AWS RDS, Azure Database for PostgreSQL |
| Receipt images and PDFs | Supabase Storage (same account) | yes | AWS S3, Azure Blob Storage |
| Hosting the app | [Render](https://render.com/) or a small cloud server (Azure, AWS, DigitalOcean) | Render has one | a company server |

Supabase is the suggested start: database, file storage and user logins in one account, and it is standard PostgreSQL,
so moving to AWS or Azure later needs no rewrite.

### What has to change in the code first

1. **Database:** the engine uses SQLite, a single file that many machines can't safely share. [engine/db.py](../engine/db.py)
   (about 130 lines) has to move to PostgreSQL.
2. **Logins:** today everyone shares one password (`INVOICE_APP_PASSWORD`) and the name in the check log is not verified.
   Many stores need one account per person, ideally tied to a store.
3. **The README promise** "no data leaves your machine" stops being true and has to be reworded.

### Rules for real invoice data online

- Database and storage are **private**: never a public bucket, never in the repository.
- Strong, unique passwords; database connection strings live in environment variables, not in code.
- HTTPS only (Render and Supabase provide it).
- Keep the scheduled backup; managed databases add their own, but test a restore once.

## A public demo (no real data)

To give recruiters or reviewers a link to try, host the app on Render with **only the [demo/](../demo/) mock data**.
No code changes are needed. The host's disk is wiped on restart, which is fine for a demo and the reason it must never
hold real invoices.
