import hashlib
import os
import io
import logging
import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from fpdf import FPDF

logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)

st.set_page_config(page_title="Client Ledger Management System", page_icon="📊", layout="wide")

# ---------------------------------------------------------
# DATABASE CONNECTION (Postgres / Supabase, via Streamlit secrets)
# ---------------------------------------------------------
@st.cache_resource
def get_engine():
    db_url = st.secrets.get("DATABASE_URL")
    if not db_url:
        st.error(
            "No database connection configured. Add DATABASE_URL to your app's Secrets "
            "(Streamlit Cloud: App settings > Secrets). See README.md for the exact steps."
        )
        st.stop()
    # Streamlit secrets sometimes hand back a 'postgresql://' URL — SQLAlchemy needs the psycopg2 driver name.
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(db_url, pool_pre_ping=True)

engine = get_engine()

def run(sql, params=None, fetch=False):
    """Execute a statement. Returns rows (as list of dict) if fetch=True."""
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        if fetch:
            return [dict(row._mapping) for row in result]
    return None

def read_df(sql, params=None):
    return pd.read_sql_query(text(sql), engine, params=params or {})

def parse_to_iso_date(date_val):
    if pd.isna(date_val) or str(date_val).strip() in ("", "nan", "None"):
        return None
    val_str = str(date_val).strip()
    dt = pd.to_datetime(val_str, errors="coerce", format="%Y-%m-%d")
    if pd.isnull(dt):
        dt = pd.to_datetime(val_str, dayfirst=True, errors="coerce")
    if pd.notnull(dt):
        return dt.strftime("%Y-%m-%d")
    return val_str.split(" ")[0]

# ---------------------------------------------------------
# PASSWORD HASHING (PBKDF2 — no plaintext passwords stored)
# ---------------------------------------------------------
def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000).hex()
    return h, salt

def verify_password(password, salt, stored_hash):
    h, _ = hash_password(password, salt)
    return h == stored_hash

