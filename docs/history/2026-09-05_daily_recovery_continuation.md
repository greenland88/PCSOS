# 日线恢复续轮与只读策略扫描

## 本轮增量

冻结 universe 2953、目标 session 2026-09-04、200 个 XNYS 交易日暖机不变；沿用原 checkpoint。程序版本 `c473f77`，`src` 和策略配置没有修改。本轮没有重跑代码测试，没有读取 FINAL OOS、期权导入、账户操作或 main 合并。

退避 1486.85 秒后，现有 `MassiveCompatibleClient.probe_daily_coverage` 对 TLT 的 2026-08-14 返回 1 行；随后通过原 `pcs_daily_coverage_recovery.py --phase recover --symbols ... --retry-source --retry-reason ...` 恢复冻结的 407 只队列。沿用原 `.env`，未复制密钥。网关恢复证据只支持本次有界尝试，不代表所有日期可用。

- TLPH：补 2026-08-20 至 2026-09-04；正式 verified 日线读取通过。
- TLRY：补 2026-09-02 至 2026-09-04；正式 verified 日线读取通过。
- TLT：补入 2026-08-14，并通过独立 pinned read 确认；随后请求 2026-09-02 至 2026-09-04 再次 429，入口保存 checkpoint 后停止。
- OST：首次请求 2025-11-18 至 2026-09-04，授权来源确认零行；没有推断退市。

407 只队列中实际尝试 4 只、5 次 loader 调用。新增完整就绪 2 只、首次确认零行 1 只，剩余 404 只（30 只曾限流、374 只尚未查询）。没有第二轮自动重试。

本轮结束后只运行一次完整 `--phase verify`：**2011/2953 日线就绪，上轮 2009，新增 2，无回退**；QQQ、SPY 仍就绪。其余 942 只：450 冲突、75 来源零行、13 来源覆盖不足、30 限流、374 尚未查询。全部保留在原分母。

## 只读扫描最终结果

初次请求 2011 只：900 秒计算阶段到期，148 只未开始、8 只 worker 超时；保留其余 1855 条结果，仅对这 156 只作局部续跑。续跑全部返回，没有重跑已完成的 1855 只，也没有第二次全池日线验收。两段原始 run 分别为 `ffd2aef342a44bd8ae7c581760e3af03`（PARTIAL_TIMEOUT）与 `6f7fdaaadca14d2f82acb966b9a19142`（COMPLETED）。合并 CSV 保留每条真实 run_id，不伪造成一个引擎 run。

| 结果 | 数量 |
|---|---:|
| 原始 universe | 2953 |
| 日线阻塞、未送入策略 | 942 |
| 日线就绪、策略扫描请求 | 2011 |
| 实际完成有效 timing 评估 | 2008 |
| 策略拒绝 | 1030 |
| WATCH | 408 |
| WAIT（有有效 timing 证据） | 532 |
| timing 通过 | 38 |
| timing 证据不可用 | 3 |
| 尚未恢复的超时 | 0 |
| 成功期权评估 | 0 / 38 次候选检查 |

38 只均停在 timing 通过之后，期权为 `DATA_BLOCKED / OPTIONS_GENERATION_MISSING`；没有合约选择、事件或组合批准。外部期权来源本轮未查询，不能据此断言来源无数据或“没有机会”。两段扫描的 provider、promotion、recovery 调用均为 0，canonical manifest 前后哈希完全相同，账户/事件状态全部 NOT_EVALUATED，PCS_TRADE_READY 为 0。

候选：AUPH、BCTX、BKKT、BNGO、BORR、CING、CLOU、COUR、CWK、EBON、GSHD、HAE、HCA、HELE、IBRX、IDYA、ILMN、IRTC、KPRX、MAT、MDLZ、MEDP、MGY、MJ、NBR、NGVT、OTEX、PHIO、PLG、QURE、RVMD、RXST、SDGR、SGML、SSRM、STRA、TASK、TLRY。

3 只不可用项的具体证据：

- FISV：正式读取到 205 行，符合冻结 200 日标准；`analyze_ma_structure` 的 SMA200 40 日斜率还需要额外计算缓冲。缺 2025-09-23 至 2025-11-10 的 35 个交易日。200 日验收口径不变，不把该项计为已完成 timing；本轮限流后不再下载。
- TBIL：420 行有效日线；既有引擎仅确认 3 个高点、1 个低点。
- TFLO：420 行有效日线；既有引擎仅确认 1 个高点、1 个低点。市场结构要求至少两个确认高点及两个确认低点，因此两者保持证据不可用，不改参数、不推断生命周期。

初次 readiness audit 为 674.18 秒，计算阶段达到 900.04 秒；局部续跑用 127.10 秒完成。记录实际耗时，不以性能猜测改造引擎。本轮未发现需要修改生产代码才能安全裁决的数据问题，未运行重复测试；验证采用真实 source/promotion/verified read、两段只读 manifest/counter 检查及 2953 行对账。

### 交付附件

