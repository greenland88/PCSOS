# 第4步：机会状态机与健康回调

## 当前交接：R1–R3 限定续修，待复核

复核基线 `c4d235e2ef0e33678858d4615f6e9d9a4871d131`；本次源码提交
`7373f1e3d1354a5e75a3f56b8f650639c7d33b9b`，原功能分支工作树起始干净。
下方旧真实验收保留为历史证据，第4步整体仍待本轮复核，未合并main。

本轮仅修改 `trend/opportunity_state.py`、必要typed模型、`pool/opportunities.py`
输出适配及对应测试。未改变检测器、交易阈值、支撑计算、生产判定或第5步。

| 项目 | 修复及实证 |
|---|---|
| R1 缺日补齐 | 仅继承 `evaluated_through` 以内的timeline、detections、transitions。边界外旧缺日报告留在旧产物；补齐后每日期唯一，timeline、转换、状态、缺项和result_id与同范围冷计算一致。 |
| R2 连续推进 | checkpoint原分析起点用于业务覆盖，展示起点单独记录。实测从索引26恢复至30、展示从29起时，实际检测27、28、29、30，保留27的首次确认。缺27则在27停步、revision不增加。 |
| R2 同日幂等 | 提供已校验完整明细时执行日期为空，状态、适用性及result_id不变；窄范围仅checkpoint返回 `OPPORTUNITY_PRIOR_DETAILS_REQUIRED`。完整前缀仍支持合法重放；历史修正缺前缀保护保留。 |
| R3 缺项汇总 | 发现未决时 `TREND_HEALTH_QUALIFIED` 列入当前缺项；历史真实未知保留日期/角色/原因。明确false仍为NO_SETUP/false，其他未知只列覆盖说明；诊断RSI不列阻断缺项。四种视图内容一致。 |

输出schema为 `1.1`，算法为 `entry-opportunity-v2.1`；模型仍接受已保存的
schema `1.0` / `entry-opportunity-v2`。新增字段均有空列表默认值，旧产物缺少字段
不代表此前完成过该检查。旧文件和ID保持原样，新算法写入result_id计算身份。
经济episode和机会定义没有改变，沿用原policy及机会身份；历史v2有效checkpoint
可复用，但本次结果ID与旧算法版本不要求相同。同一新算法下恢复与冷算ID必须一致。

- `coverage.expected_sessions` / `analysis_start`：完整业务分析范围。
- `coverage.display_sessions`：请求展示范围；完整timeline仍保留供复用。
- `coverage.processed_sessions`：本次实际尝试的日期，含遇到的首个缺日；不参与业务ID。
- `missing_evidence`、`current_missing_details`：当前判断缺项。
- `coverage_missing_evidence`：完整时间线中非诊断未知，包含日期、condition_id、role、reason_codes、affected_outputs。只影响coverage的未知不改变明确false。

能力原因从本次有效时间线重建，历史真实未知不会被统一改写为
`REQUIRED_DISCOVERY_EVIDENCE_UNKNOWN`。冷计算的业务状态、episode与判定条件保持原行为；
变化限于汇总解释、覆盖记录及版本身份。恢复路径则修复附件所述错误判断。

### 实际验证

