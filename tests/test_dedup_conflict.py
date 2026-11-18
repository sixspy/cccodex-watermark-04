import threading

import pytest

from app.db import get_db, init_db
from app.services import stamp_batch
from app.services.errors import ServiceError
from app.services.vouchers import (
    detect_double_shadow,
    list_conflicts,
    reconcile_conflict,
)


def _db_path(tmp_path):
    path = str(tmp_path / "dedup.db")
    init_db(path)
    return path


def _pending_batch_id(db_path, run_code=None):
    with get_db(db_path) as conn:
        if run_code:
            row = conn.execute(
                "SELECT id FROM run_batches WHERE run_code=?", (run_code,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM run_batches WHERE status='待盖印' ORDER BY id LIMIT 1"
            ).fetchone()
        return row["id"]


def test_concurrent_two_stampers_only_one_voucher_retained(tmp_path):
    """两名盖印员并发抢同一码：排他收束，只留一枚，后手凭证判废。"""
    db_path = _db_path(tmp_path)
    bid = _pending_batch_id(db_path, "ord-20261114-01")
    results: list[dict] = []
    errors: list[ServiceError] = []

    def worker(value, operator):
        try:
            results.append(
                stamp_batch(bid, value, db_path=db_path, operator=operator)
            )
        except ServiceError as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("WM-FIRST", "盖印员甲"))
    t2 = threading.Thread(target=worker, args=("WM-SECOND", "盖印员乙"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 1
    assert len(errors) == 1
    # 中文短句冲突报错，且带回作废/保留凭证号
    loser = errors[0]
    assert loser.status_code == 409
    assert "作废" in loser.message
    assert loser.extra["voided_voucher_no"]
    assert loser.extra["retained_voucher_no"]

    with get_db(db_path) as conn:
        kept = conn.execute(
            "SELECT * FROM stamp_vouchers WHERE run_code='ord-20261114-01' AND status='保留'"
        ).fetchall()
        voided = conn.execute(
            "SELECT * FROM stamp_vouchers WHERE run_code='ord-20261114-01' AND status='作废'"
        ).fetchall()
        batch = conn.execute(
            "SELECT * FROM run_batches WHERE run_code='ord-20261114-01'"
        ).fetchone()
        assert len(kept) == 1
        assert len(voided) == 1
        # 库存行唯一且与保留凭证水印对齐
        assert batch["watermark_value"] == kept[0]["watermark_value"]
        assert voided[0]["retained_voucher_no"] == kept[0]["voucher_no"]
        assert kept[0]["voucher_no"] == loser.extra["retained_voucher_no"]
        assert voided[0]["voucher_no"] == loser.extra["voided_voucher_no"]

    assert detect_double_shadow(db_path) == []


def test_conflict_list_and_reconcile(tmp_path):
    """冲突列表亮保留芯片；去重台一次收束，作废凭证号留痕。"""
    db_path = _db_path(tmp_path)
    bid = _pending_batch_id(db_path, "pay-20261114-01")

    stamp_batch(bid, "WM-PAY-A", db_path=db_path, operator="甲")
    with pytest.raises(ServiceError) as exc_info:
        stamp_batch(bid, "WM-PAY-B", db_path=db_path, operator="乙")
    voided_no = exc_info.value.extra["voided_voucher_no"]

    conflicts = list_conflicts(db_path)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["run_code"] == "pay-20261114-01"
    assert c["retained"]["watermark_value"] == "WM-PAY-A"
    assert c["voided_voucher_nos"] == [voided_no]
    assert c["resolved"] is True

    outcome = reconcile_conflict("pay-20261114-01", db_path=db_path)
    assert outcome["retained_voucher_no"] == c["retained"]["voucher_no"]
    assert outcome["voided_voucher_nos"] == [voided_no]

    # 再次巡检：无双影，库存只听保留凭证
    assert detect_double_shadow(db_path) == []
    with get_db(db_path) as conn:
        n_kept = conn.execute(
            "SELECT COUNT(*) AS c FROM stamp_vouchers "
            "WHERE run_code='pay-20261114-01' AND status='保留'"
        ).fetchone()["c"]
        assert n_kept == 1


def test_gap_report_flags_double_shadow_and_clears_after_reconcile(tmp_path):
    """缺口复算先报双影，收束后再验一遍归零。"""
    from app.services.gaps import run_gap_report

    db_path = _db_path(tmp_path)
    bid = _pending_batch_id(db_path, "gmv-20261114-01")
    stamp_batch(bid, "WM-GMV-1", db_path=db_path, operator="甲")
    with pytest.raises(ServiceError):
        stamp_batch(bid, "WM-GMV-2", db_path=db_path, operator="乙")

    # 正常排他路径下库存与凭证天然对齐：双影应为 0
    report = run_gap_report("2026-11-16", db_path=db_path)
    assert report["double_shadow"] == []
    assert "双影 0 处" in report["summary"]

    # 人为制造双影：库存水印被改得与保留凭证不一致
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE run_batches SET watermark_value='WM-GMV-9' "
            "WHERE run_code='gmv-20261114-01'"
        )
    shadows = detect_double_shadow(db_path)
    assert len(shadows) == 1
    bad_report = run_gap_report("2026-11-16", db_path=db_path)
    assert len(bad_report["double_shadow"]) == 1
    assert "双影 1 处" in bad_report["summary"]

    # 去重台收束后库存重新对齐，再验一遍
    reconcile_conflict("gmv-20261114-01", db_path=db_path)
    assert detect_double_shadow(db_path) == []
    clean_report = run_gap_report("2026-11-16", db_path=db_path)
    assert clean_report["double_shadow"] == []
    assert "双影 0 处" in clean_report["summary"]


@pytest.mark.asyncio
async def test_conflict_api_and_voucher_endpoints(authed):
    """HTTP 层：冲突报错带凭证号，去重台/冲突列表/双影巡检端点可用。"""
    all_b = (await (await authed.get("/api/batches")).json())["items"]
    bid = next(b["id"] for b in all_b if b["status"] == "待盖印")

    first = await authed.post(
        f"/api/batches/{bid}/stamp",
        json={"watermark_value": "WM-HTTP-1", "operator": "甲"},
    )
    assert first.status == 200
    fbody = await first.json()
    assert fbody["batch"]["voucher_no"]

    second = await authed.post(
        f"/api/batches/{bid}/stamp",
        json={"watermark_value": "WM-HTTP-2", "operator": "乙"},
    )
    assert second.status == 409
    sbody = await second.json()
    assert sbody["ok"] is False
    assert "作废" in sbody["error"]
    assert sbody["voided_voucher_no"]
    assert sbody["retained_voucher_no"]

    conflicts = (await (await authed.get("/api/conflicts")).json())["items"]
    assert any(
        c["retained"]["voucher_no"] == sbody["retained_voucher_no"]
        and sbody["voided_voucher_no"] in c["voided_voucher_nos"]
        for c in conflicts
    )

    vouchers = (await (await authed.get("/api/vouchers")).json())["items"]
    statuses = {v["voucher_no"]: v["status"] for v in vouchers}
    assert statuses[sbody["retained_voucher_no"]] == "保留"
    assert statuses[sbody["voided_voucher_no"]] == "作废"

    shadow = (await (await authed.get("/api/double-shadow")).json())["items"]
    assert shadow == []

    rec = await authed.post(
        "/api/conflicts/reconcile",
        json={"run_code": _run_code(all_b, bid)},
    )
    assert rec.status == 200
    rbody = await rec.json()
    assert rbody["retained_voucher_no"] == sbody["retained_voucher_no"]
    assert sbody["voided_voucher_no"] in rbody["voided_voucher_nos"]


def _run_code(batches, bid):
    return next(b["run_code"] for b in batches if b["id"] == bid)
