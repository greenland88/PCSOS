# 第11A步：全池股票观察与恢复

第1–8步及第8步R1–R3接受结论保留，基线为
`2d5fe9b86deed36c475fabae4315c5e52af2af7a`。本次在
`codex/selection-v2-step-11a` 交付观察集成，等待限定复核；不合并main，
不进入9A、10A或11B，不执行交易。Docker不在任务范围内。

## 接口与执行边界

`pcs.pool.observation` 暴露 `run_stock_observation(StockObservationInput)`、
`resume_stock_observation(ObservationResumeInput)`、
`read_stock_observation(ObservationQuery)`，分别返回typed run或查询结果。
run/resume委托既有 `run_pcs_pool(scope='STOCK_OBSERVATION')`，在旧静态准入和
timing筛选前分流；默认 `PRODUCTION` 链路不变。CLI继续使用原process隔离和
PoolRuntime阶段调度。规范输入的纯计算由同一个runtime持有的有界spawn worker执行，
避免CPU任务被线程GIL串行化；数据reader和checkpoint不传入worker。worker只返回typed结果，
runner持锁提交不可变对象和checkpoint。runtime关闭时回收计算进程，Windows supervisor
硬截止时清理其自有进程树；旧生产scope不启用这些计算worker。

`selection-v2-observation-v1` 冻结四family、风险档案、支撑和排序政策。
[单票spec](../../../examples/stock_observation_spec.json)、
[spec schema](../../../examples/stock_observation_spec.schema.json)、
[profile schema](../../../examples/stock_observation_profile.schema.json) 为实际生成文件。
JSON spec为观察调用的参数权威；不要同时传旧scope的symbol、日期、预算或输出参数。
所有预算有限：核验120秒、准备180秒、组件120秒、输出180秒；示例全局7200秒。
正式2953票脚本全局21600秒，另给process启动/收尾30秒。初版MDLZ冷启动约21秒；
调整runtime纯计算执行后，同一份已保存verified输入四次准备（含spawn）9.64秒，
单次串行8.02秒，四次结果与串行完全一致，测量未读取canonical或调用供应商。
预算保留保守余量，不是无限等待。

规范运行从 `H:/workspace/PCSOS` 解析默认canonical manifest及配置，
`PYTHONPATH` 指向干净的11A源码工作树。只读调用PCSDataAccess及verified handle；
不调用供应商、补数、期权或模型。SPY单飞，共享日线及基础指标每个变更股票一次。
需要的整段指标重算明确记录；支撑与family使用已有公开continuation接口。
短历史可以保留有效风险指标/股票组件，各项UNKNOWN不冒充PASS。共享支撑失败只阻止依赖它的
健康/浅回调，平台/突破继续使用自己的固定区域组件；失败仍保留在run/逐票审计中。
新输入接入字段 `OpportunityInput.support_policy` 默认仍是原政策，仅传递声明值到
平台/突破公开适配器，不改检测算法、默认阈值或旧三通道政策。

## 身份、恢复和查询

外部 `objects/<sha256>.json` 不可变，checkpoint只存逐票状态引用。
提交核验attempt令牌、deadline及expected revision；写完对象但未推进checkpoint的
对象不算完成。恢复重新验证数据；已提交且依赖一致的组件不调用检测器。
`computed_at`、result ID保留，`served_at`、`served_attempt`记录本次服务。
按worker批次加载历史，单票查询先读索引，只核验该票对象链。

依赖闭包绑定相关trend/entry/data/models/config源码与有效数据配置，保守覆盖间接依赖。
排序政策变更仅重建下游；单family政策只失效对应组件。历史前缀比较实际内容与缺日，
不是max_date或行数。相同source/seed/policy下追加交易日使用prior state；历史修订或
共享口径变更冷重放并记录最早变更日。**兼容性限制**：接受的核心checkpoint把
generation/checksum写入source身份，新的generation即使旧内容相同也会拒绝旧state。
此时本层记录 `GENERATION_CHANGED_CORE_REQUIRES_REPLAY` 并合法冷启动，绝不改写
旧checkpoint制造兼容；这类跨generation更新尚不具备只执行新增状态转移的优化。

