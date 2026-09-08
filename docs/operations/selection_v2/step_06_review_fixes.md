# 第6步 R1–R5 复核修复

范围：继续 `codex/selection-v2-step-06`，起点
`d97b7815d07e0f5e82b3e0f10435f2b0258c701f`；第5步基线
`57a1184c8b823fb67ccaff91bdc4eb5f33a704cc` 为祖先。只修复复核列出的生命周期、
恢复、未知、逐日证据和请求时间语义。没有修改DTE、strike、评分、收益政策、期权路径、
TA-Lib既有约束或main；不进入第7步。

## 变更与公共契约

- R1：等待首次回踩时，超过b+15的实际触及可留在支撑组件，但不能成为合法r。
  明确收盘/结构失效仍先于过期处理；S15回踩保持自己的确认期限，S17可确认。
- R2：只提交连续有效OHLC前缀，阻断行仅展示。恢复先截取已提交历史，从边界下一日
  推进，不受展示起点移动影响。缺项逐次重建，revision按实际提交日计数；已提交输入
  改动仍报 `BREAKOUT_PRIOR_REPLAY_REQUIRED`，调用方须提供完整输入合法冷重放。
- R3：共享 `required_conjunction` 保留false优先的三值AND。发现、确认、结构存续及
  当前资格的必要证据缺失返回unknown/PARTIAL，明确false保持false，诊断缺项不阻断。
  缺项含日期、条件、原因、来源引用和影响范围。历史未知不统一阻断当前结论。
  三family聚合有true选true；无true但有unknown时保留unknown，旧两个子结果身份不变。
- R4：每个突破日保存当时的固定区身份、b/r/c、期限及窗口快照；适配器使用该快照，
  不从最终事件对象倒填历史字段。r前无test/economic/opportunity身份。
  `events`是最终事件汇总，`timeline`是逐日可知证据。transition引用当日身份。
  JSON、中文、AI和CSV由同一typed结果导出；CSV逐日缺项不倒填最后一天缺项。
- R5：纯API调用第4步同一个 `_resolve_requested_session`，沿用真实交易日历和时区、
  休市、盘中规则。历史事实算至日线证据日，请求资格用共享 `requested_applicability`
  独立判断：窗口已过false，其他不同EOD缺证据时null，列出所缺请求交易日。
  请求session/模式进入结果身份，接收时间不进入语义ID；事件ID不因请求时间变化。

共享组件 `pcs.trend.lifecycle` 只包含待推进日期、已提交历史截取、必要条件合取及
请求适用性。旧机会状态机和健康回调发现使用相同组件，其原分支行为保留；
突破特有的固定R、首回踩等待和重启规则留在突破family。没有扩展为全项目重构。

## 版本与旧产物

`BreakoutRetestResult` schema升级为2.0，计算版本为 `breakout-retest-v2`；
突破适配器及包含突破的聚合使用schema 1.3 / `entry-opportunity-v2.4`。
旧两个family保持原计算版本、政策和结果ID。

旧突破v1结果、政策和checkpoint不能作为v2当前结果或续算输入。
typed加载通过Literal版本及checkpoint必需字段拒绝旧数据；不要手改旧版本字段。
旧bundle保留原文件与hash，`read_opportunity_bundle`仅可验证并读取历史JSON以对账，
不表示其通过新typed结果兼容性验证。使用第5步已保存且hash有效的prepared输入重新
计算，默认构造新突破政策；不复用旧错误结果或checkpoint。

## 回归与专项证据

新增回归文件 `tests/trend/test_breakout_review_regressions.py` 先在未修复核心上执行：
18项中16失败、2通过，确实覆盖R1–R5反例。扩充后36项回归通过。
使用本机 `exchange_calendars 4.13.2` 的真实XNYS，不是占位或TEST工作日列表；
价格仍是明确TEST夹具，不代表真实行情成功确认。

源码联测：206 passed，1 deselected。排除项仅为历史
`talib_dependency_stays_inside_indicator_implementation`，未修复或宣称通过。

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/trend/test_breakout_review_regressions.py tests/trend/test_breakout_retest.py tests/trend/test_shallow_pullback.py tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py tests/trend/test_pullback.py tests/trend/test_indicators.py tests/trend/test_snapshot.py tests/trend/test_cleanliness.py -q -p no:cacheprovider -k 'not talib_dependency_stays_inside_indicator_implementation'
```

真实XNYS请求回归包括晚于entry_end、不同证据日但仍在窗口、同合法EOD、时区拒绝、
周末、休市和盘中解析；纯API/适配器与同日恢复一致。其他回归包括连续缺日、
首日无可提交记录、无效OHLC、真正partial恢复、滑动展示起点、b/r/c前缀快照、
三值缺项、明确否定、诊断缺项和旧版本拒绝。现有专项保留固定区、失效、重启、
S15/S17正向、两种等待阶段恢复及旧family独立身份验证。

## 最终提交的隔离导出与对账

输入只来自已保存的
`H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904`；
旧结果比较对象为
`H:/workspace/PCSOS/selection_v2_outputs/step_06_acceptance_a55963b_20260904`。
两者全部登记产物hash已在本轮预检通过，包含NVDA、PLTR、MSFT、HOOD、MDLZ、AAL、AAOI。
UBER既有缺口单列，不补行情，不重读canonical，不请求供应商，不跑全池。

最终输出目录为
`H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908`。
最终运行是否成功及精确源码SHA只能以该目录实际生成的下列文件为证，不能沿用旧5/3/0：

- `artifact_manifest.json`：实际源码完整SHA、clean标志、所有正式产物hash。
- `validation_run.json` / `acceptance_validation_run.json`：运行前后SHA及依赖一致性。
- `step_06_acceptance.json`：新计数、状态、四视图、独立API和恢复验收。
- `r1_r5_reconciliation.json`：旧/新完整manifest，7份feature view相等、14个旧family
  子结果ID相等；逐票旧/新事件计数、状态、资格、结果ID，以及每项事件/逐日字段差异和原因。
- `reconciliation_validation_run.json`：对账自身的代码/输入身份验证。

```powershell
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --families HEALTHY_PULLBACK,SHALLOW_PULLBACK,BREAKOUT_RETEST --as-of 2026-09-04 --run-id step06-r1-r5-20260908 --input-directory H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904 --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908
python scripts/accept_breakout_retests.py H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908
python scripts/reconcile_breakout_review.py H:/workspace/PCSOS/selection_v2_outputs/step_06_acceptance_a55963b_20260904 H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908
```

普通push后回读远端完整HEAD；以上实际产物和远端SHA在交接中报告。停止在第6步，
修复提交并不替代本轮用户复核通过。