def ensure_schema_and_seed_users():
    run("""
        CREATE TABLE IF NOT EXISTS users (
            user_id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)
    run("""
        CREATE TABLE IF NOT EXISTS clients (
            client_id SERIAL PRIMARY KEY,
            client_name TEXT UNIQUE NOT NULL,
            phone_no TEXT,
            broker TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    run("""
        CREATE TABLE IF NOT EXISTS transactions (
            trans_id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
            trans_date DATE NOT NULL,
            cash_in NUMERIC DEFAULT 0.0,
            cash_out NUMERIC DEFAULT 0.0,
            balance NUMERIC DEFAULT 0.0,
            comments TEXT
        )
    """)
    existing = read_df("SELECT COUNT(*) AS n FROM users")
    if existing["n"].iloc[0] == 0:
        for uname, pwd, role in [("admin", "admin123", "admin"), ("staff", "staff123", "user")]:
            h, salt = hash_password(pwd)
            run(
                "INSERT INTO users (username, password_hash, salt, role) VALUES (:u, :h, :s, :r) "
                "ON CONFLICT (username) DO NOTHING",
                {"u": uname, "h": h, "s": salt, "r": role},
            )

def recalculate_client_balances(client_id):
    df = read_df(
        "SELECT trans_id, trans_date, cash_in, cash_out FROM transactions "
        "WHERE client_id = :cid ORDER BY trans_date ASC, trans_id ASC",
        {"cid": client_id},
    )
    if not df.empty:
        df["cash_in"] = df["cash_in"].fillna(0.0)
        df["cash_out"] = df["cash_out"].fillna(0.0)
        df["balance"] = (df["cash_in"] - df["cash_out"]).cumsum()
        with engine.begin() as conn:
            for _, row in df.iterrows():
                conn.execute(
                    text("UPDATE transactions SET balance = :b WHERE trans_id = :tid"),
                    {"b": float(row["balance"]), "tid": int(row["trans_id"])},
                )

def refresh_all_balances():
    clients = read_df("SELECT client_id FROM clients")
    for cid in clients["client_id"]:
        recalculate_client_balances(int(cid))

ensure_schema_and_seed_users()

# ---------------------------------------------------------
# PDF GENERATION HELPER (FPDF2)
# ---------------------------------------------------------
def generate_client_pdf_report(client_name, trans_df):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Client Ledger Statement", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Client Name: {client_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated Date: {datetime.date.today().strftime('%B %d, %Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 9)
    col_widths = [28, 32, 32, 35, 63]
    headers = ["Date", "Pay-In (INR)", "Pay-Out (INR)", "Balance (INR)", "Comments"]
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 8, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    total_in = trans_df["Pay-In"].sum() if not trans_df.empty else 0.0
    total_out = trans_df["Pay-Out"].sum() if not trans_df.empty else 0.0
    current_bal = trans_df["Balance"].iloc[-1] if not trans_df.empty else 0.0

    for _, row in trans_df.iterrows():
        comment_str = str(row["Comments"]) if pd.notnull(row["Comments"]) and str(row["Comments"]) != "None" else ""
        pdf.cell(col_widths[0], 7, str(row["Date"]), border=1, align="C")
        pdf.cell(col_widths[1], 7, f"{row['Pay-In']:,.2f}", border=1, align="R")
        pdf.cell(col_widths[2], 7, f"{row['Pay-Out']:,.2f}", border=1, align="R")
        pdf.cell(col_widths[3], 7, f"{row['Balance']:,.2f}", border=1, align="R")
        pdf.cell(col_widths[4], 7, comment_str[:35], border=1, align="L")
        pdf.ln()

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, f"Total Pay-In: INR {total_in:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Total Pay-Out: INR {total_out:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Current Balance: INR {current_bal:,.2f}", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())

# ---------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
    st.session_state["username"] = ""
    st.session_state["role"] = ""

def login():
    st.title("🔐 Client Ledger Management System")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        rows = read_df("SELECT * FROM users WHERE username = :u", {"u": username})
        if not rows.empty and verify_password(password, rows.iloc[0]["salt"], rows.iloc[0]["password_hash"]):
            st.session_state["logged_in"] = True
            st.session_state["username"] = rows.iloc[0]["username"]
            st.session_state["role"] = rows.iloc[0]["role"]
            st.rerun()
        else:
            st.error("Invalid Username or Password")

if not st.session_state["logged_in"]:
    login()
    st.stop()

# ---------------------------------------------------------
# MAIN NAVIGATION & LAYOUT
# ---------------------------------------------------------
st.sidebar.title(f"Welcome, {st.session_state['username']} ({st.session_state['role'].upper()})")
if st.sidebar.button("Logout"):
    st.session_state["logged_in"] = False
    st.rerun()

with st.sidebar.expander("🔑 Change my password"):
    cur_pwd = st.text_input("Current password", type="password", key="cur_pwd")
    new_pwd = st.text_input("New password", type="password", key="new_pwd")
    if st.button("Update password"):
        rows = read_df("SELECT * FROM users WHERE username = :u", {"u": st.session_state["username"]})
        if not rows.empty and verify_password(cur_pwd, rows.iloc[0]["salt"], rows.iloc[0]["password_hash"]):
            if len(new_pwd) < 6:
                st.error("New password should be at least 6 characters.")
            else:
                h, salt = hash_password(new_pwd)
                run(
                    "UPDATE users SET password_hash = :h, salt = :s WHERE username = :u",
                    {"h": h, "s": salt, "u": st.session_state["username"]},
                )
                st.success("Password updated.")
        else:
            st.error("Current password is incorrect.")

menu = ["Ledger Management", "Client Profiles", "Reports & Analytics", "Bulk Import Excel"]
if st.session_state["role"] == "admin":
    menu.append("System Backup & Admin")

choice = st.sidebar.selectbox("Navigation", menu)

# ---------------------------------------------------------
# 1. LEDGER MANAGEMENT
# ---------------------------------------------------------
if choice == "Ledger Management":
    st.header("📊 Client Ledger Entries")

    clients_df = read_df("SELECT client_id, client_name FROM clients ORDER BY client_name")
    if clients_df.empty:
        st.warning("No clients found. Please add or import clients first.")
    else:
        client_dict = dict(zip(clients_df["client_name"], clients_df["client_id"]))
        selected_client_name = st.selectbox("Select Client", list(client_dict.keys()))
        selected_client_id = int(client_dict[selected_client_name])

        with st.expander("➕ Add New Transaction"):
            col1, col2, col3 = st.columns(3)
            trans_date = col1.date_input("Date", datetime.date.today())
            cash_in = col2.number_input("Pay-In (Cash In)", min_value=0.0, step=100.0)
            cash_out = col3.number_input("Pay-Out (Cash Out)", min_value=0.0, step=100.0)
            comments = st.text_input("Comments")

            if st.button("Save Transaction"):
                iso_date_str = trans_date.strftime("%Y-%m-%d")
                run(
                    "INSERT INTO transactions (client_id, trans_date, cash_in, cash_out, comments) "
                    "VALUES (:cid, :d, :cin, :cout, :c)",
                    {"cid": selected_client_id, "d": iso_date_str, "cin": cash_in, "cout": cash_out, "c": comments},
                )
                recalculate_client_balances(selected_client_id)
                st.success("Transaction recorded successfully!")
                st.rerun()

        trans_df = read_df(
            "SELECT trans_id, trans_date AS \"Date\", cash_in AS \"Pay-In\", cash_out AS \"Pay-Out\", "
            "balance AS \"Balance\", comments AS \"Comments\" FROM transactions "
            "WHERE client_id = :cid ORDER BY trans_date ASC, trans_id ASC",
            {"cid": selected_client_id},
        )

        st.subheader(f"Ledger for {selected_client_name}")

        if trans_df.empty:
            st.info("No transaction records found for this client.")
        else:
            st.caption("💡 Select a record directly in the table using the 'Select' checkbox to Edit or Delete.")

            display_df = trans_df.copy()
            display_df["Select"] = False
            cols = ["Select", "Date", "Pay-In", "Pay-Out", "Balance", "Comments", "trans_id"]
            display_df = display_df[cols]

            edited_df = st.data_editor(
                display_df,
                hide_index=True,
                column_config={
                    "Select": st.column_config.CheckboxColumn("Select", help="Select record to edit or delete"),
                    "trans_id": None,
                },
                disabled=["Date", "Pay-In", "Pay-Out", "Balance", "Comments"],
                key="ledger_editor",
            )

            selected_rows = edited_df[edited_df["Select"] == True]

            if st.session_state["role"] == "admin" and not selected_rows.empty:
                selected_record = selected_rows.iloc[0]
                rec_id = int(selected_record["trans_id"])

                with st.expander("✏️ Manage Selected Record", expanded=True):
                    st.write(f"**Selected Record Date:** {selected_record['Date']} | **Balance:** ₹{selected_record['Balance']:,.2f}")
                    action = st.radio("Choose Action", ["Edit Entry", "Delete Entry"], horizontal=True)

                    if action == "Edit Entry":
                        edit_col1, edit_col2, edit_col3 = st.columns(3)
                        try:
                            default_d = datetime.datetime.strptime(str(selected_record["Date"]), "%Y-%m-%d").date()
                        except ValueError:
                            default_d = datetime.date.today()

                        new_date = edit_col1.date_input("Edit Date", default_d)
                        new_in = edit_col2.number_input("Edit Pay-In", min_value=0.0, value=float(selected_record["Pay-In"] or 0.0), step=100.0)
                        new_out = edit_col3.number_input("Edit Pay-Out", min_value=0.0, value=float(selected_record["Pay-Out"] or 0.0), step=100.0)
                        new_comm = st.text_input("Edit Comments", value=str(selected_record["Comments"] or ""))

                        if st.button("Update Record"):
                            iso_edit_date = new_date.strftime("%Y-%m-%d")
                            run(
                                "UPDATE transactions SET trans_date = :d, cash_in = :cin, cash_out = :cout, comments = :c "
                                "WHERE trans_id = :tid",
                                {"d": iso_edit_date, "cin": new_in, "cout": new_out, "c": new_comm, "tid": rec_id},
                            )
                            recalculate_client_balances(selected_client_id)
                            st.success("Record updated successfully!")
                            st.rerun()

                    elif action == "Delete Entry":
                        st.warning("Are you sure you want to permanently delete this transaction record?")
                        if st.button("Confirm Delete"):
                            run("DELETE FROM transactions WHERE trans_id = :tid", {"tid": rec_id})
                            recalculate_client_balances(selected_client_id)
                            st.success("Entry deleted!")
                            st.rerun()

            current_balance = trans_df["Balance"].iloc[-1] if not trans_df.empty else 0.0
            total_pay_in = trans_df["Pay-In"].sum()
            total_pay_out = trans_df["Pay-Out"].sum()

            st.markdown("---")
            b_col1, b_col2, b_col3 = st.columns(3)
            b_col1.metric("Total Pay-In", f"₹{total_pay_in:,.2f}")
            b_col2.metric("Total Pay-Out", f"₹{total_pay_out:,.2f}")
            b_col3.metric("💳 Available Balance", f"₹{current_balance:,.2f}")

            st.markdown("### 📥 Download Report")
            exp_col1, exp_col2 = st.columns(2)

            clean_csv_df = trans_df[["Date", "Pay-In", "Pay-Out", "Balance", "Comments"]].copy()
            csv_data = clean_csv_df.to_csv(index=False).encode("utf-8")
            clean_filename = selected_client_name.strip().replace(" ", "_")

            exp_col1.download_button(
                label="📄 Download as CSV", data=csv_data, file_name=f"{clean_filename}.csv", mime="text/csv"
            )

            if exp_col2.button("📕 Generate PDF Report"):
                pdf_bytes = generate_client_pdf_report(selected_client_name, trans_df)
                st.session_state[f"pdf_{selected_client_id}"] = pdf_bytes

            if f"pdf_{selected_client_id}" in st.session_state:
                exp_col2.download_button(
                    label="⬇️ Download PDF Report",
                    data=st.session_state[f"pdf_{selected_client_id}"],
                    file_name=f"{clean_filename}.pdf",
                    mime="application/pdf",
                )

# ---------------------------------------------------------
# 2. CLIENT PROFILES
# ---------------------------------------------------------
elif choice == "Client Profiles":
    st.header("👤 Client Management")

    with st.form("add_client"):
        st.subheader("Add New Client")
        c_name = st.text_input("Client Name *")
        c_phone = st.text_input("Phone Number")
        c_broker = st.text_input("Client Broker")
        submitted = st.form_submit_button("Add Client")

        if submitted and c_name:
            existing = read_df("SELECT 1 FROM clients WHERE client_name = :n", {"n": c_name})
            if not existing.empty:
                st.error("Client name already exists.")
            else:
                run(
                    "INSERT INTO clients (client_name, phone_no, broker) VALUES (:n, :p, :b)",
                    {"n": c_name, "p": c_phone, "b": c_broker},
                )
                st.success(f"Client '{c_name}' added successfully!")
                st.rerun()

    st.subheader("Registered Clients")
    clients_list = read_df("SELECT client_id, client_name, phone_no, broker, created_at FROM clients ORDER BY client_name")

    if clients_list.empty:
        st.info("No registered clients found.")
    else:
        st.caption("💡 Select a client row below to edit profile details or delete a client.")

        display_clients = clients_list.copy()
        display_clients["Select"] = False
        cols = ["Select", "client_id", "client_name", "phone_no", "broker", "created_at"]
        display_clients = display_clients[cols]

        edited_clients = st.data_editor(
            display_clients,
            hide_index=True,
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", help="Select client to edit or delete"),
                "client_id": "Client ID",
                "client_name": "Client Name",
                "phone_no": "Phone",
                "broker": "Broker",
                "created_at": "Created At",
            },
            disabled=["client_id", "client_name", "phone_no", "broker", "created_at"],
            key="client_profiles_editor",
        )

        selected_client_rows = edited_clients[edited_clients["Select"] == True]

        if not selected_client_rows.empty:
            sel_client = selected_client_rows.iloc[0]
            cid = int(sel_client["client_id"])

            with st.expander("✏️ Manage Selected Client", expanded=True):
                st.write(f"**Client Name:** {sel_client['client_name']}")
                c_action = st.radio("Choose Action", ["Edit Client", "Delete Client"], horizontal=True, key="client_mgmt_action")

                if c_action == "Edit Client":
                    edit_c1, edit_c2, edit_c3 = st.columns(3)
                    new_cname = edit_c1.text_input("Client Name", value=str(sel_client["client_name"] or ""))
                    new_cphone = edit_c2.text_input("Phone Number", value=str(sel_client["phone_no"] or ""))
                    new_cbroker = edit_c3.text_input("Broker", value=str(sel_client["broker"] or ""))

                    if st.button("Update Client Profile"):
                        dup = read_df(
                            "SELECT 1 FROM clients WHERE client_name = :n AND client_id != :cid",
                            {"n": new_cname, "cid": cid},
                        )
                        if not dup.empty:
                            st.error("A client with this name already exists.")
                        else:
                            run(
                                "UPDATE clients SET client_name = :n, phone_no = :p, broker = :b WHERE client_id = :cid",
                                {"n": new_cname, "p": new_cphone, "b": new_cbroker, "cid": cid},
                            )
                            st.success("Client profile updated successfully!")
                            st.rerun()

                elif c_action == "Delete Client":
                    st.warning("⚠️ Deleting a client will also permanently delete all associated ledger transaction records!")
                    if st.button("Confirm Delete Client"):
                        run("DELETE FROM transactions WHERE client_id = :cid", {"cid": cid})
                        run("DELETE FROM clients WHERE client_id = :cid", {"cid": cid})
                        st.success("Client and all associated transactions deleted successfully!")
                        st.rerun()

