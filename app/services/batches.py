import sqlite3
from datetime import date

from app.db import get_db
from app.services.errors import ServiceError


def list_batches(db_path: str | None = None, status: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM run_batches WHERE status=? ORDER BY id",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM run_batches ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]


def get_batch(batch_id: int, db_path: str | None = None) -> dict | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM run_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return dict(row) if row else None


def _voucher_prefix() -> str:
    return f"SV-{date.today().strftime('%Y%m%d')}-"


def _next_voucher_no(conn: sqlite3.Connection) -> str:
    prefix = _voucher_prefix()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM stamp_vouchers "
        "WHERE voucher_no LIKE ?",
        (prefix + "%",),
    ).fetchone()
    # 以当日序号发号；并发撞号时靠 UNIQUE 兜底重试
    return f"{prefix}{row['c'] + 1:04d}"


def _insert_voucher(conn, voucher_no, run_code, value, operator, status,
                    conflict_group=None, retained_no=None) -> None:
    conn.execute(
        "INSERT INTO stamp_vouchers(voucher_no, run_code, watermark_value, "
        "operator, status, conflict_group, retained_voucher_no) "
        "VALUES (?,?,?,?,?,?,?)",
        (voucher_no, run_code, value, operator, status,
         conflict_group, retained_no),
    )


def stamp_batch(
    batch_id: int,
    watermark_value: str,
    db_path: str | None = None,
    operator: str = "盖印员",
) -> dict:
    value = (watermark_value or "").strip()
    if not value:
        raise ServiceError("水印值不能为空")
    last_db_error: Exception | None = None
    with get_db(db_path) as conn:
        # IMMEDIATE 立刻拿写锁：两名盖印员抢同一码时在此排队，杜绝双写
        for attempt in range(3):
            try:
                conn.execute("BEGIN IMMEDIATE")
                batch = conn.execute(
                    "SELECT * FROM run_batches WHERE id=?", (batch_id,)
                ).fetchone()
                if not batch:
                    raise ServiceError("批次不存在", 404)
                run_code = batch["run_code"]
                retained = conn.execute(
                    "SELECT * FROM stamp_vouchers WHERE run_code=? AND status='保留'",
                    (run_code,),
                ).fetchone()
                if batch["status"] == "已盖印" or retained is not None:
                    # 后手来了：它的凭证先落“作废”，与库存先手凭证同一冲突组
                    group = f"CG-{run_code}"
                    loser_no = _next_voucher_no(conn)
                    _insert_voucher(
                        conn, loser_no, run_code, value, operator, "作废",
                        conflict_group=group,
                        retained_no=retained["voucher_no"] if retained else None,
                    )
                    if retained is not None and not retained["conflict_group"]:
                        conn.execute(
                            "UPDATE stamp_vouchers SET conflict_group=? WHERE id=?",
                            (group, retained["id"]),
                        )
                    conn.commit()
                    kept_no = retained["voucher_no"] if retained else None
                    raise ServiceError(
                        f"该运行码已盖印，后手凭证 {loser_no} 已作废，保留 {kept_no}",
                        409,
                        extra={
                            "conflict": True,
                            "run_code": run_code,
                            "retained_voucher_no": kept_no,
                            "voided_voucher_no": loser_no,
                            "conflict_group": group,
                        },
                    )
                if batch["status"] != "待盖印":
                    raise ServiceError("仅待盖印批次可盖印", 409)
                tpl = conn.execute(
                    "SELECT * FROM watermark_templates "
                    "WHERE table_name=? AND status='现行'",
                    (batch["table_name"],),
                ).fetchone()
                if not tpl:
                    raise ServiceError("无现行水印模板", 409)
                voucher_no = _next_voucher_no(conn)
                # 事务内：先写保留凭证，再改库存行，一损俱损
                _insert_voucher(
                    conn, voucher_no, run_code, value, operator, "保留"
                )
                conn.execute(
                    "UPDATE run_batches SET status='已盖印', watermark_value=?, "
                    "stamped_at=datetime('now') WHERE id=?",
                    (value, batch_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM run_batches WHERE id=?", (batch_id,)
                ).fetchone()
                result = dict(row)
                result["voucher_no"] = voucher_no
                result["operator"] = operator
                return result
            except sqlite3.OperationalError as exc:  # 锁等待/发号撞车，整笔重来
                last_db_error = exc
                conn.rollback()
                if attempt == 2:
                    raise ServiceError("盖印繁忙，请重试", 409) from exc
        # 理论不可达，兜底
        raise ServiceError("盖印失败，请重试", 409) from last_db_error