先将附件反例改为正常回归断言，在旧源码上执行以下命令得到4项预期失败，分别证明
重复日期、跳过确认、同日状态消失、当前缺项为空：

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-04
python -m pytest tests/trend/test_entry_opportunity_v2.py -q -k 'repaired_gap or later_display or same_session_checkpoint or unknown_discovery'
```

修复后最终代码执行：

```powershell
python -m pytest tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_pullback.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py -q
git diff --check
```

结果 `99 passed in 7.11s`；其中第4步36项（本轮新增8项），其余为原受影响专项。
还验证了实际检测调用日期、历史未知与已解决缺口的区别、缺项多视图、旧明细loader。
测试使用显式TEST夹具的既有session序列，不宣称该序列是实际XNYS交易日。
原有CURRENT_EOD测试随专项运行，本轮没有新增供应商或当前行情验收。

### 旧真实产物复用边界

保留目录 `H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_aaa5279_20260904`。
本轮只校验其13个manifest登记文件hash、通过现有 `load_opportunity_resume_evidence()`
加载七票typed明细（各60行，checkpoint截至2026-09-04）。产物manifest SHA256：
`a0b1bbbb7e4cc0bb34ff36c61ca39bde7a9953d196fa3a4f3186d0c1c32de2df`。
来源计算提交仍为 `aaa52798c6830c2a0d4058d207927d80ff0e5085`，未改标成本次提交。

复现此项只读校验：

```powershell
$env:PYTHONPATH='src'
@'
import hashlib, json
from pathlib import Path
from pcs.pool.opportunities import load_opportunity_resume_evidence
root = Path('H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_aaa5279_20260904')
raw = (root/'artifact_manifest.json').read_bytes()
manifest = json.loads(raw)
for name, digest in manifest['sha256'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest() == digest
for symbol in ['NVDA','PLTR','MSFT','HOOD','MDLZ','AAL','AAOI']:
    evidence = load_opportunity_resume_evidence(root, symbol)
    assert len(evidence['prior_timeline']) == 60
    assert evidence['prior_state'].evaluated_through == '2026-09-04'
assert raw == (root/'artifact_manifest.json').read_bytes()
print('13 hashes / 7 typed saved results PASS')
'@ | python -
```

本轮canonical读取次数和真实股票核心重算次数均为0。之前16个canonical文件前后hash
一致、七票业务判断、UBER暖机缺口，均只沿用原交接证据；未在新提交重新验收其行情、
支撑或真实续算。恢复正确性由确定输入回归证明，旧产物检查只证明保存文件完整和typed
读取兼容性。没有请求期权、扫描全池或收益研究。

## 边界与版本

- 基线：`3b4d674ec418106e5ba24cd7a52204a6199084a8`，包含已验收第3步及计划v1.7。
- 分支：`codex/selection-v2-step-04`。
- 最终计算源码提交：`f48ec980978fb168b14efb4023e5d283bd61e436`（含前置实现提交 `e0452ab9d8dbc08ef469162a7d5c233e1d0bad6c`、证据/身份补强 `ca259923b4f4ddce7ef2d7c55100adc9340b3f36`、失效证据角色修复 `8865306745a138126bc75ecd1c023a60d5a069a5`及缺口日历/来源/事件/经济身份/revision完整性修复）。
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

区域上下沿、formation ATR和失效线在setup冻结，后续均线或ATR不移动它们。终态事件不会因重扫或反弹回到活跃态；只有新的独立support test可开始新机会。`economic_episode_id`只绑定symbol和实际touch session，不因family、run、policy、算法或zone ID版本变化制造新市场事件；`opportunity_id`另绑定family、实际policy、指标、来源、zone/test及固定参数；`result_id`再绑定有效输入范围和业务过程。run_id、request_id、received_at、恢复方式及调用诊断不进入语义ID。

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
python -m pcs.cli entry-opportunity --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --as-of 2026-09-04 --run-id step_04_acceptance_f48ec98_20260904 --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_f48ec98_20260904
python H:/workspace/PCSOS-selection-v2-step-04/scripts/accept_entry_opportunities.py H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_f48ec98_20260904
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

最终产物：`H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_f48ec98_20260904`。产物manifest绑定干净源码 `f48ec980978fb168b14efb4023e5d283bd61e436`。

| 股票 | 结果 | 9月4日状态 | 当前可评估 | 当前事件触及/确认 | 原窗口 | 事件/转换数 |
|---|---|---|---|---|---|---:|
| NVDA | COMPLETED | ENTRY_READY | true | 09-01 / 09-02 | 09-03—09-08 | 4 / 22 |
| PLTR | COMPLETED | ENTRY_READY | true | 09-02 / 09-03 | 09-04—09-09 | 2 / 5 |
| MSFT | COMPLETED | EXPIRED | false | 07-23 / 未确认 | 不适用 | 2 / 6 |
| HOOD | COMPLETED | ENTRY_READY | false（当前超距） | 08-31 / 09-02 | 09-03—09-08 | 3 / 9 |
| MDLZ | COMPLETED | ENTRY_READY | true | 09-01 / 09-02 | 09-03—09-08 | 5 / 19 |
| AAL | COMPLETED | INVALIDATED | false | 07-08 / 未确认 | 不适用 | 1 / 3 |
| AAOI | COMPLETED | INVALIDATED | false | 08-13 / 08-14 | 08-17—08-19 | 1 / 5 |
| UBER | 未生成 | 未知 | 未知 | 未评估 | 未评估 | — |

UBER在读取阶段返回 `INSUFFICIENT_FEATURE_WARMUP`，沿用真实数据缺口，未补数据。其余7票各有60日逐日结果并评估至2026-09-04。真实样本结果是观察输出，不代表原Pool动作改变、策略盈利有效、期权可用或允许交易。

验收脚本实际核对：8票唯一齐全（7结果+1明确失败）；`EntryOpportunity` typed回读；artifact全部已登记hash；JSON/AI/中文/CSV共同字段和逐日/条件/转换明细内容一致；NVDA独立API与批量的episodes、timeline、transitions和result_id一致；保存NVDA checkpoint恢复后相同；读取前后canonical manifest与14个文件hash一致。manifest identity为 `5af72f892609519c0293b922f8515b3abdeb14683b904e7922a30501683c6400`，manifest SHA256为 `e1045bf72a80265f6d90a597655d032bdf2c7dcc9eab970c5536b29ab5b9537d`。NVDA result_id为 `sha256:a799129ee2cb2e588182728989e4d7519ea56786c4c93756d6e06d3628d051c1`。

真实总验收状态为PARTIAL，仅因UBER暖机不足；组件和其余合法样本继续完成。本步未做收益研究、正式策略采用、期权或实盘验证。下一步若获独立任务授权，可在同一状态机接浅回调；本分支停在第4步边界。

## 2026-09-08 限定复核修复（F1–F4）

本节取代上文中关于趋势健康来源、请求时点、恢复方式和最终真实样本状态的旧验收描述；旧产物保留，不覆盖。计算源码提交为 `aaa52798c6830c2a0d4058d207927d80ff0e5085`。

- F1：发现、确认和窗口复核均明确要求 `bullish` 结构及 `strong/healthy` 健康度，并消费现有 `interpret_trend()` 的真实健康度和 market-structure-engine 的短期阶段。`FAILED_FOLLOW_THROUGH` 等现有明确阶段会阻断；不再以“不是 bearish/BLOCKED”放行。支撑仍为 support-zones-v2。逐日证据同时保存旧 trend gate 与 pullback gate 的真实执行状态、结果、原因和生产者引用；benchmark 缺失只使依赖它的健康判断未知。
- F2：`HISTORICAL` 与 `CURRENT_EOD` 分开。CURRENT_EOD 要求带时区的请求时间，并由 XNYS 日历解析最近已完成 session；请求在固定窗口之后返回 false，窗口内但缺少该请求 session 的事实返回 null。请求 session/语义进入结果身份；run、request 和 received 时间仍不进入。
- F3：兼容 checkpoint 从 `evaluated_through` 后的 session 继续，episode、固定窗口和 revision 不重置；完整旧逐日证据通过 `load_opportunity_resume_evidence()` 从已校验产物读取，不塞入小 checkpoint。修正前缀可完整重放；滑动输入缺少所需修正前缀时以 `OPPORTUNITY_REPLAY_PREFIX_REQUIRED` 停止。
- F4：三值发现逻辑中，明确 false 可得 NO_SETUP/false；只有其余必需条件成立而证据缺失时才是 null/PARTIAL。RSI 等 DIAGNOSTIC 缺失不阻止确认；已有状态遇到未知输入保留最后合法事件，不伪造 NO_SETUP。

限定测试命令：

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_pullback.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py -q
```

实际结果：`91 passed in 7.48s`。覆盖三个F1反例、正常healthy、明确阶段阻断、CURRENT_EOD日历请求、必需/可选缺失、真实增量恢复、完整回放等价和缺前缀拒绝。

新的隔离真实产物为 `H:/workspace/PCSOS/selection_v2_outputs/step_04_acceptance_aaa5279_20260904`，manifest绑定上述干净提交。来源manifest及16个实际读取文件前后hash一致；typed回读、产物hash、四种共同视图、明细引用、NVDA独立调用及保存状态复核全部通过。总状态为PARTIAL：UBER因 `INSUFFICIENT_FEATURE_WARMUP` 未生成；NVDA、MSFT、MDLZ和AAL还保留各自历史早期必需趋势证据未知，未伪造为已知。

| 股票 | 能力 | 2026-09-04状态 | 请求时适用 | 触及/确认 | 固定入场窗口 |
|---|---|---|---|---|---|
| NVDA | PARTIAL | EXPIRED | false | 08-24 / 08-25 | 08-26—08-28 |
| PLTR | COMPLETED | NO_SETUP | false | 未发生 / 未发生 | 不适用 |
| MSFT | PARTIAL | NO_SETUP | false | 未发生 / 未发生 | 不适用 |
| HOOD | COMPLETED | ENTRY_READY | false | 08-31 / 09-02 | 09-03—09-08 |
| MDLZ | PARTIAL | ENTRY_READY | true | 当前事件见结构化报告 | 当前事件见结构化报告 |
| AAL | PARTIAL | NO_SETUP | false | 未发生 / 未发生 | 不适用 |
| AAOI | COMPLETED | NO_SETUP | false | 未发生 / 未发生 | 不适用 |
| UBER | 未生成 | 未知 | 未知 | 未评估 | 未评估 |

NVDA在08-24发现时结构为bullish、真实健康度healthy、阶段HEALTHY_PULLBACK；08-25确认时三项仍合格且新支撑为HELD。旧trend gate为PASS，旧pullback gate为WAIT；这证明新区域证据没有被旧weak support替代。该窗口已于08-28结束，所以09-04为EXPIRED/false。PLTR在本次真实重算中没有合法v2触及或确认；09-04原pullback分类为healthy_pullback，但真实趋势健康为mixed、旧trend gate为WATCH、旧pullback gate为WAIT，因此结果为NO_SETUP/false，不能沿用旧报告的ENTRY_READY。
