# 固定 universe 的 canonical 日线恢复

## 最终结果：部分完成

固定 2953 只全部完成本地检查和最终 verified 读取尝试；2009/2953（68.03%）通过日线验收。共有 375 只尚未进行来源查询，故不能报告全量恢复完成。

| 对账项 | 数量 |
|---|---:|
| 原来自身日线 READY 且仍 READY | 258 |
| 新增 READY | 1751 |
| 从 READY 变为非 READY | 0 |
| 有权威生命周期证据的不适用/新上市不足 | 0 |
| 已检查仍阻塞：版本冲突 | 450 |
| 已检查仍阻塞：来源问题 | 119 |
| 尚未查询来源（本地已检查） | 375 |
| 原始分母 | 2953 |

新增 READY 中，1690 只来自 A 类窗口准入后补齐，61 只来自 B 类增量/内部缺口恢复。没有缩小 universe；QQQ 和 SPY 最终均 READY。初始自身日线覆盖 258/2953（8.74%），但当时 QQQ benchmark 未就绪，因此该初始数字不代表完整扫描就绪。

119 只来源阻塞由 74 只实际确认零行、13 只导入后仍缺所需覆盖、32 只 HTTP 429 组成。零行不证明退市；AZUL、OST 的可观察历史不足也没有被擅自解释成新上市。429 的具体配额及解除时间未确认。最后 372 只可独立 admission 的数据已准入；375 只未查询队列含这些项及历史缺失项。

本轮共记录 2152 次 admission 动作、1995 次 control-plane loader 调用；后者含复用和失败，不能当成成功 ticker 数。来源回执有 1872 个 IMPORTED、74 个零行、4 个仅复用未查询、13 个导入后未满足 canonical 覆盖、32 个限流。最终 READY 一律以独立 verified read 加冻结交易日覆盖检查为准。

- [逐 ticker 最终状态、身份、动作和回执索引](daily_coverage_20260905/per_ticker.csv)
- [最终数量及分类](daily_coverage_20260905/final_summary.json)
- [原始冻结请求](daily_coverage_20260905/request.json)、[运行身份及审计哈希](daily_coverage_20260905/audit_manifest.json)
- [来源查询结果与精确日期需求](daily_coverage_20260905/source_checks.json)
- [450 只冲突的双方文件、哈希及差异范围](daily_coverage_20260905/conflicts.csv)
- [未完成恢复队列](daily_coverage_20260905/resume_queue.json)、[共享限流证据](daily_coverage_20260905/source_backoff.json)
- [真实 ABBV 准入/加载/promotion/最终 verified 身份](daily_coverage_20260905/ABBV_checkpoint.json)、[QQQ 最终身份](daily_coverage_20260905/QQQ_checkpoint.json)

完整逐 ticker 回执及缺失 session 诊断保留在 `H:/workspace/PCSOS/pool_scan_runs/daily_coverage_recovery_20260905_1741`。附件只提交审计元数据，不提交原始行情或配置密钥。`checkpoint_hashes.json` 可核对原始回执；`audit_manifest.json` 可核对提交的附件。

## 范围和身份

- 原始 universe：2953；`global_pcs_candidates:c2ede1f139e783e3-fd5a6552aff369df`。
- 冻结时间：2026-09-05 17:43:04 UTC；目标 session：2026-09-04。
- 暖机：200 个 XNYS session，2025-11-18 至 2026-09-04。现有准备缓冲起点为 2025-07-11；只准入相交的 2025/2026 年分区，下载仅针对缺失 session。
- 数据工作目录：`H:/workspace/PCSOS`；代码：`H:/workspace/PCSOS-pool-closure`，分支 `codex/pool-scan-closure`。
- 初始 HEAD：`9a2d5fa`；维修提交：`78ea561`、`4e05666`、`837a3f6`、`cc3a660`。主工作区 main 未切换或合并。
- 授权配置：原工作区 `.env`，由 `PCS_ENV_FILE` 指定；加载和子进程继承已核实，密钥不进入报告。

## 根因和修复

1. 控制面以日期端点判断日线覆盖，QQQ 缺少 2026-09-03 时仍规划 `REUSE_CANONICAL`。现在检查请求范围内实际 pinned generation 的交易日。
2. 日线增量更新曾自动进入研究/期权 readiness。日线入口和单日修复入口现在跳过这一额外阶段，保留派生数据失效记录。
3. 增量合并曾按日期保留最后一行，可能覆盖不同来源的重叠值。现在遇到不同 OHLCV 明确拒绝 `DAILY_SOURCE_OVERLAP_CONFLICT`，原 active generation 不变。
4. admission 比较未统一 route 编码的 symbol 与显式 symbol 列。现在先通过 pinned read 校验 active 对象，再作同口径比较；不因冗余 symbol 列而重新 promotion。
5. 恢复脚本按本次年份判断 active 状态，并优先准入有效物理分区；来源重试耗尽不阻止独立 admission。脚本只编排现有 admission/control plane/loader，没有 Scanner、策略引擎或数据注册表。

6. 物理数据仅暖机不足时，编排仍请求缺失来源历史；质量/内容冲突仍停止。定向恢复参数不会改变冻结分母，只有明确记录来源变化后才允许新的有界重试。

