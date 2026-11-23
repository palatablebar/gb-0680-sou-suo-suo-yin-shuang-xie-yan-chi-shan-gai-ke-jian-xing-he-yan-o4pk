"""M8 检索裁决：检索规则升级期间的双索引路由与可见范围。

权威可见性只来自源端最终状态（M3/M4 已裁决，含删除墓碑）；
迁移台账（migration_ledger）只声明每个文档的新索引重建状态，不授予可见性。

四条硬规则：
1. 已撤文档（源端 delete 墓碑）一律不命中；新索引缺失也不回落旧索引显示。
2. 尚未重建的可见文档，允许在台账限定范围内回落旧索引命中，不消失。
3. 台账按输入摘要+规则版本冻结处理范围；同一迁移重跑不得新增或改换范围。
4. 旧索引只读保留并先快照封存；切换不删除、不覆盖，丢失即失去复核依据。
"""
import hashlib
import json
from app.errors import DomainError

# 台账允许的重建状态
LEDGER_STATES = {"REBUILT", "PENDING", "OUT_OF_SCOPE"}
# 台账输入必须携带的列
LEDGER_COLUMNS = ["doc_id", "rebuild_state", "scope_epoch", "ledger_version"]
# 路由结论
ROUTE_NEW = "NEW_INDEX"            # 仅查新索引
ROUTE_LEGACY_FALLBACK = "LEGACY_FALLBACK"  # 新索引未就绪，受限回落旧索引
ROUTE_SUPPRESSED = "SUPPRESSED"    # 已撤文档，任何索引都不显示
ROUTE_OUT_OF_SCOPE = "OUT_OF_SCOPE"  # 未纳入本次迁移范围，沿用旧索引但不属回退豁免


def _canonical_ledger(ledger):
    """规范化并校验迁移台账，返回 {doc_id: entry}。"""
    if not isinstance(ledger, list):
        raise DomainError("BAD_LEDGER", "迁移台账需要对象列表")
    normalized = {}
    for entry in ledger:
        if not isinstance(entry, dict) or any(key not in entry for key in LEDGER_COLUMNS):
            raise DomainError("BAD_LEDGER", "台账列必须包含 doc_id/rebuild_state/scope_epoch/ledger_version")
        doc = str(entry["doc_id"])
        state = str(entry["rebuild_state"]).strip()
        if state not in LEDGER_STATES:
            raise DomainError("BAD_LEDGER_STATE", "台账重建状态无效：%s" % state)
        epoch = str(entry["scope_epoch"]).strip()
        if not epoch:
            raise DomainError("BAD_LEDGER", "scope_epoch 不能为空")
        record = {"doc_id": doc, "rebuild_state": state,
                  "scope_epoch": epoch, "ledger_version": str(entry["ledger_version"])}
        # 同一文档重复登记且内容不一致：范围定义自相矛盾，拒绝裁决
        if doc in normalized and normalized[doc] != record:
            raise DomainError("LEDGER_CONFLICT", "同一文档的台账登记冲突：%s" % doc)
        normalized[doc] = record
    return normalized


def ledger_fingerprint(ledger):
    """台账冻结摘要：只取决于规范化后的范围内容，与列表顺序无关。"""
    canonical = json.dumps(sorted(ledger, key=lambda e: str(e["doc_id"])),
                           sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def decide_visibility(row, ledger_entry):
    """单文档裁决。检索页面与归档凭据共用这一个函数，不允许各写一份。

    返回 (route, searchable, reason)。
    row: M6 对账后的文档行，携带 source_op / tombstone / visibility_state。
    ledger_entry: 该文档的台账登记，缺失视为 OUT_OF_SCOPE。
    """
    tombstoned = row.get("tombstone") is True or row.get("source_op") == "delete"
    state = (ledger_entry or {}).get("rebuild_state", "OUT_OF_SCOPE")

    # 规则1：删除墓碑优先于一切索引状态。旧索引里即使仍有残留也不得回落显示。
    if tombstoned:
        return ROUTE_SUPPRESSED, False, "TOMBSTONE_BLOCKS_LEGACY"

    # 规则2：可见文档按重建状态路由。
    if state == "REBUILT":
        return ROUTE_NEW, True, "NEW_INDEX_SERVED"
    if state == "PENDING":
        # 仅对台账显式登记为 PENDING 的文档开放旧索引回落，且只读。
        return ROUTE_LEGACY_FALLBACK, True, "PENDING_LEGACY_FALLBACK"
    return ROUTE_OUT_OF_SCOPE, True, "OUT_OF_SCOPE_LEGACY"


def evaluate(rows, context):
    params = context.get("parameters", {}) or {}
    ledger = _canonical_ledger(params.get("migration_ledger", []))
    epochs = sorted({e["scope_epoch"] for e in ledger.values()})
    ledger_versions = sorted({e["ledger_version"] for e in ledger.values()})
    if len(ledger_versions) > 1:
        raise DomainError("LEDGER_VERSION_MIXED", "同一批次台账版本不一致，范围无法冻结")

    # 规则3：范围冻结。台账摘要写入结果，重跑同一迁移时摘要必须一致，
    # 否则 run_id（输入+参数+规则版本的 digest）本身就会变化，无法复用批次。
    fingerprint = ledger_fingerprint(list(ledger.values()))
    scoped_docs = {doc for doc, e in ledger.items() if e["rebuild_state"] != "OUT_OF_SCOPE"}

    out = []
    seen_docs = set()
    for row in rows:
        doc = str(row["doc_id"])
        if doc in seen_docs:
            raise DomainError("ARCHIVE_DUPLICATE", "裁决业务键重复：%s" % doc)
        seen_docs.add(doc)
        entry = ledger.get(doc)
        route, searchable, reason = decide_visibility(row, entry)
        out.append({
            **row,
            "rebuild_state": (entry or {}).get("rebuild_state", "OUT_OF_SCOPE"),
            "scope_epoch": (entry or {}).get("scope_epoch", "—"),
            "retrieval_route": route,
            "searchable": "1" if searchable else "0",
            "retrieval_reason": reason,
            "legacy_index_readonly": "1" if route in (ROUTE_LEGACY_FALLBACK, ROUTE_OUT_OF_SCOPE) else "0",
        })

    # 台账中声明在迁移范围内、但当前流水线没有任何文档记录的登记：
    # 不允许静默丢弃，显式暴露为无主登记，保证复核依据完整。
    orphan_ledger = sorted(scoped_docs - seen_docs)

    metrics = {
        "documents": len(out),
        "searchable": sum(r["searchable"] == "1" for r in out),
        "suppressed": sum(r["retrieval_route"] == ROUTE_SUPPRESSED for r in out),
        "new_index_served": sum(r["retrieval_route"] == ROUTE_NEW for r in out),
        "legacy_fallback": sum(r["retrieval_route"] == ROUTE_LEGACY_FALLBACK for r in out),
        "out_of_scope": sum(r["retrieval_route"] == ROUTE_OUT_OF_SCOPE for r in out),
        "ledger_scope_size": len(scoped_docs),
        "ledger_orphan_entries": len(orphan_ledger),
        "ledger_fingerprint": fingerprint[:16],
        "scope_epochs": ",".join(epochs) if epochs else "—",
        # 规则4：旧索引在整个迁移窗口保持只读快照，不删除不覆盖。
        "legacy_index_mode": "READONLY_SNAPSHOT",
        "frozen": True,
    }
    return {"rows": out, "quarantine": [], "metrics": metrics,
            "ledger_orphans": orphan_ledger}
