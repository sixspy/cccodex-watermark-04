from app.db import get_db


def list_reports(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM gap_reports ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def run_gap_report(as_of_date: str, db_path: str | None = None) -> dict:
    day = (as_of_date or "").strip() or "2026-11-16"
    with get_db(db_path) as conn:
        expected = conn.execute(
            "SELECT COUNT(*) AS c FROM run_batches WHERE biz_date<=? AND status!='已作废'",
            (day,),
        ).fetchone()["c"]
        stamped = conn.execute(
            "SELECT COUNT(*) AS c FROM run_batches WHERE biz_date<=? AND status='已盖印'",
            (day,),
        ).fetchone()["c"]
        gap = expected - stamped
        # 复算时顺手核双影：已盖印库存行必须恰好对应一枚保留凭证
        shadow_rows = conn.execute(
            "SELECT b.run_code, b.watermark_value AS bwm, "
            "COUNT(v.id) AS kept, "
            "GROUP_CONCAT(v.watermark_value) AS vwm "
            "FROM run_batches b LEFT JOIN stamp_vouchers v "
            "ON v.run_code = b.run_code AND v.status='保留' "
            "WHERE b.biz_date<=? AND b.status='已盖印' "
            "GROUP BY b.id HAVING kept != 1 OR vwm IS NULL OR vwm != bwm",
            (day,),
        ).fetchall()
        double_shadow = [
            {
                "run_code": r["run_code"],
                "reason": "双影：保留凭证与库存水印对不齐",
            }
            for r in shadow_rows
        ]
        suffix = "，双影 0 处" if not double_shadow else f"，双影 {len(double_shadow)} 处"
        summary = (
            f"截至 {day}：应盖印 {expected}，已盖印 {stamped}，缺口 {gap}{suffix}"
        )
        cur = conn.execute(
            "INSERT INTO gap_reports(as_of_date, expected_count, stamped_count, gap_count, summary) "
            "VALUES (?,?,?,?,?)",
            (day, expected, stamped, gap, summary),
        )
        row = conn.execute(
            "SELECT * FROM gap_reports WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        report = dict(row)
        report["double_shadow"] = double_shadow
        return report
