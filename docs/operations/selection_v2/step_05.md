# 第5步：强趋势浅回调

## 已接受基线与边界

用户已明确复核通过第4步 R1–R3：缺日补齐、连续增量、同日恢复和缺项汇总符合要求。
接受完整 HEAD 为 `a1d23590a840ee7c4152332a6d76eb09b1feae2b`。
从该提交建立独立 `codex/selection-v2-step-05`，
工作区 `H:/workspace/PCSOS-selection-v2-step-05`。第4步旧文档的待复核文字为历史状态。
保留前四步成果及旧产物，不合并 main，不改变生产规则、权重、仓位或扫描判定。

## 接口映射与版本

| 已有接口 | 第5步复用／扩展 |
|---|---|
| OpportunityDataReader / PCSDataAccess | 单次验证canonical日线、权威指标、支撑；两个family共用typed输入 |
| support-zones-v2 | 原区域、测试、HELD、固定失效线不变 |
| setup_detectors | 新公开纯函数 detect_shallow_pullback(ShallowPullbackInput) -> SetupEvidence |
| evaluate_entry_opportunity | 同一机会状态推进函数消费两family；独立启停、确认和当前资格 |
| checkpoint / resume evidence | 增加冻结峰值、前日ATR、峰后累计低点及首次超限；旧状态不伪装新版本 |
| pool机会输出 | JSON、AI、中文、CSV、逐日条件／事件及完整输入保存；仅重建视图无需重读canonical |

新结果 schema 1.2 / entry-opportunity-v2.2，SetupEvidence 1.0 / shallow-pullback-v1；
浅回调政策 shallow-pullback-observation-v1。共有确认仍读取第4步 effective_policy。
峰值为触及前20个交易日high最大值，平局取最近session；
深度分母固定前一交易日ATR14，初次深度闭区间[0.25,1.50]，
累计最低价覆盖峰后至当前（包括触及前）。超过1.50终止本family资格，不宣称结构已破坏。
建区ATR、深度ATR、当日ATR独立保存；原始行情时间未知不以接收时间代替。
日期型known_at表示已完成交易日，不能推断盘中路径。

业务身份排除run/request/received及恢复诊断；实际数据、参数、价格口径和算法身份参与。
纯检测恢复另核验已消费前缀hash，修正输入要求重放；共享状态机会根据已有机制重放。
历史SetupEvidence保留原调用上下文，因此恢复与冷算按语义结果ID和事实对账，不要求调用元数据相同。
跨第4／5步对象升级会改变ID，不能声称与旧产物同身份；新版本健康子结果不受浅回调开关／顺序影响。
经济事件保留第4步symbol/touch日期引用，聚合同时显式按symbol/zone/test去重；不产生订单。
主显示顺序：优先当前可评估，然后HEALTHY_PULLBACK、SHALLOW_PULLBACK。

缺量不造0或1：观察适配器显式启用价格计算的allow_missing_volume，
旧生产调用默认严格校验保持原行为，RVOL及相关确认仍未知。
必需健康／峰值／前日ATR缺失按条件报告；无合法触及是明确false，
其他必需未知仍保存于coverage，不用缺失伪造事件。

## 独立调用与输出

`examples/shallow_pullback.py`从已验证保存输入调用纯检测器。
`find_shallow_evidence(result, session, test_id)`查询完整事实；原机会／日期／条件查询仍可用。
双family结果放在family_results，顶层只是主显示结果，不能把其状态当所有family都通过。
prepared_opportunity_inputs.json保存合法输入，复核可不重复读取供应商或重算指标。
结果内support_facts含完整区域来源；新低点、均线、HELD的原身份不替换。

## 提交、验证与真实验收

计算提交集合：

- `6aa541cf267fd899dbc6e6cb96a5e135388691b0`：完整实现、示例、测试、验收器。
- `0cf463d364e128ff9e74d204432ed058d3730f4b`：真实产物回读发现政策来源被重标，修复typed往返；保留原失败验收产物。
- `f37f7560a421a402f4fea02070761f9e721a84a9`：不完整RVOL日历条件同时保持null/UNKNOWN。

