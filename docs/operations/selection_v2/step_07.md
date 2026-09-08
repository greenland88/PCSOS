# 第7步：上涨后的平台整理

状态：实现提交供第7步限定复核；未合并main，不进入第8步排序。
授权基线为第6步R1–R5已接受完整HEAD
`45634c1b8b0e0a5896017f503c22fdee864cb3e2`。
工作树 `H:/workspace/PCSOS-selection-v2-step-07`，分支 `codex/selection-v2-step-07`。

## 公共接口与范围

```python
detect_constructive_base(input: ConstructiveBaseInput) -> BaseResult
evaluate_entry_opportunity(input: OpportunityInput) -> EntryOpportunity
```

平台API消费已准备的 `OpportunityFeatureView` 和实际结构/健康/阶段字段。
新增 `CONSTRUCTIVE_BASE` family；固定展示顺序健康、浅回调、突破回踩、平台。
true优先，无true但存在unknown则保留unknown。排序仅解决展示主意见，不是收益排名。
平台形成、有效性、机会状态和请求时资格分开，不将平台形态叫公司业务质量或下单授权。
无期权依赖，不修改生产准入、DTE、短腿、评分、持仓、收益政策；不做研究、全池或FINAL OOS。

## 形成、来源和时间语义

F的前20个session为W，包含F；W之前20个为P。全部使用交易所下标及完整OHLC。
P存在已保存bullish足以证明上涨基础，其他未知保留覆盖；完全已知无bullish为false，
仍缺必需证据则unknown。F的neutral可形成，确认仍消费原有实际bullish/health/phase条件。
`BaseStructureEvidence`是独立可选证据：有来源、算法、确认日期和高低点比较；
已确认LH+LL拒绝，未来或过早确认的摆动点拒绝。未保存细节明确记录，不伪填pivot。

固定L=min(low)、U=max(high)、A=F的权威ATR；宽度>0且不超过4A。
TR包含前收跳空，保留20条样本；最近5日中位数<=此前15日中位数，等号有效。
保存所有极值日，代表日期平局取最近。首次形成冻结F/L/U/A，活跃期间不移动边界。

支撑算术从 `support_zones._update_zone` 提取为无I/O价格推进helper：
正常包装器保持available_at限制；回溯包装器不改正式区域日期，仅返回独立typed测试。
回溯测试统一retrospective=true、known_at=F、available_for_entry=false；
真实touch/held/departure和逐日价格证据保留。F时正式live tests为空。
新增typed `BASE_LOWER_BOUNDARY` 来源；上沿保存在 `BaseBoundary` 阻力实体，
不是“已守住支撑”。两条来源都包含base_id、F、W、极值日期及来源引用。

至少两次在F已HELD的独立测试；触及当日不HELD，r+1…r+3按固定ATR反弹和reclaim判断。
前一测试结束后，另一天完成离开，之后更晚一天才可再触及。没有同根OHLC内的顺序推断。

F WATCH且eligible=false。F+1…F+20首次live触及，r后3日共用确认，c日仅记录确认，
c+1…c+3复核当前资格。独立保存位置(close−L)/(U−L)，不截断；确认和当前资格均需<=0.50。
确认/当前条件直接复用共享函数，不伪造bullish或改变旧family阈值。

明确结构/收盘失效优先；然后检查固定上行离开和阶段期限；最后触及/确认/当前资格。
F+21首次触及不能成为合法r，F+20合法触及继续其自己的r+3期限。
三种过期分开记录；只产生一次首次触及机会。后续支撑证据可以归档，但不会刷新机会。
新平台W必须全部位于旧terminal之后，最早terminal+20，并重新满足完整形成条件。

收盘严格>U+0.10A记录BASE_UPSIDE_EXIT，机会EXPIRED而非支撑破坏。
突破通道独立计算；聚合关系只在当日确有合法breakout时关联两个实体，
并保存阻力/缓冲差异。不改突破子结果；关闭突破为NOT_EVALUATED，明确未合格与UNKNOWN分开。

## 恢复、身份与兼容

消费第6步共享 `pending_sessions`、`committed_days`、`required_conjunction`、
`requested_applicability` 和同一个请求日期解析；连续OHLC提交、展示起点分离、
缺日阻断行不提交。补未提交缺口可恢复，同日/冷算/保存恢复身份相等。
已提交历史修正报 `BASE_PRIOR_REPLAY_REQUIRED`；完整冷重放可通过
`replay_of_result_id`（聚合输入为 `base_replay_of_result_id`）保留原结果关系，
旧产物不覆写，运行时间不充当市场新事件。