CURRENT_EOD请求必须带时区，由已有 `_resolve_requested_session` 解析最近已收盘
交易日；周末、假日保留周五行情，新请求时间仍重新进入资格适配。历史状态、请求资格、
确认期限和入场窗口分别保留。不得把HISTORICAL输出称为今天机会。

`observation_run.json` 分离执行/覆盖，互斥计数满足请求总数恒等式。
`selection_v2` 单列五组、family执行和状态、当前未知、组件命中/重算与实际引用查询数。
旧链路未运行指标保留null；options为NOT_REQUESTED、调用0。packet沿用第8步
NOT_PROVIDED/NOT_EVALUATED期权语义及NOT_REVIEWED/NOT_RECORDED默认。
当前请求未提供的benchmark/公司/账户资料保留独立缺口。

每20秒写终端和 `progress.json` 心跳，含阶段、执行分类、待处理数、缓存命中及checkpoint。
`stage_counts.json`、`read_audit.json` 保存实际阶段次数与来源核验。
中断可通过checkpoint读已提交证据，明确标记IN_PROGRESS，不冒充完整结果；process终止后
写 `interrupted_run.json`，只计入本attempt已服务终态。恢复仍使用同一spec及run ID。
`LATEST_ATTEMPT.json` 和观察scope的 `CURRENT.json` 分开；任何执行/覆盖PARTIAL
均不推进CURRENT。原生产输出目录及CURRENT不变。

## 实际操作

以下使用PowerShell，固定源码以验收记录的完整SHA为准。开始前核对干净工作树及
`git rev-parse HEAD`；不要切换运行中的源码。正式验收脚本同时记录ValidationRun、
HEAD/依赖、spec、原2953成员/hash、实际命令和结果。

```powershell
Set-Location H:/workspace/PCSOS
$env:PYTHONPATH='H:/workspace/PCSOS-selection-v2-step-11a/src'
python H:/workspace/PCSOS-selection-v2-step-11a/scripts/accept_stock_observation.py --full-universe --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v3 --previous-run H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v2/historical-20260904
```

同一逻辑全池因超时恢复，不另建全池run：

```powershell
python H:/workspace/PCSOS-selection-v2-step-11a/scripts/accept_stock_observation.py --full-universe --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v3 --previous-run H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v2/historical-20260904 --resume-run-id historical-20260904
```

普通CLI使用同一spec；无需再传重复的旧scope预算参数：

```powershell
python -m pcs.cli pool-scan --mode EOD --scope STOCK_OBSERVATION --selection-profile selection-v2-observation-v1 --observation-spec H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v3/actual_spec.json
```

创建CURRENT_EOD请求spec不读取价格；随后由同一个CLI执行（本轮不执行今日全池）：

```powershell
python H:/workspace/PCSOS-selection-v2-step-11a/examples/update_stock_observation.py H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v3/actual_spec.json --previous-run H:/workspace/PCSOS/selection_v2_outputs/step_11a_final_v3/historical-20260904 --output H:/workspace/PCSOS/selection_v2_outputs/next_current_spec.json
python -m pcs.cli pool-scan --mode EOD --scope STOCK_OBSERVATION --observation-spec H:/workspace/PCSOS/selection_v2_outputs/next_current_spec.json
```

单票/小批量Python示例为 `examples/stock_observation.py SPEC --query-symbol MDLZ`；
`--resume-run-id` 使用typed恢复API。被拒绝票AAL、历史缺数票UBER的保存证据查询：

```powershell
python -m pcs.pool.observation_cli --run-directory H:/workspace/PCSOS/selection_v2_outputs/step_11a_saved_v3/saved-eight --symbol AAL
python -m pcs.pool.observation_cli --run-directory H:/workspace/PCSOS/selection_v2_outputs/step_11a_saved_v3/saved-eight --symbol UBER
```

查询返回packet后，复制真实排序键source_refs、condition/test/component ID，通过同命令
`--evidence-id <实际ID>` 查询；不在范围返回NOT_IN_INPUT_SCOPE。恢复八票只装配原保存
资料的命令为 `scripts/accept_stock_observation.py --saved-anchor --output-directory
H:/workspace/PCSOS/selection_v2_outputs/step_11a_saved`，不重新检测原七票。

只渲染保存结果（不调用canonical/指标/检测器）：

