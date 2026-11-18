import sqlite3

from app.db import get_db
from app.services.errors import ServiceError


def list_vouchers(db_path: str | None = None, status: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM stamp_vouchers WHERE status=? ORDER BY id",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stamp_vouchers ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]


def list_conflicts(db_path: str | None = None) -> list[dict]:
    """同一运行码下出现两枚及以上凭证即冲突，含已收束与待收束。"""
    with get_db(db_path) as conn:
        groups = conn.execute(
            "SELECT run_code, COUNT(*) AS n FROM stamp_vouchers "
            "GROUP BY run_code HAVING n >= 2 ORDER BY run_code"
        ).fetchall()
        conflicts: list[dict] = []
        for g in groups:
            rows = conn.execute(
                "SELECT * FROM stamp_vouchers WHERE run_code=? ORDER BY id",
                (g["run_code"],),
            ).fetchall()
            vouchers = [dict(r) for r in rows]
            retained = next((v for v in vouchers if v["status"] == "保留"), None)
            voided = [v for v in vouchers if v["status"] == "作废"]
            # 库存行与保留凭证对不上，或一枚运行码挂了两枚保留凭证，即未收束双影
            batch = conn.execute(
                "SELECT * FROM run_batches WHERE run_code=?",
                (g["run_code"],),
            ).fetchone()
            retained_n = sum(1 for v in vouchers if v["status"] == "保留")
            aligned = (
                retained is not None
                and retained_n == 1
                and batch is not None
                and batch["watermark_value"] == retained["watermark_value"]
            )
            conflicts.append(
                {
                    "run_code": g["run_code"],
                    "conflict_group": retained["conflict_group"] if retained else None,
                    "resolved": aligned,
                    "retained": retained,
                    "voided": voided,
                    "voided_voucher_nos": [v["voucher_no"] for v in voided],
                    "batch": dict(batch) if batch else None,
                }
            )
        return conflicts


def detect_double_shadow(db_path: str | None = None) -> list[dict]:
    """双影巡检：库存行与凭证表不对齐即报，一处都不许有。"""
    with get_db(db_path) as conn:
        problems: list[dict] = []
        rows = conn.execute(
            "SELECT * FROM run_batches WHERE status='已盖印' ORDER BY id"
        ).fetchall()
        for b in rows:
            retained = conn.execute(
                "SELECT * FROM stamp_vouchers WHERE run_code=? AND status='保留'",
                (b["run_code"],),
            ).fetchall()
            if len(retained) != 1:
                problems.append(
                    {
                        "run_code": b["run_code"],
                        "reason": f"保留凭证数为 {len(retained)}，应为 1",
                    }
                )
            elif retained[0]["watermark_value"] != b["watermark_value"]:
                problems.append(
                    {
                        "run_code": b["run_code"],
                        "reason": "库存水印与保留凭证水印不一致",
                    }
                )
        return problems


def reconcile_conflict(run_code: str, db_path: str | None = None) -> dict:
    """去重台一次收束：保留先手凭证，其余凭证判废，库存行对齐保留水印。"""
    code = (run_code or "").strip()
    if not code:
        raise ServiceError("运行码不能为空")
    with get_db(db_path) as conn:
        for attempt in range(3):
            try:
                conn.execute("BEGIN IMMEDIATE")
                batch = conn.execute(
                    "SELECT * FROM run_batches WHERE run_code=?", (code,)
                ).fetchone()
                if not batch:
                    raise ServiceError("运行码不存在", 404)
                vouchers = conn.execute(
                    "SELECT * FROM stamp_vouchers WHERE run_code=? ORDER BY id",
                    (code,),
                ).fetchall()
                if not vouchers:
                    raise ServiceError("该运行码没有盖印凭证可收束", 409)
                group = f"CG-{code}"
                # 先手 = 最早落库的一枚，钉死保留它
                winner = vouchers[0]
                losers = [v for v in vouchers if v["id"] != winner["id"]]
                conn.execute(
                    "UPDATE stamp_vouchers SET status='保留', conflict_group=?, "
                    "retained_voucher_no=NULL WHERE id=?",
                    (group, winner["id"]),
                )
                for loser in losers:
                    conn.execute(
                        "UPDATE stamp_vouchers SET status='作废', conflict_group=?, "
                        "retained_voucher_no=? WHERE id=?",
                        (group, winner["voucher_no"], loser["id"]),
                    )
                # 库存行只对齐保留凭证这一枚水印
                conn.execute(
                    "UPDATE run_batches SET status='已盖印', watermark_value=?, "
                    "stamped_at=COALESCE(stamped_at, datetime('now')) "
                    "WHERE run_code=?",
                    (winner["watermark_value"], code),
                )
                conn.commit()
                return {
                    "ok": True,
                    "run_code": code,
                    "conflict_group": group,
                    "retained_voucher_no": winner["voucher_no"],
                    "retained_watermark": winner["watermark_value"],
                    "voided_voucher_nos": [v["voucher_no"] for v in losers],
                }
            except sqlite3.OperationalError:
                conn.rollback()
                if attempt == 2:
                    raise ServiceError("去重繁忙，请重试", 409)
        raise ServiceError("去重失败，请重试", 409)
