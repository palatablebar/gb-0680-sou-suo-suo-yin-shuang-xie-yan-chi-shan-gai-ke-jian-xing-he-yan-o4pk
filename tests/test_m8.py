import pytest
from app.modules.m8_retrieval.rules import evaluate, decide_visibility, ledger_fingerprint
from app.errors import DomainError

EPOCH = "epoch-2026-11-23"
LV = "ledger-3"


def entry(doc, state, epoch=EPOCH, version=LV):
    return {"doc_id": doc, "rebuild_state": state, "scope_epoch": epoch, "ledger_version": version}


def ctx(ledger):
    return {"parameters": {"migration_ledger": ledger}, "quarantine": []}


def doc(doc_id, op="upsert", tombstone=None):
    return {"doc_id": doc_id, "source_op": op,
            "tombstone": op == "delete" if tombstone is None else tombstone,
            "visibility_state": "CONSISTENT", "_sources": [1]}


# 规则1：已撤文档不得因新索引缺失而回落显示
def test_tombstoned_doc_never_falls_back_to_legacy():
    # 即使台账声称 PENDING（新索引缺失），删除墓碑仍压制一切命中
    rows = evaluate([doc("d1", op="delete")], ctx([entry("d1", "PENDING")]))["rows"]
    assert rows[0]["retrieval_route"] == "SUPPRESSED"
    assert rows[0]["searchable"] == "0"
    assert rows[0]["retrieval_reason"] == "TOMBSTONE_BLOCKS_LEGACY"


def test_tombstoned_doc_suppressed_even_when_rebuilt():
    rows = evaluate([doc("d1", op="delete")], ctx([entry("d1", "REBUILT")]))["rows"]
    assert rows[0]["retrieval_route"] == "SUPPRESSED"
    assert rows[0]["searchable"] == "0"


def test_decide_visibility_unit_matches_pipeline():
    route, searchable, reason = decide_visibility(doc("d", op="delete"), entry("d", "PENDING"))
    assert (route, searchable) == ("SUPPRESSED", False)


# 规则2：尚未重建的可见文档仍须保留检索命中
def test_pending_visible_doc_kept_via_legacy_fallback():
    rows = evaluate([doc("d2")], ctx([entry("d2", "PENDING")]))["rows"]
    assert rows[0]["retrieval_route"] == "LEGACY_FALLBACK"
    assert rows[0]["searchable"] == "1"
    assert rows[0]["legacy_index_readonly"] == "1"


def test_rebuilt_doc_served_from_new_index_only():
    rows = evaluate([doc("d3")], ctx([entry("d3", "REBUILT")]))["rows"]
    assert rows[0]["retrieval_route"] == "NEW_INDEX"
    assert rows[0]["searchable"] == "1"
    assert rows[0]["legacy_index_readonly"] == "0"


def test_doc_without_ledger_entry_is_out_of_scope_not_dropped():
    rows = evaluate([doc("d4")], ctx([]))["rows"]
    assert rows[0]["retrieval_route"] == "OUT_OF_SCOPE"
    assert rows[0]["searchable"] == "1"


# 规则3：重跑同一迁移不能新增或改换处理范围
def test_rerun_same_ledger_identical_fingerprint():
    ledger_a = [entry("d1", "REBUILT"), entry("d2", "PENDING")]
    ledger_b = [entry("d2", "PENDING"), entry("d1", "REBUILT")]  # 换序
    assert ledger_fingerprint(ledger_a) == ledger_fingerprint(ledger_b)


def test_changed_scope_changes_fingerprint():
    base = [entry("d1", "REBUILT"), entry("d2", "PENDING")]
    widened = [entry("d1", "REBUILT"), entry("d2", "REBUILT")]
    assert ledger_fingerprint(base) != ledger_fingerprint(widened)


def test_mixed_ledger_versions_rejected():
    with pytest.raises(DomainError) as e:
        evaluate([doc("d1")], ctx([entry("d1", "REBUILT", version="v1"),
                                   entry("d2", "PENDING", version="v2")]))
    assert e.value.code == "LEDGER_VERSION_MIXED"


def test_conflicting_ledger_entries_rejected():
    with pytest.raises(DomainError) as e:
        evaluate([doc("d1")], ctx([entry("d1", "REBUILT"), entry("d1", "PENDING")]))
    assert e.value.code == "LEDGER_CONFLICT"


