import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","retrieval_route","searchable","evidence"])
    meta=run["manifest"]
    final=run["stages"][-1]
    # 归档与检索页面共用 M8 的同一裁决行：路由/可命中直接取自最终阶段，不在导出处二次判断。
    for row in final["rows"]:
        kind="suppressed" if row.get("retrieval_route")=="SUPPRESSED" else "accepted"
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],kind,json.dumps(row.get("_sources",[])),row.get("retrieval_reason",row.get("status","")),row.get("retrieval_route",""),row.get("searchable",""),json.dumps(row,ensure_ascii=False)])
    for row in final.get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),"","0",json.dumps(row,ensure_ascii=False)])
    # 无主台账登记同样入凭据：旧索引切换后这些是复核范围完整性的唯一依据。
    for doc in final.get("ledger_orphans",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"ledger_orphan",json.dumps([]),"LEDGER_NO_DOCUMENT","","0",json.dumps({"doc_id":doc},ensure_ascii=False)])
    return "﻿"+stream.getvalue()
