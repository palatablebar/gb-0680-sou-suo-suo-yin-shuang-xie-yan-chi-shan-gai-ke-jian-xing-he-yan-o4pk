# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

M8 `/api/retrieval` 在 parameters.migration_ledger 接收迁移台账（见DATA-DICTIONARY），输出每文档 retrieval_route（NEW_INDEX/LEGACY_FALLBACK/OUT_OF_SCOPE/SUPPRESSED）、searchable 与 retrieval_reason；metrics 含 ledger_fingerprint、ledger_scope_size、ledger_orphan_entries、legacy_index_mode=READONLY_SNAPSHOT。台账状态非法、版本混用或同文档登记冲突返回422。归档CSV新增 retrieval_route、searchable 列，已撤文档 kind=suppressed，无主台账登记 kind=ledger_orphan。