7. 批次后段网关返回 HTTP 429，已在 ticker 边界暂停，随后仅继续本地 admission。新增共享限流 checkpoint：普通恢复调用在新的网络请求前停止；这不同于确认零行。32 只实际收到 429，其余未查询者单列为未完成，不能推断其来源内容。

## 批次内纠正

脚本最初把 54 只仅在旧年份存在 active 的 ticker 归入“当前窗口已准入”。其中已处理的 16 只产生了本可避免的历史重叠查询和差异，发现后在 ticker 边界暂停。

已用保存的、哈希未变且经过原 admission validator 验证的物理数据，通过现有 `adopt_legacy_canonical_generation` 恢复历史值；用 `repair_daily_session` 复用保留的真实 canonical generation 中的新日期。没有重新下载这些日期，没有删除旧对象，也没有改写 manifest 来制造成功。每个纠正分区校验原历史值完全相等、没有丢失已入库 session，纠正回执保留在对应 ticker checkpoint。

这项纠正不裁决任务开始前已经存在的多版本冲突；那些对象及具体冲突日期保持可审计。

## 历史与当前验收的区别

历史 263 只的原始 run manifest 和全部列出的 artifact 哈希验证通过，只作为历史对照。本次初始自身日线覆盖为 258：ATXG、AZUL、BGLC、IFBD、QQQ 存在内部 session 缺口。目标交易日未变，差异不是跨日 freshness 变化。QQQ 初始缺口还影响 benchmark 就绪；不能把自身日线就绪数量混同于带 benchmark 的最终覆盖。

最终数量、逐 ticker 身份、动作、来源回执和验收结果以本目录的最终审计附件为准。没有进行策略全池扫描、期权导入、账户评估或交易动作；不输出交易级 READY。

## Agent 恢复入口

工作目录和环境沿用上面的固定配置；由 agent 执行，用户无需手工拼接模块：

```powershell
$env:PCS_ENV_FILE='H:/workspace/PCSOS/.env'
$env:PYTHONPATH='H:/workspace/PCSOS-pool-closure/src'
C:/Python313/python.exe H:/workspace/PCSOS-pool-closure/scripts/pcs_daily_coverage_recovery.py --output H:/workspace/PCSOS/pool_scan_runs/daily_coverage_recovery_20260905_1741 --phase recover
```

入口保留冻结 request，跳过有效完成项；来源确认为零行或已经尝试失败时不紧密重试。来源条件改变后须先核实对应回执，不能将保存了恢复条件描述成后台监控。

确认具体来源条件已经改变后，agent 可在同一入口使用 `--symbols TICKER --retry-source --retry-reason "已核实的来源变化及证据路径"` 定向重试一次。旧回执不会删除，原始 2953 分母不变。该开关不在本轮自动启用，不把经过一段时间等同于来源已恢复。

`PAUSE` 文件只在 ticker 边界生效，已开始的 promotion 正常结束。最终验收使用同一入口的 `--phase verify`，不运行 Pool Scan 或期权准备。

## 针对性验证

以下均在代码 worktree 使用 `C:/Python313/python.exe -m pytest` 执行；没有重跑全套测试。

- `tests/data/test_incremental_update.py tests/data/test_control_plane.py -q -k 'daily_overlap or new_daily_date or massive_daily_handler or daily_safety_repair'`：4 passed，32 deselected。
- `tests/pool/test_preparation_orchestration.py -q -k 'route_encoded_symbol or valid_legacy_daily or partial_admission_retry or active_generation_missing_path'`：4 passed，25 deselected。
- `tests/data/test_daily_recovery_operator.py -q`：当时 5 passed（来源结果判别、不重复查询、benchmark 身份失效、旧年份 active 不阻碍窗口 admission、明确来源变化后的单次重试）。
- 后续仅对新增暖机恢复分支运行 `tests/data/test_daily_recovery_operator.py -q -k insufficient_physical_warmup`：1 passed，5 deselected。
- 内部缺口专项 `tests/data/test_control_plane.py -q -k 'daily_interior_session or massive_daily_handler or daily_safety_repair'`：3 passed。

这些是代码/fixture 验证；真实 canonical verified read-back 及逐 ticker 收据另列，不把测试通过当成生产数据 READY。

- 限流专项 `tests/data/test_daily_recovery_operator.py -q -k rate_limit`：1 passed，6 deselected。真实再次调用返回 `SOURCE_RATE_LIMIT_DEFERRED`，没有发起新的 provider 请求；本地 admission 仍可独立执行。


本轮实际依次使用同一脚本的 `--phase inspect`、代表性恢复、`--phase recover`、冲突证据核实及 `--phase admit`。429 暂停后仅继续 `--phase admit`；最后只运行一次完整 `--phase verify`。完整路径、冻结请求和各次代码/配置哈希见审计附件的 `request.json` 与 `invocations.jsonl`。

未完成恢复队列保存在 `resume_queue.json`。用户只需说“继续恢复日线”：agent 先有界核实原授权网关限流是否解除，再读取该队列，将未查询及 429 的 ticker 传给现有 `--symbols ... --retry-source --retry-reason` 入口。没有新证据时保持 checkpoint，不重复查询已确认零行的来源。冲突项须有可审计的来源权威/复权身份裁决，不能用重试开关绕过。
