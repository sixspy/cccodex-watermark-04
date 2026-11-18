(() => {
  const view = document.getElementById("view");
  const msgEl = document.getElementById("msg");
  let tab = "batches";
  let templates = [];
  let batches = [];
  let reports = [];
  let vouchers = [];
  let conflicts = [];
  let selected = null;
  let stampValue = "";
  let stampOperator = "盖印员";
  let lastStampVoucher = null;
  let traceValue = "WM-ORD-131";
  let traceResult = null;

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (r.status === 401) {
      location.href = "/login";
      throw new Error("未登录");
    }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(j.error || ("HTTP " + r.status));
      err.payload = j;
      throw err;
    }
    return j;
  }

  function setMsg(t) {
    msgEl.textContent = t || "";
  }

  async function refresh() {
    const meta = await api("/api/meta");
    document.getElementById("lab-name").textContent = meta.lab_name || "血缘水印台";
    templates = (await api("/api/templates")).items || [];
    batches = (await api("/api/batches")).items || [];
    reports = (await api("/api/gap-reports")).items || [];
    vouchers = (await api("/api/vouchers")).items || [];
    conflicts = (await api("/api/conflicts")).items || [];
  }

  function syncDock() {
    document.querySelectorAll(".dock [data-tab]").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.tab === tab);
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(
      /[&<>"']/g,
      (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  function statusChip(status) {
    const cls = status === "保留" ? "chip chip-keep" : status === "作废" ? "chip chip-void" : "chip";
    return `<span class="${cls}">${esc(status)}</span>`;
  }

  function renderTemplates() {
    return `
      <h2>水印模板</h2>
      <table>
        <thead><tr><th>表</th><th>列</th><th>版本</th><th>状态</th></tr></thead>
        <tbody>
          ${templates
            .map(
              (t) => `<tr>
            <td>${esc(t.table_name)}</td>
            <td>${esc(t.column_name)}</td>
            <td>${t.version_no}</td>
            <td>${statusChip(t.status)}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderBatches() {
    return `
      <h2>运行批次</h2>
      <p class="muted">主链：选批盖印 → 用水印追索下游 → 缺口对账。并发抢同一码时后手凭证自动判废。</p>
      <table>
        <thead><tr><th>编号</th><th>表</th><th>运行码</th><th>业务日</th><th>状态</th><th>水印</th><th>凭证号</th><th></th></tr></thead>
        <tbody>
          ${batches
            .map((b) => {
              const v = vouchers.find(
                (x) => x.run_code === b.run_code && x.status === "保留"
              );
              return `<tr>
            <td>${b.id}</td>
            <td>${esc(b.table_name)}</td>
            <td>${esc(b.run_code)}</td>
            <td>${esc(b.biz_date)}</td>
            <td>${statusChip(b.status)}</td>
            <td>${esc(b.watermark_value || "-")}</td>
            <td>${v ? esc(v.voucher_no) : "-"}</td>
            <td>${
              b.status === "待盖印"
                ? `<button type="button" class="ghost" data-pick="${b.id}">盖印</button>`
                : ""
            }</td>
          </tr>`;
            })
            .join("")}
        </tbody>
      </table>
      ${
        selected
          ? `<div class="cardish">
        <h3>盖印</h3>
        <p>${esc(selected.run_code)} · ${esc(selected.table_name)}</p>
        <label>盖印员<input id="stamp-operator" value="${esc(stampOperator)}" /></label>
        <label>水印值<input id="stamp-input" value="${esc(stampValue)}" /></label>
        <button type="button" id="btn-stamp">写入盖印</button>
        ${
          lastStampVoucher
            ? `<div class="appendix" id="stamp-appendix">
               <p class="muted">盖印附页 · 合并结局</p>
               <p>凭证号 <strong>${esc(lastStampVoucher.voucher_no)}</strong>
                  ${statusChip("保留")}
                  运行码 ${esc(lastStampVoucher.run_code)}</p>
             </div>`
            : ""
        }
      </div>`
          : ""
      }`;
  }

  function renderTrace() {
    const src = (traceResult && traceResult.sources) || [];
    const down = (traceResult && traceResult.downstream) || [];
    return `
      <h2>下游追索</h2>
      <label>水印值<input id="trace-input" value="${esc(traceValue)}" placeholder="例如 WM-ORD-131" /></label>
      <div class="row"><button type="button" id="btn-trace">追索</button></div>
      ${
        traceResult
          ? `<div>
        <h3>源批次</h3>
        <ul>${src
          .map((s) => `<li>${esc(s.run_code)} · ${esc(s.table_name)}</li>`)
          .join("")}</ul>
        <h3>下游</h3>
        <ul>${down
          .map(
            (d) =>
              `<li>${esc(d.run_code)} · ${esc(d.status)} · ${esc(
                d.watermark_value || "未盖印"
              )}</li>`
          )
          .join("")}</ul>
      </div>`
          : ""
      }`;
  }

  function renderGaps() {
    return `
      <h2>缺口对账</h2>
      <button type="button" id="btn-gap">生成对账</button>
      <table>
        <thead><tr><th>编号</th><th>截至</th><th>缺口</th><th>摘要</th></tr></thead>
        <tbody>
          ${reports
            .map(
              (r) => `<tr>
            <td>${r.id}</td>
            <td>${esc(r.as_of_date)}</td>
            <td>${r.gap_count}</td>
            <td>${esc(r.summary)}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderDedup() {
    const conflictBlocks = conflicts.length
      ? conflicts
          .map((c) => {
            const retained = c.retained;
            const voided = c.voided || [];
            return `<div class="cardish conflict-card">
              <h3>运行码 ${esc(c.run_code)}
                <span class="chip ${c.resolved ? "chip-keep" : "chip-void"}">${
              c.resolved ? "已收束" : "待收束"
            }</span>
              </h3>
              ${
                retained
                  ? `<p>保留凭证 ${statusChip("保留")}
                      <strong>${esc(retained.voucher_no)}</strong>
                      · 水印 ${esc(retained.watermark_value)}
                      · ${esc(retained.operator)}</p>`
                  : `<p class="err">无保留凭证</p>`
              }
              ${
                voided.length
                  ? `<p>作废凭证 ${voided
                      .map(
                        (v) =>
                          `${statusChip("作废")}<strong class="void-no">${esc(
                            v.voucher_no
                          )}</strong>`
                      )
                      .join("　")}</p>`
                  : `<p class="muted">暂无作废凭证</p>`
              }
              ${
                c.resolved
                  ? ""
                  : `<button type="button" class="ghost" data-reconcile="${esc(
                      c.run_code
                    )}">点合并冲突</button>`
              }
            </div>`;
          })
          .join("")
      : `<p class="muted">暂无冲突：每个运行码只有一枚保留凭证。</p>`;

    return `
      <h2>去重台</h2>
      <p class="muted">同一运行码挂多枚凭证时在此一次收束：先手凭证保留，后手凭证判废，库存行对齐保留水印。</p>
      <h3>冲突列表</h3>
      ${conflictBlocks}
      <h3>盖印凭证全量</h3>
      <table>
        <thead><tr><th>凭证号</th><th>运行码</th><th>水印值</th><th>盖印员</th><th>状态</th><th>冲突组</th></tr></thead>
        <tbody>
          ${vouchers
            .map(
              (v) => `<tr>
            <td class="${v.status === "作废" ? "void-no" : ""}">${esc(v.voucher_no)}</td>
            <td>${esc(v.run_code)}</td>
            <td>${esc(v.watermark_value)}</td>
            <td>${esc(v.operator)}</td>
            <td>${statusChip(v.status)}</td>
            <td>${esc(v.conflict_group || "-")}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function render() {
    if (tab === "templates") view.innerHTML = renderTemplates();
    else if (tab === "batches") view.innerHTML = renderBatches();
    else if (tab === "trace") view.innerHTML = renderTrace();
    else if (tab === "gaps") view.innerHTML = renderGaps();
    else view.innerHTML = renderDedup();

    view.querySelectorAll("[data-pick]").forEach((btn) => {
      btn.onclick = () => {
        const id = Number(btn.dataset.pick);
        selected = batches.find((b) => b.id === id) || null;
        stampValue = selected ? "WM-" + selected.run_code.toUpperCase() : "";
        lastStampVoucher = null;
        render();
      };
    });
    const stampInput = document.getElementById("stamp-input");
    if (stampInput) {
      stampInput.oninput = () => (stampValue = stampInput.value);
      const opInput = document.getElementById("stamp-operator");
      if (opInput) opInput.oninput = () => (stampOperator = opInput.value);
      document.getElementById("btn-stamp").onclick = async () => {
        setMsg("");
        try {
          const r = await api("/api/batches/" + selected.id + "/stamp", {
            method: "POST",
            body: JSON.stringify({
              watermark_value: stampValue,
              operator: stampOperator,
            }),
          });
          lastStampVoucher = {
            voucher_no: r.batch.voucher_no,
            run_code: selected.run_code,
          };
          selected = null;
          await refresh();
          setMsg("盖印完成，凭证 " + (r.batch.voucher_no || "") + " 已保留");
          render();
        } catch (e) {
          // 冲突收束：亮保留芯片与作废凭证号
          const p = e.payload || {};
          if (p.conflict) {
            setMsg(
              `合并结局：${e.message}（运行码 ${p.run_code}，保留 ${p.retained_voucher_no}，作废 ${p.voided_voucher_no}）`
            );
          } else {
            setMsg(e.message);
          }
          await refresh();
          render();
        }
      };
    }
    const traceInput = document.getElementById("trace-input");
    if (traceInput) {
      traceInput.oninput = () => (traceValue = traceInput.value);
      document.getElementById("btn-trace").onclick = async () => {
        setMsg("");
        try {
          traceResult = await api("/api/trace", {
            method: "POST",
            body: JSON.stringify({ watermark_value: traceValue }),
          });
          setMsg("追索完成");
          render();
        } catch (e) {
          setMsg(e.message);
        }
      };
    }
    const gapBtn = document.getElementById("btn-gap");
    if (gapBtn) {
      gapBtn.onclick = async () => {
        setMsg("");
        try {
          const r = await api("/api/gap-reports", {
            method: "POST",
            body: JSON.stringify({ as_of_date: "2026-11-16" }),
          });
          await refresh();
          const ds = (r.report.double_shadow || []).length;
          setMsg(r.report.summary + (ds ? "，双影即停请先去重" : "，库存与凭证对齐"));
          render();
        } catch (e) {
          setMsg(e.message);
        }
      };
    }
    view.querySelectorAll("[data-reconcile]").forEach((btn) => {
      btn.onclick = async () => {
        const code = btn.dataset.reconcile;
        setMsg("");
        try {
          const r = await api("/api/conflicts/reconcile", {
            method: "POST",
            body: JSON.stringify({ run_code: code }),
          });
          await refresh();
          setMsg(
            `合并结局：运行码 ${r.run_code} 保留 ${r.retained_voucher_no}` +
              (r.voided_voucher_nos.length
                ? "，作废 " + r.voided_voucher_nos.join("、")
                : "")
          );
          render();
        } catch (e) {
          setMsg(e.message);
        }
      };
    });
  }

  document.querySelectorAll(".dock [data-tab]").forEach((btn) => {
    btn.onclick = () => {
      tab = btn.dataset.tab;
      syncDock();
      render();
    };
  });
  document.getElementById("btn-logout").onclick = () => {
    fetch("/api/logout", { method: "POST" }).then(() => (location.href = "/login"));
  };

  refresh()
    .then(() => {
      syncDock();
      render();
    })
    .catch((e) => setMsg(e.message));
})();
