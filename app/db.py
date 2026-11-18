import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    # WAL 让读写不互斥；busy_timeout 让并发盖印在锁上排队而不是立刻报锁错
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS watermark_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL UNIQUE,
  column_name TEXT NOT NULL,
  version_no INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK (status IN ('现行','停用')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  run_code TEXT NOT NULL UNIQUE,
  biz_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('待盖印','已盖印','已作废')),
  watermark_value TEXT,
  stamped_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lineage_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  upstream_run_code TEXT NOT NULL,
  downstream_run_code TEXT NOT NULL,
  UNIQUE(upstream_run_code, downstream_run_code)
);

CREATE TABLE IF NOT EXISTS gap_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of_date TEXT NOT NULL,
  expected_count INTEGER NOT NULL,
  stamped_count INTEGER NOT NULL,
  gap_count INTEGER NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 旁路盖印凭证表：每一次盖印（含被挤掉的后手）都留痕，
-- 运行码同一时刻只许有一枚“保留”凭证，其余落“作废”。
CREATE TABLE IF NOT EXISTS stamp_vouchers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  voucher_no TEXT NOT NULL UNIQUE,
  run_code TEXT NOT NULL,
  watermark_value TEXT NOT NULL,
  operator TEXT NOT NULL DEFAULT '盖印员',
  status TEXT NOT NULL CHECK (status IN ('保留','作废')),
  conflict_group TEXT,
  retained_voucher_no TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 排他落点：同一运行码至多一枚保留凭证，缺它并发会双写各留一枚
CREATE UNIQUE INDEX IF NOT EXISTS idx_vouchers_one_retained
  ON stamp_vouchers(run_code) WHERE status = '保留';
CREATE INDEX IF NOT EXISTS idx_vouchers_run_code
  ON stamp_vouchers(run_code);
"""

SEED_TEMPLATES = [
    ("dw_orders_d", "wm_batch", 1, "现行"),
    ("dw_payments_d", "wm_batch", 1, "现行"),
    ("dm_gmv_d", "wm_trace", 1, "现行"),
]

SEED_BATCHES = [
    ("dw_orders_d", "ord-20261114-01", "2026-11-14", "待盖印", None),
    ("dw_payments_d", "pay-20261114-01", "2026-11-14", "待盖印", None),
    ("dm_gmv_d", "gmv-20261114-01", "2026-11-14", "待盖印", None),
    ("dw_orders_d", "ord-20261113-01", "2026-11-13", "已盖印", "WM-ORD-131"),
]

SEED_EDGES = [
    ("ord-20261114-01", "gmv-20261114-01"),
    ("pay-20261114-01", "gmv-20261114-01"),
    ("ord-20261113-01", "gmv-20261114-01"),
]

# 历史已盖印批次补一枚保留凭证，保证库存行与凭证表对得齐
SEED_VOUCHERS = [
    ("SV-20261113-0001", "ord-20261113-01", "WM-ORD-131", "系统"),
]


def init_db(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
        if row:
            return
        for table, col, ver, status in SEED_TEMPLATES:
            conn.execute(
                "INSERT INTO watermark_templates(table_name, column_name, version_no, status) "
                "VALUES (?,?,?,?)",
                (table, col, ver, status),
            )
        for table, code, biz, status, wm in SEED_BATCHES:
            conn.execute(
                "INSERT INTO run_batches(table_name, run_code, biz_date, status, watermark_value, stamped_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    table,
                    code,
                    biz,
                    status,
                    wm,
                    "2026-11-13 18:00:00" if status == "已盖印" else None,
                ),
            )
        for up, down in SEED_EDGES:
            conn.execute(
                "INSERT INTO lineage_edges(upstream_run_code, downstream_run_code) VALUES (?,?)",
                (up, down),
            )
        for voucher_no, code, wm, operator in SEED_VOUCHERS:
            conn.execute(
                "INSERT INTO stamp_vouchers(voucher_no, run_code, watermark_value, operator, status) "
                "VALUES (?,?,?,?,'保留')",
                (voucher_no, code, wm, operator),
            )
        conn.execute("INSERT INTO meta(key, value) VALUES ('seeded','1')")
