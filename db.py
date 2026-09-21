"""Postgres (Neon) persistence for reviewed days and categorization rules.

Used automatically when a DATABASE_URL env var is set (as it will be on
Render, pointing at Neon). Falls back to nothing here — store.py and
categorize.py both keep their local-JSON-file path for plain local dev
without a database configured.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get('DATABASE_URL')


def is_configured() -> bool:
    return bool(DATABASE_URL)


@contextmanager
def _conn():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS days (
                day INTEGER PRIMARY KEY,
                data JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY DEFAULT 1,
                data JSONB NOT NULL,
                CHECK (id = 1)
            )
        """)


# ---- days ----

def load_days() -> dict:
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT day, data FROM days ORDER BY day")
        return {str(row['day']): row['data'] for row in cur.fetchall()}


def save_day(day: int, data: dict) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO days (day, data, updated_at) VALUES (%s, %s, now())
            ON CONFLICT (day) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
            """,
            (day, json.dumps(data)),
        )


def delete_day(day: int) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM days WHERE day = %s", (day,))


# ---- rules ----

def load_rules() -> dict | None:
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT data FROM rules WHERE id = 1")
        row = cur.fetchone()
        return row['data'] if row else None


def save_rules(data: dict) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rules (id, data) VALUES (1, %s)
            ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
            """,
            (json.dumps(data),),
        )