- [2953 只逐 ticker 数据与策略阶段结果](daily_continuation_20260905/per_ticker_final.csv)
- [扫描统计和两段运行身份](daily_continuation_20260905/scan_summary.json)
- [38 只 timing 候选及精确期权需求](daily_continuation_20260905/timing_candidates.json)
- [覆盖增量及恢复回读](daily_continuation_20260905/coverage_delta.json)、[真实回执与 verified 身份](daily_continuation_20260905/recovery_readback.json)
- [450 只冲突分组](daily_continuation_20260905/conflict_groups.json)、[分组统计](daily_continuation_20260905/conflict_group_summary.json)
- [87 只既有来源问题复核](daily_continuation_20260905/source_recheck.json)
- [剩余恢复队列及分阶段输入需求](daily_continuation_20260905/remaining_queue.json)
- [审计哈希](daily_continuation_20260905/audit_manifest.json)

## 冲突分组及裁决边界

450 只的原物理文件哈希全部未变化，通过既有 migration validator 做本地比较。按 ticker 分组：417 只 OHLC 与成交量均有差异；12 只仅 OHLC；12 只同时存在前两种配对；6 只仅成交量；2 只存在 OHLC/成交量分别不同的配对；1 只为 OHLC+成交量与仅成交量的混合。

共 900 个文件有 QFQ 迁移路径关联，519 个候选没有精确来源绑定。检查现有 provenance、请求 ledger 和 canonical catalog 后，没有可补足这些冲突身份的对应日线记录。目录中的生成对象不被用作未经验证的读取路线。

真实价格差异的最大绝对值至少为 0.001（同时有成交量差异组）或 0.005（仅价格组），没有全部差异仅在 1e-6 以下的配对；也没有统一缩放模式。47 对只有相同重叠区间/覆盖差异，但所属 ticker 仍有其他真实冲突，不能据此宣布整个 ticker 可准入。

`canonical_adjusted` / `canonical_identity` 是现有 validator 的声明值，不是供应商复权版本证据。注册表的来源优先级也不能为未知文件补造来源。现有 `_reconcile_migrated_candidates` 仅能复用相同内容或相同重叠数据的完整覆盖；本轮没有满足完整裁决条件的 ticker。

共同阻断点是同一逻辑年份留下了内容不同的候选，却没有可绑定这些内容身份的来源/版本裁决记录；这不是已经证实的拆股或退市原因。逐 ticker 缺失证据保留为：候选物理哈希对应的原始生产器及版本、split/dividend 复权因子版本与生效日期、能绑定冲突双方身份的权威选择依据。未强制覆盖、未取消 expected-active 检查、未改阈值。

## 原有 87 只来源问题

74 只零行、13 只覆盖不足的缺失 session 与查询区间逐只复核。候选文件哈希未变，QFQ 源文件元数据没有晚于先前查询的新变更；本地最新快照仍为 2026-08-19。网关本轮再次限流，没有这些 ticker 的新增覆盖证据，因此没有重复下载。Yahoo 仍缺少与 canonical_adjusted 的已批准价格口径映射，不能擅自混入未调整价格。

## 运行入口与证据

代码目录 `H:/workspace/PCSOS-pool-closure`；工作目录 `H:/workspace/PCSOS`；`PCS_ENV_FILE=H:/workspace/PCSOS/.env`；`PYTHONPATH=H:/workspace/PCSOS-pool-closure/src`；Python `C:/Python313/python.exe`。

原 checkpoint：`H:/workspace/PCSOS/pool_scan_runs/daily_coverage_recovery_20260905_1741`。本轮新增证据在其 `continuation_20260905` 子目录，旧逐 ticker 状态保存于 `baseline_tickers`；不覆盖历史审计附件。

日线验收实际命令：

```powershell
C:/Python313/python.exe H:/workspace/PCSOS-pool-closure/scripts/pcs_daily_coverage_recovery.py --output H:/workspace/PCSOS/pool_scan_runs/daily_coverage_recovery_20260905_1741 --phase verify
```

只读扫描实际命令：

```powershell
C:/Python313/python.exe H:/workspace/PCSOS/pool_scan_runs/daily_coverage_recovery_20260905_1741/continuation_20260905/run_read_only.py
```

该薄调用脚本将正式验收通过的 ticker 交给现有 `run_read_only_scan` → `run_pcs_pool`，没有另一套 Scanner。READ_ONLY 不接入账户，不做导入；规则保持原配置。扫描时间身份使用冻结请求时间，行情 session 仍为 2026-09-04，是固定 EOD 扫描，不是实时行情或交易建议。

下一次用户说“继续恢复日线”时，由 agent 读取原根目录更新后的 `resume_queue.json`，遵守退避、有界 probe 和再次 429 即停止的顺序；无需用户拼参数。原零行/覆盖不足项仍要求新的来源覆盖证据，冲突项仍要求上述身份依据。

本轮 CSV 的 `round_before_daily` 是 2009 只就绪的续轮基线；保留的 `before_own_daily` 是最初恢复任务的历史基线。扫描子集不修改原 universe；最终逐 ticker 表对 2953 只逐一对账。

156 只局部续跑实际调用同目录 `resume_read_only.py`，仍使用原 ReadOnlyScanRequest / run_read_only_scan；各自 request JSON 记录冻结日期、明确子集及执行预算。新增 Python 文件仅为实际调用和证据分组的审计副本，没有修改生产模块。