# ---------------------------------------------------------
# 3. REPORTS & ANALYTICS
# ---------------------------------------------------------
elif choice == "Reports & Analytics":
    st.header("📈 Consolidated Reports & Summary")

    summary_df = read_df('''
        SELECT
            c.client_id,
            c.client_name AS "Client Name",
            c.broker AS "Broker",
            c.phone_no AS "Phone",
            COALESCE(SUM(t.cash_in), 0) AS "Total Pay-In",
            COALESCE(SUM(t.cash_out), 0) AS "Total Pay-Out",
            (COALESCE(SUM(t.cash_in), 0) - COALESCE(SUM(t.cash_out), 0)) AS "Net Balance"
        FROM clients c
        LEFT JOIN transactions t ON c.client_id = t.client_id
        GROUP BY c.client_id
        ORDER BY c.client_name
    ''')

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Overall Pay-In", f"₹{summary_df['Total Pay-In'].sum():,.2f}")
    col2.metric("Total Overall Pay-Out", f"₹{summary_df['Total Pay-Out'].sum():,.2f}")
    col3.metric("Net Total Balance", f"₹{summary_df['Net Balance'].sum():,.2f}")

    st.subheader("Client-Wise Portfolio Summary")
    display_summary = summary_df.drop(columns=["client_id"]).copy()
    display_summary.index = range(1, len(display_summary) + 1)
    display_summary.index.name = "S.No"
    st.dataframe(display_summary, use_container_width=True)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        display_summary.to_excel(writer, sheet_name="Consolidated_Summary", index=True)
    st.download_button(
        label="📥 Export Consolidated Report to Excel",
        data=buffer.getvalue(),
        file_name="consolidated_client_summary.xlsx",
        mime="application/vnd.ms-excel",
    )

    st.markdown("---")
    st.subheader("📄 Generate Individual Client Detailed Ledger PDF")

    if not summary_df.empty:
        client_options = dict(zip(summary_df["Client Name"], summary_df["client_id"]))
        rep_client_name = st.selectbox("Select Client for Detailed PDF", list(client_options.keys()), key="rep_client_select")
        rep_client_id = int(client_options[rep_client_name])

        rep_trans_df = read_df(
            "SELECT trans_id, trans_date AS \"Date\", cash_in AS \"Pay-In\", cash_out AS \"Pay-Out\", "
            "balance AS \"Balance\", comments AS \"Comments\" FROM transactions "
            "WHERE client_id = :cid ORDER BY trans_date ASC, trans_id ASC",
            {"cid": rep_client_id},
        )

        pdf_col1, pdf_col2 = st.columns(2)
        if pdf_col1.button("📕 Generate Detailed PDF Statement", key="btn_gen_rep_pdf"):
            pdf_bytes = generate_client_pdf_report(rep_client_name, rep_trans_df)
            st.session_state[f"rep_pdf_{rep_client_id}"] = pdf_bytes

        if f"rep_pdf_{rep_client_id}" in st.session_state:
            clean_fname = rep_client_name.strip().replace(" ", "_")
            pdf_col2.download_button(
                label="⬇️ Download Detailed Ledger PDF",
                data=st.session_state[f"rep_pdf_{rep_client_id}"],
                file_name=f"{clean_fname}_Detailed_Ledger.pdf",
                mime="application/pdf",
                key="btn_dl_rep_pdf",
            )