最后完整专项：149 passed, 1 deselected in 9.58s。命令：

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-05
python -m pytest tests/trend/test_shallow_pullback.py tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_pullback.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py tests/trend/test_indicators.py tests/trend/test_snapshot.py tests/trend/test_cleanliness.py -q -k 'not talib_dependency_stays_inside_indicator_implementation'
```

该deselected项并非未检查：首次包含它的运行实际失败，原因是第2步
`underlying_profile.py`已import talib，而旧测试只允许indicators.py使用TA-Lib。
已通过git show核对接受基线也存在该import；本步未改该文件或修改测试来冒充通过。
本步新增22项，覆盖深度闭区间、前日ATR、同根新高、峰前缀缺日、触及前深跌、
深度失格不复活、双family独立资格／去重、真实续算、同日恢复、修订拒绝、
价格缩放、未来隔离、缺量无伪造及typed产物往返。测试不证明收益或实际行情权限。

### 来源与输出

原第4步 `step_04_acceptance_aaa5279_20260904` 的13个登记文件hash通过，
7个结果可读，但没有保存完整OpportunityInput，不能把60根报告代替前缀。
本次一次canonical读取由干净6aa541c源码执行，使用既有PCSDataAccess验证路径。
7票每票实际逻辑读取260根，指标起算2025-08-25，观察2026-06-11—2026-09-04共60交易日。
峰前20日、前日ATR、RVOL前缀均保留。SPY共享缓存，两个family不重复计算基础指标／区域。
完整generation路径、各文件hash及价格口径见原输入source.detail和read_audit。

本次manifest identity：
`bcf968b0aff51058367d31fbc9fb129b7cdd8e153995e44cef92de3dea207a6e`；
manifest SHA256：
`d3873cb0c5d8321376c44ae36cf05efd4d80dd0fb95802296c06fd987acf36c4`。
manifest及实际读取16个canonical文件在读取前后、交接前再次核对均未变。
当前manifest与旧第4步记录不同，因此不声称跨版本结果同身份。
旧production意见及support-zones-v2仍沿原计算，未改变旧策略参数。

隔离目录（均保留，不覆盖）：

- 初次真实读取：`H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_6aa541c_20260904`。
- 政策回读修复核验：`H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_reload_20260904`。
- **最终交付**：`H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904`。
- 实际命令演示：`H:/workspace/PCSOS/selection_v2_outputs/step_05_commands`。

最终目录绑定干净f37f756，仅消费初次已验证并hash保存的输入，没有重复读取canonical、
下载行情或重跑扫描。artifact_manifest登记所有主产物hash；
validation_run.json与acceptance_validation_run.json记录提交及依赖未变化。
step_05_acceptance.json记录逐票独立API、family子结果、前缀续算／同日恢复及视图结果。
93条有触及的完整支撑内容与输入逐对象对账，source_ids均可在sources中找到实际对象，
不是只比较条数。中文、AI、CSV、逐日、条件和转换明细都由同一typed结果生成。

### 逐票结果

| 股票 | 浅回调观察触及/合格事件/确认 | 健康回调事件/确认 | 9月4日健康通道 | 浅回调未合格实例（未舍入计算） |
|---|---:|---:|---|---|
| NVDA | 19 / 0 / 0 | 1 / 1 | EXPIRED，false | 08-19健康上涨，但深度1.684 ATR；07-14当日1.411、累计3.349 |
| PLTR | 12 / 0 / 0 | 0 / 0 | NO_SETUP，false | 08-26健康上涨，但深度1.745 ATR |
| MSFT | 12 / 0 / 0 | 0 / 0 | NO_SETUP，false | 09-02强健康上涨，但深度2.061 ATR |
| HOOD | 10 / 0 / 0 | 1 / 1 | ENTRY_READY但当前超距，false | 08-31健康上涨，但深度1.933 ATR；08-21深度0.519但趋势deteriorating/健康mixed |
| MDLZ | 18 / 0 / 0 | 2 / 1 | ENTRY_READY，true | 09-01健康上涨，但深度3.200 ATR |
| AAL | 10 / 0 / 0 | 0 / 0 | NO_SETUP，false | 08-31深度7.561，趋势deteriorating/健康weakening |
| AAOI | 12 / 0 / 0 | 0 / 0 | NO_SETUP，false | 08-13初次1.237/累计1.373，但结构neutral、健康weakening |
| UBER | 未生成 | 未生成 | 未知 | INSUFFICIENT_FEATURE_WARMUP；未补数据 |

七票浅回调当前均NO_SETUP/false，不是ENTRY_READY，也不是新增交易许可。
未产生合格事件，因此真实样本不能证明浅回调确认/失效的实际市场覆盖；这些路径由TEST专项验证。
无新增候选不触发改阈值或换股票。

**缺项保留**：沿当前验证输入，06-11—08-06的39个session健康度及阶段未知；
每个family保存78条带日期的coverage缺项。已有200根指标前缀不能使健康/阶段所需的
长均线斜率在最早39日完成，未用标签补造值。明确的深度/结构不满足仍可证明该日false；
COMPLETED仅表示该结论可得，不表示全部趋势字段齐全。
原健康结果中NVDA/MSFT/MDLZ/AAL仍PARTIAL；两通道的缺项独立读取，不能只看主显示状态。
全部价格单位USD沿用来源配置假设；行情精确source_timestamp未知仍null。

### 五类实际可运行命令

下面均读取保存输入，执行前为输出选择**新的空目录**；现有演示目录已经存在，不覆盖。
执行这些命令不需要供应商或期权服务。检测器不执行完整历史生命周期，恢复使用独立保存结果。

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-05
$env:PYTHONPATH='H:/workspace/PCSOS-selection-v2-step-05/src'
$bundle='H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904'

# 独立Python公开检测API
python examples/shallow_pullback.py --input-directory $bundle --symbol NVDA --as-of 2026-09-04

# 仅浅回调生命周期
python -m pcs.cli entry-opportunity --symbols NVDA --families SHALLOW_PULLBACK --as-of 2026-09-04 --run-id manual-shallow --input-directory $bundle --output-directory H:/workspace/PCSOS/selection_v2_outputs/step05_manual_shallow

# 两通道有界批量
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --families HEALTHY_PULLBACK,SHALLOW_PULLBACK --as-of 2026-09-04 --run-id manual-both --input-directory $bundle --output-directory H:/workspace/PCSOS/selection_v2_outputs/step05_manual_both

# 从已保存2026-08-03前缀真实续算
python -m pcs.cli entry-opportunity --symbols NVDA --families HEALTHY_PULLBACK,SHALLOW_PULLBACK --as-of 2026-09-04 --run-id manual-resume --input-directory $bundle --resume-directory H:/workspace/PCSOS/selection_v2_outputs/step_05_commands/prefix --output-directory H:/workspace/PCSOS/selection_v2_outputs/step05_manual_resume

# 只从结果重新渲染，不调用检测器
python -m pcs.cli entry-opportunity --symbols NVDA --as-of 2026-09-04 --run-id manual-render --input-directory $bundle --render-only --output-directory H:/workspace/PCSOS/selection_v2_outputs/step05_manual_render
```

上述命令类型已实际运行成功；演示prefix日期2026-08-03，CLI恢复NVDA result_id与批量相同。
最终有界导出与验收器命令：

```powershell
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --families HEALTHY_PULLBACK,SHALLOW_PULLBACK --as-of 2026-09-04 --run-id step_05_acceptance_f37f756_20260904 --input-directory H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_6aa541c_20260904 --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904
python scripts/accept_shallow_pullbacks.py H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904
git diff --check a1d23590a840ee7c4152332a6d76eb09b1feae2b HEAD
```

验收结论：组件及上述代码/产物专项通过；真实覆盖7/8且明确保留历史字段缺项，
并非经济效果或生产采用验收。等待人工源码复核；不继续第6步、不合并main。
