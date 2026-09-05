# Pool Scan 根因核实与恢复（2026-09-05）

本记录撤回“唯一 blocker 是外部凭证”。代码修复提交为 `8c7f948`，
任务分支 `codex/pool-scan-closure`；不合并 main。账户检查按用户后续指示
留到交易决策时，本次扫描不要求 Robinhood 账户数据。

## 已确认根因

- 原工作区 `H:/workspace/PCSOS/.env` 存在，独立 worktree 没有 `.env`。
  control plane 原先先检查密码，后进入才加载环境的 client 构造函数，
  因此错误报告缺凭证。现在支持 `PCS_ENV_FILE`，先加载、保留继承环境、
  再构造 client。原授权配置加载后密码存在；默认 URL 的有界 health probe
  HTTP 200；子进程继承检查通过。没有将密钥写入文件、报告或提交。
- 前一任务 `01a0705d-12f5-71f2-8f2c-c4c3b73e5adb` 的恢复命令错误要求
  `2026-08-01 .. 2026-10-31` 报价。control plane 按 required_start/end
  生成 Q3/Q4；并非按到期日分区。本次需求为 **2026-09-04 单日报价**。
  30–45 DTE 是合约属性；10 月到期不授权未来报价导入。
- `clickhouse.fetch_options_range` 过滤 TradeDate；control_plane options
  handler 按 trade_date 的 year/quarter 分区。`required_start/end` 是报价
  覆盖边界，expiration_date 是合约到期日。
- provider 的 AUTHORIZED_SOURCE_NO_ROWS 原先被顶层 DATASET_GAP 掩盖；
  incremental updater 的 SUCCESS 又被 coordinator 错认成失败。两者已修复，
  canonical readiness 仍以独立读取为准。
- 全历史 admission 会先被早年坏 OHLC 分区阻断。新增显式 required_start
  限定所需年份，年份内完整验证，不截掉坏行；旧历史仍须独立准入。
- 周六 EOD 使用 previous_session(non-session) 会抛错；改为交易所日历的
  date_to_session。当前 EOD adapter 独立记录 decision_as_of、
  market_data_as_of、portfolio_observed_at、event_known_at；历史 PIT 继续
  拒绝未来账户。复用同一 candidate constructor 与 DecisionEngine。

## 定向真实证据

AUPH/BKKT 各经标准 control plane 查询 2026-09-04，授权 ClickHouse 表
`firstrate.options_kline_1d` 都返回零行。另一次有界 2026-08-28 .. 09-04
只读 probe 分别发现 900 / 2824 行，最新均为 09-03。不能用这些旧报价替代
09-04，也不能由此宣称全市场或其他来源不存在数据。

定向 run `96f1dc7c71c54a5b991ca33a2b46faf8`：当前 EOD 时间为
2026-09-05T12:25:46.600716-04:00，行情 session 为 2026-09-04。
2/2 daily/timing 成功评估并满足 timing；options 读取尝试 2，成功评估 0，
spread 0。事件日历来源尚不可用；DecisionEngine 未获得完整合约与事件输入，
因此未真实运行；portfolio 按用户要求推迟，不能声称通过或交易级 READY。
该定向产物早于 source fingerprint 修复，保留作分阶段诊断，不作为最终代码
身份的可复用 CURRENT。最终全池产物另行记录。

## 历史比较边界

仅复用两份旧产物作历史计数比较；全部 artifact_hashes 已校验。
旧 run `13cb13f4ee3e4b8998e0b4a7f8b23b66` 为 09-03：131 daily-ready；
`c9b3c6a093fa4f9cabb88d7abdb06725` 为 09-04：125 daily-ready。
变化 ticker 为 GOOGL、HOOD、META、SOXL、SPY、TSLA，均为 DAILY_STALE。
这是 session 推進导致的 freshness 差异；没有观察到数据回退证据。
旧产物缺少完整逐 ticker generation 身份，不能据此证明字节级历史不变。
六只现已通过标准 daily 更新，并经 09-04 verified handle 独立确认 READY。

## 测试

`PYTHONPATH=src python -m pytest tests/data/test_recovery_configuration.py
 tests/data/test_control_plane.py tests/data/test_control_plane_boundary.py
 tests/data/test_massive_client.py tests/pool/test_context_adapters.py
 tests/pool/test_process.py tests/pool/test_final_correctness.py
 tests/pool/test_preparation_orchestration.py
 tests/data/test_pool_options_handle_routing.py -q`：110 passed。

随后 source identity 与 adapter 错误保留修订，受影响的四个 pool 文件测试
再次验证：68 passed。git diff --check 通过。

旧 options-routing fixture 在原工作区 c62db50 上复现失败：它没有提供新的
完整 timing producer 输出，却期待 DISCOVERY 即 PASS。现隔离上游 timing
fixture、使用现行 options rules，明确断言 DISCOVERED 与未连接 selector；
真实 DecisionEngine PASS/RED 拒绝仍由独立 fixture 验证。没有改变生产阈值。

Fixture 测试不是 provider probe，也不是实际交易验收。未读取 FINAL OOS，
未自动下单，未探测账户，未改策略配置，未覆盖用户研究文件。


