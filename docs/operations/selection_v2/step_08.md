# 第8步：观察名单与AI讨论证据包

状态：实现交付，等待第8步限定复核；不合并main，不进入第9步。
已接受基线为 `896f2249cb8b88dc2abc7724090c1d9e5710c6c6`，
独立工作树 `H:/workspace/PCSOS-selection-v2-step-08`，分支 `codex/selection-v2-step-08`。
当前交付源码和远端完整HEAD以提交记录及正式产物manifest为准。

## 2026-09-09 R1–R3限定修复

复核基线 `3ba80901691a11de67ac0c3b669d464bb7e3d064` 暂不通过。
本轮仅修下列三项，保留第1–7步和平台R1接受结论，仍等待第8步限定复核。

- R1：意见文件名改为完整序列化AIReview的SHA-256，包含review_id；内容hash仍用于
  同ID幂等/冲突校验。文件以独占创建方式追加，已存在不同字节拒绝写入。意见manifest
  升为1.1；有效旧日志可读且不迁移旧文件。已损坏日志仍拒绝读取，不伪造丢失意见。
- R2：查询支持证据hash、实体ID、`result_id:json_pointer`，以及条件的
  `result_id:日期:condition_id`。同别名不同内容明确AMBIGUOUS_EVIDENCE_ID；可用精确
  pointer或hash消歧。当前条件直接保存于/current_conditions；第3步支撑测试逐条索引。
  组件ID解析到简洁COMPONENT定位记录。流动性排序引用/diagnostics中的已保存诊断，
  原上游source_refs仍在诊断内容内，不再冒充可直接解析的排序引用。
- R3：消费保存的确认期限/入场窗口届满标记，true优先于WATCH/CONFIRMING或入场等待，
  当前归为NOT_CURRENTLY_APPLICABLE并保存具体原因；原状态和原资格不改写。
  另一通道仍合法时照常作为当前代表通道。

名单/证据包schema均为1.1；计算版本分别为stock-observation-ranking-v2和
decision-evidence-packet-v2。固定排序政策ID和排序方向未改。v1输出不得typed复用为v2，
仅可在hash核验后作为原始JSON历史对比；旧输出原样保留，本轮以新的独立目录交付。
AIReview的意见内容语义仍为ai-review-record-v1，仅存储manifest升级。

必要重导出命令（干净提交后执行）：

```powershell
python scripts/accept_stock_shortlist.py H:/workspace/PCSOS/selection_v2_outputs/step_08_r1_r3_20260909 --compare-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909
```

验收从实际名单每个排序键的source_refs及component_refs逐一解析；另保留全部detail_index
校验、四视图对账和旧21个子ID检查。acceptance.json的revision_comparison记录新旧版本、
名单/行/packet身份、分组和排序数值差异，并校验旧产物字节未变。专项TEST覆盖不同意见ID
相同内容、旧文件不变、幂等/冲突、真实输出引用、仅当前条件、冲突消歧、期限过期及另一
通道仍可用。未重跑检测器、canonical、供应商或全池。

## 独立公开接口

从 `pcs.selection` 导入以下函数，均消费typed输入：

```python
rank_stock_opportunities(input: RankingInput) -> StockShortlist
build_decision_evidence_packet(input: DecisionPacketInput) -> DecisionEvidencePacket
resolve_evidence(input: EvidenceQuery) -> EvidenceQueryResult
record_ai_review(input: AIReviewInput) -> AIReview
```

讨论、查询和意见记录同时由 `pcs.pool.ai_evidence` 导出，旧解释函数和旧CLI路径保留。
核心没有扫描、形态检测、指标计算、canonical读取、供应商或模型调用。
`DecisionPacketInput`可以独立构建单票讨论包，无需StockShortlist；被拒绝股票同样可查。
批量使用BatchContext与明确requested_symbols，不伪造一只symbol代表股票池。

保存适配位于 `pcs.selection.adapters`，原子保存、回读、只渲染及独立review日志位于
`pcs.selection.storage`。独立示例为 [stock_shortlist.py](../../../examples/stock_shortlist.py)。

## 输入范围、兼容与身份

