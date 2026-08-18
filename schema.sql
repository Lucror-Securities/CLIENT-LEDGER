-- ============================================
-- Client Ledger — Supabase / Postgres schema
-- Run this FIRST in Supabase: Dashboard > SQL Editor > New query > Run
-- ============================================

CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    client_id SERIAL PRIMARY KEY,
    client_name TEXT UNIQUE NOT NULL,
    phone_no TEXT,
    broker TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    trans_id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    trans_date DATE NOT NULL,
    cash_in NUMERIC DEFAULT 0.0,
    cash_out NUMERIC DEFAULT 0.0,
    balance NUMERIC DEFAULT 0.0,
    comments TEXT
);

CREATE INDEX IF NOT EXISTS idx_transactions_client ON transactions(client_id);
