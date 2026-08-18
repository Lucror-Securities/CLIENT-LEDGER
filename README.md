# Client Ledger — Cloud Setup Guide

No coding or command-line needed. Everything below is done by clicking
buttons on two free websites. It takes about 15–20 minutes the first time.

**What you're setting up:** your same ledger app, but its data will now live
in a secure cloud database instead of a file on one server — so it survives
restarts, backs itself up automatically, and (later) can be reached from
anywhere with a permanent web address.

Your real data (10 clients, 202 transactions) is already prepared to move
across — nothing needs to be re-entered.

---

## Part A — Create your cloud database (Supabase)

1. Go to **supabase.com** and click **Start your project**. Sign up (free —
   no credit card needed).
2. Click **New project**. Give it any name, e.g. `client-ledger`, set a
   database password (**write this password down somewhere safe** — you'll
   need it in a minute), and choose the region closest to you. Click
   **Create new project**. Wait ~2 minutes while it sets up.
3. In the left sidebar, click the **SQL Editor** icon, then **New query**.
4. Open the file **`schema.sql`** (included in this folder) on your
   computer, copy all of its contents, paste into the SQL Editor box, and
   click **Run**. You should see "Success. No rows returned."
5. Click **New query** again. Open **`data_migration.sql`** (also included),
   copy all of it, paste it in, and click **Run**. This loads your existing
   10 clients and 202 transactions into the cloud database, and sets up your
   `admin` / `staff` logins with the same passwords as before
   (`admin123` / `staff123`) — just stored securely now.
6. Get your connection string: click the **Connect** button near the top of
   the project page (or **Project Settings → Database**). Under
   **Connection string**, choose the **URI** tab. Copy it — it looks like:
   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.xxxxxxxxxxxx.supabase.co:5432/postgres
   ```
   Replace `[YOUR-PASSWORD]` with the database password you set in step 2.
   Save this full line somewhere — you'll paste it into Streamlit next.

## Part B — Put the app online (Streamlit Community Cloud)

1. If you don't already have one, make a free account at **github.com**.
2. On GitHub, click the **+** icon (top right) → **New repository**. Name
   it `client-ledger`, keep it **Private**, click **Create repository**.
3. On the new repository page, click **uploading an existing file** (or
   **Add file → Upload files**). Drag in every file from this folder
   (`app.py`, `requirements.txt`, `schema.sql`, `data_migration.sql`, and
   the `.streamlit` folder) and click **Commit changes**. No installation
   needed — this is just a drag-and-drop upload on the website.
   - **Do not upload** `.streamlit/secrets_example.toml` with a real
     password in it — leave it as the placeholder, your real connection
     string goes only into Streamlit's Secrets box in the next step (never
     into GitHub).
4. Go to **share.streamlit.io** and sign in with your GitHub account.
5. Click **Create app** → **From an existing repo**. Pick the
   `client-ledger` repository, branch `main`, main file path `app.py`.
6. Before deploying, click **Advanced settings** → **Secrets**, and paste in:
   ```
   DATABASE_URL = "postgresql://postgres:YOUR-PASSWORD@db.xxxxxxxxxxxx.supabase.co:5432/postgres"
   ```
   (your real connection string from Part A, step 6).
7. Click **Deploy**. After a minute or two, your app will be live at a URL
   like `https://client-ledger-yourname.streamlit.app` — bookmark it. Open
   it, log in with `admin` / `admin123`, and you should see all your
   existing clients and transactions.

**First thing to do once it's live:** log in and use the **"Change my
password"** box in the sidebar to set new passwords for `admin` and `staff`
— the old ones (`admin123` / `staff123`) are now on a public-facing site, so
they shouldn't stay as-is.

---

## Restoring a backup

If you ever need to restore from an Excel backup (downloaded from
**System Backup & Admin** in the app):
1. Open the Excel file, and note down the rows you want to bring back.
2. In Supabase's **SQL Editor**, write an `INSERT INTO clients (...)` /
   `INSERT INTO transactions (...)` statement for each row (same pattern as
   `data_migration.sql`), and click **Run**.

If this ever comes up, message me with the Excel file and I'll generate the
exact SQL for you — no need to write it by hand.

## If something breaks

- **"No database connection configured"** on the app → the Secrets box in
  Streamlit Cloud doesn't have `DATABASE_URL` set correctly. Go to your
  app → **Settings → Secrets** and check it matches Part A step 6 exactly,
  including the quotation marks.
- **Login fails with correct password** → re-run `data_migration.sql` in
  Supabase's SQL Editor; it's safe to run more than once.
- Anything else — send me the error message shown on the page and I'll tell
  you exactly what to click.