每一天保存当时F/r/c、边界、期限、条件、身份，不从最终事件倒填。
F前没有平台信号，r前没有live/economic身份，c前没有entry窗口。
真实XNYS提前补齐日历尾部，不按价格数组尾部截短期限。
CURRENT_EOD沿时区、休市、盘中既有解析，过窗口false，缺对应请求证据null并列session。

`BaseResult` schema1.0 / `constructive-base-v1`；新机会family及含平台聚合
schema1.4 / `entry-opportunity-v2.5`。新平台版本不匹配明确拒绝，不给旧结果补造平台。
旧三family政策及语义ID保持；新增可选input/result字段默认空，不参与旧子结果计算。
平台policy包括执行单全部初值、来源标记，及实际消费的共用确认/支撑policy。
run/request/received_at不进业务ID。

## 输出与查询

新增文件由同一typed结果导出：`base_results.json`、`base_regions.json`、
`base_formation_candidates.json`、`base_retrospective_evidence.json`、`base_timeline.json`、
`base_relationships.json`及两份schema；原JSON/CSV/中文/AI视图保留全部family。
中文展示形成、上下沿、两次守住日期、位置、等待期限和取消条件，候选逐条件中文对账。
CSV逐日记录base_id/L/U/position/validity及首次触及期限，不用最终对象填历史。

独立查询 `find_base_event`、`find_base_day`、`find_base_test`；最后一个返回typed
历史或live对象并明确retrospective。`--summary`为已有CLI新增的短中文输出，仍完整保存。

## 已运行源码验证

平台54项专项，包括执行单手工算术样例：L/U=100/104，宽度/A=2，TR中位数1.6/0.6，
两次回溯HELD、F无live test、S23确认且当日false、S24–S26可评估。
测试使用真实XNYS日期，但价格/结构/ATR明确是TEST，不声称由人工OHLC算出了这些指标。
现有market_structure/indicator专项保护权威实现，本步未替换指标或pivot算法。

完整受影响专项：260 passed，1 deselected；旧TA-Lib导入位置约束
`talib_dependency_stays_inside_indicator_implementation`继续单列，未修复、未声称通过。

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/trend/test_constructive_base.py tests/trend/test_breakout_review_regressions.py tests/trend/test_breakout_retest.py tests/trend/test_shallow_pullback.py tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py tests/trend/test_pullback.py tests/trend/test_indicators.py tests/trend/test_snapshot.py tests/trend/test_cleanliness.py -q -p no:cacheprovider -k 'not talib_dependency_stays_inside_indicator_implementation'
```

## 最终提交的有界产物与实际命令记录

输入优先复用第6步最终目录
`H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908`，已预检所有登记hash、
clean标记和源码45634c1完整身份。7票各260根，早期结构仍有真实未知；可选结构细节未保存
明确记录，本步无需重新派生指标。UBER保持INSUFFICIENT_FEATURE_WARMUP。
不下载新行情、不重读canonical；旧输入和旧产物保持原样。

最终目录：`H:/workspace/PCSOS/selection_v2_outputs/step_07_acceptance_20260908`。
该目录实际 `artifact_manifest.json` 绑定最终源码SHA；`validation_run.json`与
`acceptance_validation_run.json`记录运行身份不变；`step_07_acceptance.json`保存
候选/形成/回溯/实时触及/确认/当前资格的实际计数、逐票原因、缺项、21个旧子结果ID对账、
纯API/单票/批量/保存恢复、原输入hash不变。未生成该证据前不预填平台数量。
真实样本若无确认，成功路径仅TEST验证，不能作为市场正例或盈利证明。

```powershell
$bundle='H:/workspace/PCSOS/selection_v2_outputs/step_07_acceptance_20260908'
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --families HEALTHY_PULLBACK,SHALLOW_PULLBACK,BREAKOUT_RETEST,CONSTRUCTIVE_BASE --as-of 2026-09-04 --run-id step07-20260908 --input-directory H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908 --output-directory $bundle --summary
python scripts/accept_constructive_bases.py $bundle
python examples/constructive_base.py --input-directory $bundle --symbol HOOD --as-of 2026-09-04 --summary
python -m pcs.cli entry-opportunity --symbols HOOD --families CONSTRUCTIVE_BASE --as-of 2026-09-04 --run-id step07-single --input-directory $bundle --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_07_commands_20260908/single --summary
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --as-of 2026-09-04 --run-id step07-render --input-directory $bundle --render-only --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_07_commands_20260908/render
```

合法F/等待r的prefix及恢复调用必须使用实际发现的事件日期。五类实际运行的命令、
选择的股票/日期和结果身份另存最终目录 `command_validation.json`，不提前编造真实F。
若7票没有合法平台，明确使用手工TEST平台做该演示并标记，不补行情强造市场平台。
所有产物仅为股票观察；完成自动提交、普通push、回读远端完整HEAD后停在第7步等待复核。