## Universe 分组与实际新增覆盖

[逐 ticker 分组](pool_root_cause_20260905/daily_groups.json) 覆盖原 2828 个
blocked ticker，一只不漏：

| 分组 | 数量 | 证据/边界 |
|---|---:|---|
| 有效物理数据待 admission | 161 | 文件校验通过不等于已准入 |
| 缺失或过期 | 2217 | 保留缺失/旧日期及原始原因 |
| 其他具体问题 | 450 | 主要为候选数据内容冲突，保留具体原因 |
| 已确认新上市/退市/不适用 | 0 已确认 | 生命周期权威信息未核实，不能推断全部适用 |
| 已确认权威来源不可用 | 0 已确认 | 未对全 universe 逐一 probe，不能推断全部来源可用 |

161 个正式 admission 中，131 个通过独立 verified handle；30 个因为
MIGRATED_ACTIVE_CONTENT_CONFLICT 中止，随后 verified handle 仍报 warmup
不足。保存已提交、复用、失败分区和收据，不删除或强行替换 active generation。

早年坏分区排除出当前需求后，AAP 经 bounded admission、标准 daily 更新及
verified handle 成功。加上六个 freshness 恢复，总新增 131 + 1 + 6 = **138**。
[收据与分区结果](pool_root_cause_20260905/admission_results.json)、
[AAP 实际恢复](pool_root_cause_20260905/AAP_bounded_recovery.json)、
[131→125 逐 ticker 差异](pool_root_cause_20260905/daily_131_to_125.json) 均保留。
历史坏分区不是有效历史证据；本次未恢复全部旧历史或批量下载过期 universe。

## 最后一次全池真实扫描

run_id：`455faf98607f45848ade9348628f4a37`；代码提交 `8c7f948`。
请求/决策时间 `2026-09-05T12:36:56.667418-04:00`，行情 session `2026-09-04`，
CURRENT EOD scan、READ_ONLY；2953 个 ticker 恰好一条结果，运行约 142.904 秒。
全部产物哈希校验通过；源代码、有效规则和 manifest 指纹已记录。真实 canonical
日线和 benchmark 路径已运行；扫描期间 provider/import/promotion 调用均为 0。

- daily-ready：**263 / 2953**，此前 125，新增 138，无覆盖流失。
- daily/timing 成功评估分母：263；timing 条件满足 **4 / 263**，即 AUPH、
  BKKT、GSHD、IDYA。另有 119 个规则拒绝、67 个 WATCH；没有 timing-unavailable。
- options 读取尝试 4，成功读取/评估 **0 / 4**；spread 0。
  这不是“完整评估后没有机会”。
- 事件、DecisionEngine 真实合约决策均未完成；portfolio 按用户要求留到交易
  决策时。本次不将账户缺失当 scan 阻塞，也不宣称账户已通过或交易级 READY。
- 2690 个 daily 仍 blocked：2560 ACTIVE_GENERATION_MISSING、130 DAILY_STALE。
  这些是读取入口状态，不替代上面的物理数据/冲突根因分组。

[最终汇总与新增 ticker 列表](pool_root_cause_20260905/final_summary.json)、
[2953 个逐 ticker 结果](pool_root_cause_20260905/final_ticker_results.json)、
[run manifest](pool_root_cause_20260905/final_run_manifest.json)、
[snapshot](pool_root_cause_20260905/final_snapshot.json)、
[本地完整检查点哈希](pool_root_cause_20260905/checkpoint_hashes.json)。

剩余 blocker：AUPH/BKKT 为 09-04 授权表确认零行（AUTHORIZED_SOURCE_NO_ROWS），
需要该交易日的真实报价；GSHD/IDYA 为 canonical options 未准入，未做额外
provider probe，不把它们推断为来源空。事件窗口没有具备来源与覆盖证明的
输入；用户确认尚未找到该来源。账户输入由用户明确延期，不属于 scan 的
恢复前提。生命周期分类仍需权威元数据，不能用陈旧价格推断退市。

提交后补充 mode/session 定向测试：8 passed。报告文件密钥扫描：0 命中。

## 恢复命令（当前 EOD 扫描，不需要账户）

在 PowerShell 选择原数据工作区并指向任务分支代码；环境路径只引用授权文件，
不复制密钥。标准 pool-scan 准备开关会复用有效 daily，只恢复实际所需 options
报价日期。若来源仍未更新，保留准确数据 blocker；不会下单。

```powershell
Set-Location H:\workspace\PCSOS
$env:PYTHONPATH = 'H:\workspace\PCSOS-pool-closure\src'
$env:PCS_ENV_FILE = 'H:\workspace\PCSOS\.env'
python -m pcs.cli pool-scan --symbol AUPH --symbol BKKT --mode EOD --as-of latest --data-mode PREPARE_THEN_SCAN --auto-prepare-data --output-directory H:\workspace\PCSOS-pool-closure\pool_scan_runs\recovery
```

日常只读全池入口使用同一命令，将 symbol 参数替换为
`--universe-id global_pcs_candidates --data-mode READ_ONLY` 并移除
`--auto-prepare-data`；结果是分阶段扫描，不是账户批准的订单。
