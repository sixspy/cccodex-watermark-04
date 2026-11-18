# cc-watermark-lab

CC_Codex 二期（26.11）**数据工程**产品线：**血缘水印台**（BlackSheep + Alpine CDN + SQLite）。

## 一句话骨架（自有，非外仓抄题）

**产出表水印模板 → 批次盖印 → 下游追索 → 缺口对账**

与现有七线正交（不是采集目录、ETL 管道、同步源、解析入库、编排流、种子摄取、留存裁撤）。
不碰外仓常见指纹（Feed 归并 / CDC 游标 / 质检签发 / Slicegate 类仓名）。

## 领域

- **所属领域**：数据工程
- **子方向**：数据血缘与水印（产出表盖印 / 追索闭环）

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12、BlackSheep、Uvicorn、SQLite |
| 前端 | 原生 JS SPA（全中文，底栏），雾灰底 + 青柠强调；不依赖外网 CDN |
| 测试 | pytest + httpx（本地秒级闭环，优先于 compose） |

## 基线已通主链

1. 产出表水印模板（表名 / 列名 / 现行版本）
2. 向运行批次盖印水印值
3. 按水印值追索下游批次
4. 缺口对账（已盖印 vs 应盖印）

预留切口（未做，供后续功能题）：模板换版生效窗、盖印作废回灌、跨表依赖冻结、追索深度限制等。

## 双影收束（watermark-04）

同一运行码被两名盖印员并发抢盖时，缺排他会双写各留一枚水印。本台钉死的落点：

- **旁路凭证表 `stamp_vouchers`**：每次盖印都发一枚凭证（`SV-日期-序号`），同一运行码至多一枚 `保留`，后手落 `作废` 并回指保留凭证号；部分唯一索引 `idx_vouchers_one_retained` 在库内核兜底。
- **事务内删后写（实际为先写凭证再改库存，同一 `BEGIN IMMEDIATE` 事务）**：WAL + busy_timeout 让并发盖印在写锁上排队，一损俱损；不采用“整码整笔回滚后重盖”，因为作废凭证号必须在去重台留痕可亮。
- **保留谁、听谁**：先手凭证（最早落库）保留，库存行 `watermark_value` 只对齐保留凭证；后续盖印冲突一律中文短句报错并带保留/作废凭证号。
- 新端点：`GET /api/conflicts`（冲突列表，亮保留芯片）、`GET /api/vouchers`（去重台全量凭证）、`POST /api/conflicts/reconcile`（一次收束）、`GET /api/double-shadow`（双影巡检）。
- 缺口复算同时核双影，摘要尾部带“双影 N 处”；双影即停，先去重再对账。


## 本地（推荐反馈环）

```powershell
cd claude-workspace\cc-watermark-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python run.py
```

http://127.0.0.1:7000/login · **admin / 123456**

## Docker

```powershell
docker compose up -d --build
```

端口 **7000**。冒烟：`python ..\scripts\_smoke_lab_access.py --dir .`