# ---------------------------------------------------------
# 4. BULK IMPORT EXCEL
# ---------------------------------------------------------
elif choice == "Bulk Import Excel":
    st.header("📤 Bulk Import Client Excel Files")
    uploaded_files = st.file_uploader("Upload Excel Ledgers (.xlsx)", type=["xlsx"], accept_multiple_files=True)

    if uploaded_files and st.button("Process & Import Files"):
        for uploaded_file in uploaded_files:
            try:
                df = pd.read_excel(uploaded_file)
                df.columns = [c.strip().lower() for c in df.columns]

                client_name = os.path.splitext(uploaded_file.name)[0].strip()
                run(
                    "INSERT INTO clients (client_name) VALUES (:n) ON CONFLICT (client_name) DO NOTHING",
                    {"n": client_name},
                )
                c_id = int(read_df("SELECT client_id FROM clients WHERE client_name = :n", {"n": client_name}).iloc[0]["client_id"])

                pay_in_col = [c for c in df.columns if "in" in c or "payin" in c][0]
                pay_out_col = [c for c in df.columns if "out" in c or "payout" in c][0]
                date_col = [c for c in df.columns if "date" in c][0]
                comments_col = [c for c in df.columns if "comment" in c or "unnamed" in c]

                for _, row in df.iterrows():
                    c_in = row[pay_in_col] if pd.notnull(row[pay_in_col]) else 0.0
                    c_out = row[pay_out_col] if pd.notnull(row[pay_out_col]) else 0.0
                    c_date = parse_to_iso_date(row[date_col])
                    c_comm = str(row[comments_col[0]]) if comments_col and pd.notnull(row[comments_col[0]]) else ""

                    run(
                        "INSERT INTO transactions (client_id, trans_date, cash_in, cash_out, comments) "
                        "VALUES (:cid, :d, :cin, :cout, :c)",
                        {"cid": c_id, "d": c_date, "cin": c_in, "cout": c_out, "c": c_comm},
                    )
                recalculate_client_balances(c_id)
                st.success(f"Successfully imported & standardized dates for: {client_name}")
            except Exception as e:
                st.error(f"Error importing {uploaded_file.name}: {e}")

# ---------------------------------------------------------
# 5. BACKUP & ADMIN
# ---------------------------------------------------------
elif choice == "System Backup & Admin":
    st.header("⚙️ Database Backup & Admin")

    st.subheader("1. Download a Backup")
    st.caption("Your data lives safely in the cloud (Supabase) with automatic daily backups. "
               "You can also download your own copy any time as an Excel workbook below.")

    clients_bk = read_df("SELECT * FROM clients ORDER BY client_id")
    trans_bk = read_df("SELECT * FROM transactions ORDER BY trans_id")

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        clients_bk.to_excel(writer, sheet_name="Clients", index=False)
        trans_bk.to_excel(writer, sheet_name="Transactions", index=False)
    st.download_button(
        label="💾 Download Full Backup (.xlsx)",
        data=buffer.getvalue(),
        file_name=f"client_ledger_backup_{datetime.date.today()}.xlsx",
        mime="application/vnd.ms-excel",
    )

    st.subheader("2. Restore Data")
    st.info(
        "Restoring from a backup file could overwrite live data, so it's done safely from the "
        "Supabase SQL Editor rather than in-app. See the 'Restoring a backup' section of README.md "
        "for the exact steps — it takes about a minute."
    )
