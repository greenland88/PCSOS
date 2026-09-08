# 第4步：机会状态机与健康回调

## 边界与版本

- 基线：`3b4d674ec418106e5ba24cd7a52204a6199084a8`，包含已验收第3步及计划v1.7。
- 分支：`codex/selection-v2-step-04`。
- 最终计算源码提交：`b167a4bff501b06f72879e94671c643951376851`（含前置实现提交 `e0452ab9d8dbc08ef469162a7d5c233e1d0bad6c`、证据/身份补强 `ca259923b4f4ddce7ef2d7c55100adc9340b3f36`及失效证据角色修复 `8865306745a138126bc75ecd1c023a60d5a069a5`）。
- 公共接口：`pcs.trend.opportunity_engine.evaluate_entry_opportunity(input: OpportunityInput) -> EntryOpportunity`。
- schema `1.0`；算法 `entry-opportunity-v2`；policy `healthy-pullback-opportunity-v1.7`。

本步是研究/观察组件。`ENTRY_READY`表示按v1.7机械条件形成的股票机会及当前评估窗口，不是下单授权，也不表示期权已评估。旧 `replay_opportunities()`、正式Pool准入、期权门槛、评分和最终动作未切换。没有运行全池、供应商请求、数据导入、收益优化或FINAL OOS。

## 实际入口与职责

- `trend/selection_models.py`：typed输入、政策、条件、事件、状态、checkpoint和结果。
- `trend/setup_detectors.py`：健康回调发现、RVOL、确认及当前适用性事实。
- `trend/opportunity_state.py`：统一状态转移、幂等身份、缺口停步和恢复校验。
- `trend/opportunity_engine.py`：公共v2 API及显式回放边界；旧回放保持兼容。
- `pool/opportunities.py`：PCSDataAccess读取、因果特征/支撑投影、原子落盘、多视图和pool观察边界。
- `market_context.evaluate_market_context_opportunity()`：单票上下文显式v2观察边界。
- CLI：`python -m pcs.cli entry-opportunity ...`。缺省扫描不调用它。

核心函数只消费准备好的 `OpportunityInput`，不读文件、不请求供应商、不调用AI、不计算另一套ATR。pool、单票和回放三个v2边界均调用同一核心。输入内保留旧回调分类/执行状态；v2差异单列，不能反推旧程序曾经执行未记录的门槛。

## 输入、因果性与政策

canonical适配器通过 `ProfileDataReader` 固定ManifestSnapshot和active verified daily handle，每票读取260根：200日指标暖机加60个XNYS分析交易日。逻辑范围为 `2025-08-25`—`2026-09-04`，分析范围为 `2026-06-11`—`2026-09-04`。价格口径为 `canonical_adjusted`，公司行动身份为 `canonical_identity`。

现有 `calculate_base_indicators()`只运行一次，输出SMA20、SMA50、SMA200、ATR14和RSI14；EMA200单独明确命名。现有3/3 market structure confirmed swings只预计算一次，再按 `confirmed_at` 逐日投影。第3步support-zones-v2使用同一特征身份按日推进；每个机会日只消费当日已经可知的区域和测试，不能把9月4日最终状态回填历史。

`OpportunityFeatureBar`新增真实volume。RVOL分母严格是当前日以前20个完成session的平均量，当前量不进入分母。缺量保留null；由于旧价格指标函数的输入验证要求OHLCV齐全，adapter仅在不消费volume的局部价格指标副本用非零schema占位，通过审计字段明确记录；原volume从未改写，RVOL继续UNKNOWN。

健康回调沿现行分类和分支顺序：20日high口径近期高点、5%—15%健康范围、距SMA20不超过1.5 ATR或距SMA50不超过2 ATR。旧逻辑先判断浅回调，因此在其余浅回调条件成立时，精确5%仍为 `shallow_pullback`。结构与趋势健康分别记录。发现还要求触及事件绑定的区域在触及前已经可知；同日形成区域不能认领较早盘中触及。多个合法区按距收盘、available_at、zone_id、test_id的版本化稳定顺序选择，其他候选保留。