[实际输入清单](../../../examples/selection_step08_inputs.json)明确指定8票、2026-09-04 HISTORICAL、
启用family及各保存包。每个包先验证其manifest和登记文件hash，单次装配每包读取一次。

| 包 | 消费范围 |
|---|---|
| step_07_acceptance_20260908，源码a1282a8d83dc54b955c8241cf963aa5e01284ea1 | 仅21个旧三通道子结果；跳过旧平台v1后再typed加载 |
| step_07_r1_20260909，源码896f2249cb8b88dc2abc7724090c1d9e5710c6c6 | 7个constructive-base-v2平台结果 |
| step_02_acceptance_20260904_bounded | 已保存档案及实际dollar_volume_median_20；按来源的币种、价格口径、日期验证 |
| step_01_acceptance_45c17d5_58363ffc66a246c185f429f3d878b3cc | 旧解释、实际动作、趋势/timing/期权执行及规则引用 |
| step_03_review_e2d5531_20260904 | 已接受support-zones-v2支撑与测试事实 |

后3个组件可选，不补数据。冻结清单记录所选子ID、原schema/计算版本、包hash、源提交、
输入feature身份、日线来源身份及适配器 `saved-selection-adapter-v1`。
同票同family不同结果必须显式选择 `symbol|session|family -> result_id`；否则输出
AMBIGUOUS_FAMILY_RESULT及候选ID，保留“执行过但未选择”，不误称未执行。
相同ID内容冲突、日期错配及同一共享必需证据来源/值冲突仅阻断对应股票。
同票不同family的政策判断分歧保留，不升级为共享数据冲突。

支撑live测试使用精确绑定zone_id、同日线来源hash/generation、价格与公司行动口径、
可知日期核对。第3步和机会包装器的indicator_identity不同，不是这里所消费的live测试
事实的依赖；不能据此丢弃同一已验证zone，也不能借另一个zone的测试。

UBER没有机会结果仍有一行，保留原INSUFFICIENT_FEATURE_WARMUP，未重试。
历史日期不改称今天；请求新日期不会运行检测器推进状态，而是保存时间错配。
行情data_timestamp保留实际输入日期，原状态、原当前资格和排序适用性分列。

## 固定观察政策

政策为 `stock-observation-ranking-v1`，完整group顺序、方向、空值规则及hash落盘。
分组依次为READY_FOR_OPTIONS_REVIEW、WATCH_SETUP、NOT_CURRENTLY_APPLICABLE、
NO_SETUP、INSUFFICIENT_EVIDENCE。先处理共享冲突；明确true优先于可观察，再处理未知。
可用/观察通道不被另一通道缺项抹掉；全false加必需未知不称无形态。
ENTRY_READY且当前false不进第一组；确认当天等待入场窗口可以观察。

主机会按当前适用层级、当前必需证据完整性、确认日期新旧、稳定机会/结果ID选择。
所有候选键和原结果引用保留；不重新挑选历史episode，不按family名称偏爱某通道。
同组按当前必需完整性、绑定live测试类别、保存确认条件true/total、剩余交易session、
兼容日线成交额中位数、symbol依次排序。只有DIAGNOSTIC的缺项不进入确认分母或当前必需门槛。
没有确认分母为null；没有兼容指标为null；未知值排序靠后，不能默认为零风险。
未来窗口保存等待session数；剩余数为max(请求日, entry_start)到entry_end，含两端。
调用本地XNYS日历计算周末/假日，不使用日历天数。
平台回溯HELD单列为形成事实，不能代替live；多次HELD必须有已知的独立离开/再次触及。

旧规则比较只用同一评估时点明确保存的timing布尔资格；WAIT和缺期权不能推导股票false。
时间不一致或旧布尔字段未记录时为INCOMPLETE_EVIDENCE，旧动作和执行字段仍保留。
两份名单比较单列scope移除、family范围、来源不可用、policy变化、确认/失效/过期/
实际超距条件及仅排名变化，不从排名下降推断策略转弱。没有前序为NOT_COMPARED。

排序ID排除路径、run/request/received_at、物理manifest checksum；保留实际源结果、
依赖身份、排序政策、请求范围和输出判断。独立packet链接不作为排序键或排序身份输入，
但保存manifest及回读校验packet与每行输入/时点的链接。单票排名是1，批量排名依赖范围；
同票row_id、主机会与排序键可对账。可附带rank的packet必须明确shortlist_id并核对行身份。

