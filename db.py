"""Postgres (Neon) persistence — multi-company.

Schema (new table names, deliberately not reusing the old single-company
`days`/`rules` tables from the first version, so this migration never has to
drop anything):

- companies(id, name)
- company_rules(company_id, data jsonb)               — categorization rules, per company
- company_days(company_id, year, month, day, data jsonb) — one reconciled day
- voucher_images(id, company_id, file_hash, filename, year, month, day, content bytea)
  — file_hash is a SHA-256 of the image bytes, used to detect a re-upload of
  the same photo (UNIQUE per company) so the app can say "already added for
  date X" instead of silently reprocessing it.
"""
from __future__ import annotations

import hashlib
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
            CREATE TABLE IF NOT EXISTS companies (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS company_rules (
                company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
                data JSONB NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS company_days (
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                data JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (company_id, year, month, day)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS voucher_images (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                file_hash TEXT NOT NULL,
                filename TEXT,
                year INTEGER, month INTEGER, day INTEGER,
                content BYTEA NOT NULL,
                content_type TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (company_id, file_hash)
            )
        """)


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ---- companies ----

def list_companies() -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM companies ORDER BY name")
        return cur.fetchall()


def create_company(name: str) -> int:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO companies (name) VALUES (%s) ON CONFLICT (name) DO NOTHING RETURNING id",
            (name,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT id FROM companies WHERE name = %s", (name,))
        return cur.fetchone()[0]


# ---- rules ----

def load_rules(company_id: int) -> dict | None:
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT data FROM company_rules WHERE company_id = %s", (company_id,))
        row = cur.fetchone()
        return row['data'] if row else None


def save_rules(company_id: int, data: dict) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO company_rules (company_id, data) VALUES (%s, %s)
            ON CONFLICT (company_id) DO UPDATE SET data = EXCLUDED.data
            """,
            (company_id, json.dumps(data)),
        )


# ---- days ----

def load_days(company_id: int, year: int | None = None, month: int | None = None) -> dict:
    q = "SELECT year, month, day, data FROM company_days WHERE company_id = %s"
    params = [company_id]
    if year is not None:
        q += " AND year = %s"
        params.append(year)
    if month is not None:
        q += " AND month = %s"
        params.append(month)
    q += " ORDER BY year, month, day"
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(q, params)
        rows = cur.fetchall()
    return {f"{r['year']:04d}-{r['month']:02d}-{r['day']:02d}": r['data'] for r in rows}


def load_days_range(company_id: int, start_date, end_date) -> dict:
    """start_date/end_date: datetime.date. Returns {date_iso: data}."""
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT year, month, day, data FROM company_days
            WHERE company_id = %s
              AND make_date(year, month, day) BETWEEN %s AND %s
            ORDER BY year, month, day
            """,
            (company_id, start_date, end_date),
        )
        rows = cur.fetchall()
    return {f"{r['year']:04d}-{r['month']:02d}-{r['day']:02d}": r['data'] for r in rows}


def save_day(company_id: int, year: int, month: int, day: int, data: dict) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO company_days (company_id, year, month, day, data, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (company_id, year, month, day)
            DO UPDATE SET data = EXCLUDED.data, updated_at = now()
            """,
            (company_id, year, month, day, json.dumps(data)),
        )


def delete_day(company_id: int, year: int, month: int, day: int) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM company_days WHERE company_id=%s AND year=%s AND month=%s AND day=%s",
            (company_id, year, month, day),
        )


# ---- images ----

def find_image_by_hash(company_id: int, file_hash: str) -> dict | None:
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, filename, year, month, day, status FROM voucher_images WHERE company_id=%s AND file_hash=%s",
            (company_id, file_hash),
        )
        return cur.fetchone()


def save_image(company_id: int, file_hash: str, filename: str, content: bytes,
               content_type: str, year: int | None, month: int | None, day: int | None,
               status: str = 'pending') -> int:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO voucher_images
                (company_id, file_hash, filename, content, content_type, year, month, day, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (company_id, file_hash) DO UPDATE SET
                year = EXCLUDED.year, month = EXCLUDED.month, day = EXCLUDED.day,
                status = EXCLUDED.status
            RETURNING id
            """,
            (company_id, file_hash, filename, psycopg2.Binary(content), content_type, year, month, day, status),
        )
        return cur.fetchone()[0]


def set_image_status(image_id: int, status: str, year: int | None = None, month: int | None = None, day: int | None = None) -> None:
    with _conn() as conn, conn.cursor() as cur:
        if year is not None:
            cur.execute(
                "UPDATE voucher_images SET status=%s, year=%s, month=%s, day=%s WHERE id=%s",
                (status, year, month, day, image_id),
            )
        else:
            cur.execute("UPDATE voucher_images SET status=%s WHERE id=%s", (status, image_id))


def list_images(company_id: int) -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, filename, year, month, day, status, uploaded_at, content_type,
                   length(content) AS size_bytes
            FROM voucher_images WHERE company_id=%s ORDER BY uploaded_at DESC
            """,
            (company_id,),
        )
        return cur.fetchall()


def get_image_content(image_id: int) -> tuple[bytes, str] | None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT content, content_type FROM voucher_images WHERE id=%s", (image_id,))
        row = cur.fetchone()
        return (bytes(row[0]), row[1]) if row else None
