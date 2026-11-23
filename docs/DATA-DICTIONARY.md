# 双写定义

event_id唯一，doc_id文本保留。side为source或index；版本正整数按Decimal比较并作为文本序列化。upsert要求JSON对象，delete忽略payload并产生墓碑。相同文档同端同版本不同内容全部隔离，存在未裁决冲突的主体不回落旧版本；同内容合并来源；较高版本覆盖低版本，迟到旧写不得覆盖墓碑。

双端最终版本、操作、文档分别核验。目标缺失、删除未确认、索引领先、陈旧索引、内容差异分开展示。source_time与visible_time带偏移；可见延迟按实际秒数。默认阈值120秒，等于阈值通过。已可见但超过阈值为延迟，尚无目标且等待时间超过阈值为缺失，等待中不冒充延迟。

对账expected=每文档应通过的1，reported=门禁通过1或未通过0，逐条保存visibility_state和gate原因，允许按文档复核。

业务ID和超大整数按文本保形；Decimal参与数量金额计算，对外序列化为字符串。哨兵集合定义在app/values.py，缺失值不能当零。所有时间带UTC偏移，先转UTC比较，业务日按指定地区划分。区间统一[start,end)。重复业务键保留第一条、完全相同行计重复；值冲突行隔离且保留原值。空文件、全空白、仅表头分开拒绝。

运行清单包括输入sha256、规则版本、参数、生成时刻与耗时。源行编号以CSV解析器的物理结束行号表示，引号内换行不增加记录数。输入超过4MB返回413，非法表头和类型返回422，失效版本复核返回409。

## M8 迁移台账参数（parameters.migration_ledger）

台账以JSON对象数组随 `POST /api/runs` 的 parameters 提交，列固定为 doc_id、rebuild_state、scope_epoch、ledger_version。rebuild_state 仅允许 REBUILT（新索引已重建，只查新索引）、PENDING（尚未重建，可见文档受限回落只读旧索引）、OUT_OF_SCOPE（不在本次迁移范围）。

裁决只认源端最终状态：源端 delete 墓碑一律 SUPPRESSED、searchable=0，即使台账为 PENDING 或旧索引仍有残留也不回落显示；可见文档 REBUILT 走 NEW_INDEX，PENDING 走 LEGACY_FALLBACK 保留命中，无台账登记按 OUT_OF_SCOPE 处理但不消失。同一批次 ledger_version 必须一致，同文档冲突登记返回422。台账规范化后计算 sha256 指纹（与顺序无关），指纹随 run_id 的输入+参数摘要冻结：重跑同一迁移复用同一批次，范围一旦增改即产生不同 run_id，不能新增或改换处理范围。台账声明在范围内却无流水线文档的登记，作为 ledger_orphan 写入归档凭据。旧索引在迁移窗口固定为 READONLY_SNAPSHOT，不删除不覆盖。归档导出新增 retrieval_route、searchable 列，与检索页面共用同一裁决行。