def test_bad_ledger_state_rejected():
    with pytest.raises(DomainError) as e:
        evaluate([doc("d1")], ctx([entry("d1", "HALF_DONE")]))
    assert e.value.code == "BAD_LEDGER_STATE"


def test_orphan_ledger_entry_is_exposed_not_silently_dropped():
    result = evaluate([doc("d1")], ctx([entry("d1", "REBUILT"), entry("ghost", "PENDING")]))
    assert result["ledger_orphans"] == ["ghost"]
    assert result["metrics"]["ledger_orphan_entries"] == 1


# 规则4：旧索引只读快照，复核依据保留
def test_legacy_index_kept_readonly_snapshot():
    result = evaluate([doc("d1")], ctx([entry("d1", "PENDING")]))
    assert result["metrics"]["legacy_index_mode"] == "READONLY_SNAPSHOT"
    assert result["metrics"]["frozen"] is True


# 真实样例：8 个删除墓碑全部压制，可见文档默认范围外仍可命中
def test_real_sample_tombstones_suppressed(result):
    m8 = result["stages"][7]
    suppressed = [r for r in m8["rows"] if r["retrieval_route"] == "SUPPRESSED"]
    assert len(suppressed) == 8
    assert all(r["searchable"] == "0" for r in suppressed)


def test_real_sample_visible_docs_remain_searchable_without_ledger(result):
    m8 = result["stages"][7]
    visible = [r for r in m8["rows"] if r["searchable"] == "1"]
    assert {r["doc_id"] for r in visible} == {"0090", "0091"}


def test_real_sample_full_ledger_routes(result):
    from app.pipeline import calculate, load_sample
    ledger = [entry(d, "REBUILT") for d in ("0001", "0002", "0091")] + \
             [entry("0090", "PENDING")]
    rerun = calculate(load_sample("dirty.csv"), {"migration_ledger": ledger})
    m8 = rerun["stages"][7]
    by_doc = {r["doc_id"]: r for r in m8["rows"]}
    # 已撤文档即使 REBUILT 也不显示
    assert by_doc["0001"]["retrieval_route"] == "SUPPRESSED"
    # 已重建的可见文档只走新索引
    assert by_doc["0091"]["retrieval_route"] == "NEW_INDEX"
    # 未重建的可见文档受限回落旧索引，不消失
    assert by_doc["0090"]["retrieval_route"] == "LEGACY_FALLBACK"
    assert by_doc["0090"]["searchable"] == "1"


def test_rerun_same_migration_reuses_run_id():
    from app.pipeline import execute, load_sample
    from app import store
    ledger = [entry("0090", "PENDING"), entry("0091", "REBUILT")]
    a = execute(load_sample("dirty.csv"), {"migration_ledger": ledger})
    b = execute(load_sample("dirty.csv"), {"migration_ledger": ledger})
    assert a["run_id"] == b["run_id"] and len(store.history()) == 1


def test_widened_scope_creates_distinct_run():
    from app.pipeline import execute, load_sample
    from app import store
    a = execute(load_sample("dirty.csv"), {"migration_ledger": [entry("0090", "PENDING")]})
    b = execute(load_sample("dirty.csv"),
                {"migration_ledger": [entry("0090", "PENDING"), entry("0091", "PENDING")]})
    assert a["run_id"] != b["run_id"] and len(store.history()) == 2


def test_stage_preview_accepts_rows_and_context():
    from app.pipeline import stage_preview
    payload = {"rows": [doc("d1"), doc("d2", op="delete")],
               "context": {"parameters": {"migration_ledger": [entry("d1", "PENDING")]},
                           "quarantine": []}}
    out = stage_preview(7, payload)
    by = {r["doc_id"]: r for r in out["rows"]}
    assert by["d1"]["retrieval_route"] == "LEGACY_FALLBACK"
    assert by["d2"]["retrieval_route"] == "SUPPRESSED"


def test_malformed_ledger_rejected():
    with pytest.raises(DomainError) as e:
        evaluate([doc("d1")], ctx([{"doc_id": "d1"}]))
    assert e.value.code == "BAD_LEDGER"