## 讨论证据与意见

packet保存正反证据、程序意见、当前/历史缺项、约束/期限、规则分歧、讨论问题及可知时间。
条件谓词原值原样保留；已知“触发失效”的三类旧predicate以反向极性归类正反依据，
不把未触发失效的false误说成反对证据，不改变其程序资格。
公司实体/行业/地位与基本面事实、市场和账户是typed可选槽；默认公司质量和行业地位尚未评估。
期权报价、discovery、正式评估分列NOT_PROVIDED/NOT_EVALUATED，不写“没有合适价差”。
25–45 DTE仅保存为用户后续偏好，不改生产preferred30–40、hard30–45或其他交易规则。

EvidenceRef指向可搬移的NORMALIZED_ATTACHMENT，保留源result_id、实体/条件ID、
源定位、schema、证据日期、规范记录hash和保存包别名。附件为投影后的规范证据，
不是原始行情或checkpoint复制。resolve_evidence实际验证packet/记录身份并返回记录；
找不到返回NOT_FOUND，同名实体跨日期含不同内容返回UNRESOLVED，不擅选另一天。
支持一次query的evidence_ids批量解析，避免每个引用重新校验整包。

review由typed外部JSON导入；支持EXTERNAL_AI、MANUAL、TEST，必须有真实生成时间及时区。
验证packet身份和实际引用，重复导入幂等，同ID不同内容拒绝，追加不覆盖。
review日志独立于不可变packet/名单；packet的原始NOT_REVIEWED状态不被回写，当前意见按日志查询。
新packet不继承旧review，用户决定始终NOT_RECORDED。没有内置模型服务，也没有交易授权入口。

## 运行与有界验收

开发专项为62 passed（35项第8步具体风险及27项旧selection explanation兼容）；
最终提交态仍运行同一组，结果以独立检查记录为准。没有重跑全库、四类检测或历史研究。

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/selection/test_observation.py tests/pool/test_selection_explanation.py -q -p no:cacheprovider
python scripts/accept_stock_shortlist.py H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909
```

验收脚本要求已提交且干净工作树，使用ValidationRun绑定HEAD和依赖hash。
五类实际调用通过真实CLI main/示例main执行并保存argv；为避免反复读源，传递同一份
已验证typed输入缓存。独立运行下面同样参数时，适配器自行验证源包。正式数据/指标/
形态/扫描入口被测试守卫设置为一旦到达即失败，不改其策略或返回值。

```powershell
python -m pcs.cli stock-shortlist --input-manifest examples/selection_step08_inputs.json --as-of 2026-09-04 --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909 --summary
python examples/stock_shortlist.py --input-manifest examples/selection_step08_inputs.json --symbol NVDA --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909_single_nvda
python -m pcs.cli pool-evidence --selection-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909 --symbol AAL --discussion-packet --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909_aal_packet
python -m pcs.cli stock-shortlist --selection-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909 --render-only --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909_render
python -m pcs.cli pool-evidence --selection-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909 --symbol AAL --import-review H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909_TEST_review_input.json --review-directory H:/workspace/PCSOS/selection_v2_outputs/step_08_acceptance_20260909_TEST_reviews
```

查询具体ID使用 `--evidence-id`，查询已导入意见使用 `--review-id` 和相同review目录。
实际生成的完整ID、重复导入/读取命令和stdout记录于 `actual_commands.json`，不预填ID。
终端输出摘要，完整JSON和CSV/中文/AI落盘；旧pool-evidence --run-directory仍独立可用。
输出已存在时拒绝覆盖，请为新的调用使用新的目录。

正式验收的源hash前后、21个旧子ID、逐票全部排序键、主原因、引用解析数、AAL例子、
TEST分歧及四视图字节对账见 `acceptance.json`、`actual_commands.json`、
`validation_run.json`及`acceptance_manifest.json`。主结果与packet由artifact_manifest绑定。
提交前开发输出不是正式证据；语义改变后的旧开发包标为INVALID并保留。
最终只交付股票观察，未验证收益、未覆盖全市场、未接实时交易或真实AI调用。