## 状态与确认语义

业务状态严格为 `NO_SETUP/WATCH/CONFIRMING/ENTRY_READY/EXPIRED/INVALIDATED`。缺日、缺量或未知条件是能力状态，不新增DATA_BLOCKED市场状态。首个缺失交易日停止推进，保存 `evaluated_through`、`last_known_state`，请求时当前可评估为null；冷启动缺数据时state也是null，不伪称NO_SETUP。日历仍可独立报告确认截止或入场窗口在请求session是否已经过去，但该事实不会推演缺失期间的价格、结构或市场终态。

首次合法触及为b：b日WATCH且不可确认；b+1至b+3（含两端）按未舍入值同时检查：

1. close严格大于固定zone.upper + 0.10×formation ATR；
2. close不低于上一完成日close；
3. close location至少0.55；
4. RVOL20至少0.80；
5. 同日可知的同一test已HELD、zone未BROKEN且结构无明确阻断；
6. `(close-zone.upper)/current ATR`不超过1.75。

长上影条件为上影/ATR至少0.50且close location不超过0.35，在本policy中是 `CONFIRMATION_BLOCKER`，不是既有确认机会的自动失效。RSI高位只作诊断。ATR非正/非有限、high=low、缺量、分母不足20或不为正分别输出UNKNOWN与稳定reason code，不用默认值通过。

首次确认日期冻结为c；c日仅说明确认，入场观察窗口是c+1至c+3。窗口内逐日复核固定支撑、结构和当前距离。超距时保留ENTRY_READY历史及原窗口、当前eligible=false；原窗口内回到范围可重新变为true，窗口不顺延。判断优先级为固定支撑/结构失效、对应阶段过期、确认或当前条件。b+3可确认，b+4才过期。同日有失效和有利确认事实时双方条件都保存，但结果为INVALIDATED。

区域上下沿、formation ATR和失效线在setup冻结，后续均线或ATR不移动它们。终态事件不会因重扫或反弹回到活跃态；只有新的独立support test可开始新机会。`economic_episode_id`只绑定symbol、family和实际touch session，不因run、policy、算法或zone ID版本变化制造新市场事件；`opportunity_id`另绑定实际policy、指标、来源、zone/test及固定参数；`result_id`再绑定有效输入范围和业务过程。run_id、request_id、received_at、恢复方式及调用诊断不进入语义ID。

保存checkpoint只含必要episode状态、revision、已提交result引用、输入/支撑/policy/指标/价格身份和evaluated_through；完整逐日条件与转换单独落盘。恢复前校验旧前缀与支撑hash；相同前缀标记兼容重放，修正或补齐历史则从合法输入重放并保留旧产物。输出目录必须为空，原子写入且manifest记录每个文件hash。

## 独立调用、输出和查询

核心调用：

```python
from pcs.trend.opportunity_engine import evaluate_entry_opportunity

result = evaluate_entry_opportunity(prepared_opportunity_input)
assert result.calculation_version == "entry-opportunity-v2"
```

canonical单票：

```powershell
Set-Location H:/workspace/PCSOS
$env:PYTHONPATH='H:/workspace/PCSOS-selection-v2-step-04/src'
python H:/workspace/PCSOS-selection-v2-step-04/examples/entry_opportunity.py --symbol NVDA --as-of 2026-09-04
```

有界批量与验收：

```powershell
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --as-of 2026-09-04 --run-id step_04_acceptance_b167a4b_20260904 --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_b167a4b_20260904
python H:/workspace/PCSOS-selection-v2-step-04/scripts/accept_entry_opportunities.py H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_b167a4b_20260904
```