```powershell
python -m pcs.pool.observation_cli --run-directory H:/workspace/PCSOS/selection_v2_outputs/step_11a_saved_v3/saved-eight --render-directory H:/workspace/PCSOS/selection_v2_outputs/step_11a_rendered_v3
```

回退方式：停止新观察调用，原调用保持默认PRODUCTION，或显式 `--scope PRODUCTION`；
不要附带observation-spec。旧生产读取/计数/策略不使用观察产物。

## 限定验证映射与交接

| 风险 | 最小验证 |
|---|---|
| T1、T14 旧门槛/默认被改 | TEST旧worker禁止调用；选取原process有界检查；新packet默认未讨论/未决定 |
| T2、T4 重复读取/计算 | 四family共享一次准备；相同请求恢复各组件调用0；CLI传入profile/spec完全一致 |
| T3 局部缺数 | 准备失败仍保留独立PROFILE；缺行情股票仍有packet及五类位置 |
| T5、T6 时间/追加 | 真实XNYS周末假日解析；新请求不复用旧资格结果；短TEST冷算/续算ID和首次确认一致 |
| T7、T8 修订/依赖 | 内容修订最早日、新generation显式重放；排序/单family政策分别失效 |
| T9、T10 原子/晚回 | 对象先落盘模拟崩溃；旧attempt/revision/deadline提交拒绝 |
| T11 部分续跑 | 一family失败后只补该family；已完成另一family不重复调用 |
| T12 计数/发布 | 完成+缺数互斥总数；PARTIAL无CURRENT；中断只计本attempt |
| T13 引用 | 从实际名单sort_refs查询，组件ID解析；索引篡改拒绝；中断checkpoint可查询 |
| T15 变化 | 范围移除不是失效；修订确认不是今日NEW_CONFIRMATION；旧新result及日期/lineage保留 |

正式结果以外部 `acceptance.json`、`validation_run.json`、`observation_manifest.json`
为准，不以本文预设通过。测试为 `tests/pool/test_stock_observation.py` 和受影响process检查，
不重跑70项或第1–8步全库。旧process的4秒重验证启动测试在接受HEAD也发生同样超时，
单独保留失败记录，未改旧策略以迎合机器启动速度。

初版 `dd27b34267f53d297b6066c8e60790079651bebe` 保存八票对账通过；全池首次尝试在
发现共享支撑失败会误挡独立family的接线风险后主动停止，已有7完成、1数据阻断、2945未处理。
旧根 `step_11a_final` 及checkpoint保留并标记SUPERSEDED_PARTIAL，未发布CURRENT。
修复版从同一冻结2953成员/日期继续，输出根 `step_11a_final_v2`，`previous_run` 指向旧根的
`historical-20260904`；较宽源码依赖变化使受影响组件合法重放，旧对象不改写。
保存八票修复版输出根为 `step_11a_saved_v2`。首次修复版全池命令是在上述命令基础上
使用新输出根，并加 `--previous-run H:/workspace/PCSOS/selection_v2_outputs/step_11a_final/historical-20260904`；
恢复时保持这项参数与actual_spec一致。没有重新选择股票或使用原38票替代全池。

随后在 `11fb3c68e98869b1b524c2b0ccb48cbb858a733d` 检查到跨代码依赖变化时仍可能
传递旧prior_state的风险，停止时14完成、6数据阻断、2933未处理。该根同样保留并标为
SUPERSEDED_PARTIAL。最终修复同时核验结果缓存和状态延续的依赖身份，包括先完成新输入、
中断、再恢复旧family的路径；相关代码/config变化输出CODE_DEPENDENCY_CHANGED_REPLAY。
最终操作以上方命令为准：全池根 `step_11a_final_v3`，前序为v2根；保存八票根
`step_11a_saved_v3`。新增20项集成测试通过，之前34项runtime/process/集成组合通过；
未因这次局部恢复修复重跑无关旧检测器测试。

保存八票验收核对接受的shortlist/八packet IDs；原7步包只取旧三family，R1包只取平台。
正式全池仅使用原冻结 `included_symbols` 2953只（不是原38只），日期2026-09-04。
最终交接分别报告代码/TEST、保存八票、全池尝试及覆盖、CURRENT、实时数据与收益验证。
实时今日、真实AI、期权及策略收益不在本次验收结论内。停止11A等待限定复核。