输出包括统一 `entry_opportunities.json`、AI视图、中文报告、CSV摘要、逐日timeline、完整conditions、transitions、checkpoint、输入/结果schema、字段字典、正常/缺失示例、读取审计及artifact manifest。绑定支撑事实内嵌创建及后续观测的完整typed来源，不只保存ID；超距与窗口内重新合格有独立事件。CSV的null写为空值并引用JSON明细，不转成false。`find_opportunity_episode()`、`find_opportunity_day()`和`find_opportunity_condition()`可独立查询；`load_opportunity_state()`先核验保存状态文件hash。

## 专项验证

最终命令：

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_pullback.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py -q
git diff --check 3b4d674ec418106e5ba24cd7a52204a6199084a8 HEAD
```

本步新增22项，连同旧机会、回调、support-zones、support和market structure专项共85项。覆盖S0触及/S1等待/S2确认、b+3/b+4边界、确认日不入场、同日相反证据优先级、缺日停步及独立日历截止事实、调用身份稳定、短prefix恢复与冷批量一致、历史修正重放、超距及窗口内重新合格事件、固定支撑、完整支撑来源、未来数据隔离、精确5%分支、high=low、异常ATR、缺量、零分母、同日HELD消费、终态不复活、经济/机会身份分层、三类入口同核、typed落盘/哈希/恢复和CSV未知语义。第3步既有未确认pivot与future-as-of专项同时运行。

## 真实8票验收

最终产物：`H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_b167a4b_20260904`。产物manifest绑定干净源码 `b167a4bff501b06f72879e94671c643951376851`。

| 股票 | 结果 | 9月4日状态 | 当前可评估 | 当前事件触及/确认 | 原窗口 | 事件/转换数 |
|---|---|---|---|---|---|---:|
| NVDA | COMPLETED | ENTRY_READY | true | 09-01 / 09-02 | 09-03—09-08 | 4 / 12 |
| PLTR | COMPLETED | ENTRY_READY | true | 09-02 / 09-03 | 09-04—09-09 | 2 / 4 |
| MSFT | COMPLETED | EXPIRED | false | 07-23 / 未确认 | 不适用 | 2 / 6 |
| HOOD | COMPLETED | ENTRY_READY | false（当前超距） | 08-31 / 09-02 | 09-03—09-08 | 3 / 7 |
| MDLZ | COMPLETED | ENTRY_READY | true | 09-01 / 09-02 | 09-03—09-08 | 5 / 14 |
| AAL | COMPLETED | INVALIDATED | false | 07-08 / 未确认 | 不适用 | 1 / 3 |
| AAOI | COMPLETED | INVALIDATED | false | 08-13 / 08-14 | 08-17—08-19 | 1 / 3 |
| UBER | 未生成 | 未知 | 未知 | 未评估 | 未评估 | — |

UBER在读取阶段返回 `INSUFFICIENT_FEATURE_WARMUP`，沿用真实数据缺口，未补数据。其余7票各有60日逐日结果并评估至2026-09-04。真实样本结果是观察输出，不代表原Pool动作改变、策略盈利有效、期权可用或允许交易。

验收脚本实际核对：8票唯一齐全（7结果+1明确失败）；`EntryOpportunity` typed回读；artifact全部已登记hash；JSON/AI/中文/CSV共同字段和逐日/条件/转换明细内容一致；NVDA独立API与批量的episodes、timeline、transitions和result_id一致；保存NVDA checkpoint恢复后相同；读取前后canonical manifest与14个文件hash一致。manifest identity为 `5af72f892609519c0293b922f8515b3abdeb14683b904e7922a30501683c6400`，manifest SHA256为 `e1045bf72a80265f6d90a597655d032bdf2c7dcc9eab970c5536b29ab5b9537d`。NVDA result_id为 `sha256:479d5c52d36fbb73da7802cd4f1a3e7ff83f7eaa25a878eeb0703d0312c6c0ee`。

真实总验收状态为PARTIAL，仅因UBER暖机不足；组件和其余合法样本继续完成。本步未做收益研究、正式策略采用、期权或实盘验证。下一步若获独立任务授权，可在同一状态机接浅回调；本分支停在第4步边界。
