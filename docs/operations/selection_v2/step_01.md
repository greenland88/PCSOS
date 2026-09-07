# Selection v1.4 第1步交接：逐票解释、评分来源与入口差异

## 边界与版本

- 问题：旧证据视图不能把逐票事实、缺失字段、评分预设、实际执行状态和入口实现差异清楚分开。
- 允许修改：公共分析契约、`pool/ai_evidence.py`、`pool-evidence` 只读导出、聚焦测试和能力登记。
- 停止条件：八票以内的保存结果可由独立 Python API 解释并生成 JSON、CSV、中文 Markdown 和 AI 视图；不改变任何原决策。
- 主分支核对：`main` 与 `origin/main` 均为 `a463e4698a52f1ea0d49635d488c2e322d4f1670`，与计划核对版本一致，未回退历史版本。
- 前置开发提交：`5e910fe`（计划文档；当前分支同时保留其前置的已提交网关工作，不在本步改动）。
- 独立工作分支：`codex/selection-v2-step-01`。
- 组件实现提交：`1505a60`（`feat: add selection explanation evidence component`）。
- 本文件的交接提交：见本文件所在的后续提交；第1步的可独立回退实现边界是 `1505a60` 加本交接提交。
- 2026-09-07 复核修复提交：`9a356e8`（`fix: correct selection explanation execution evidence`）。
- 本次追加复现修复开始时 HEAD：`be32eb6`；修复提交：`e0b1aea`（`fix: require evidence for selection explanation stages`）。

本步没有执行 Pool Scan、数据导入、供应商请求、账户读取、收益研究或策略修改。来源 run 和其 checkpoint 均未改写。

## 组件与调用边界

- Typed 核心：`pcs.pool.ai_evidence.explain_selection(input: ExplanationInput) -> SelectionExplanation`。
- 输入/输出模型：`pcs.analysis_contracts.ExplanationInput`、`SelectionExplanation`，使用 Pydantic，有限来源状态为 `OBSERVED`、`DERIVED`、`CONFIGURED_ASSUMPTION`、`MISSING`、`NOT_RECORDED`；执行状态单独保存。
- 可信运行产物适配器：`load_selection_explanation_inputs()`。它先用现有 `_load_pool_run` 验证整个固定 run，再一次遍历 AI JSONL 读取所需股票；核心函数本身不读路径、不调用 scanner、账户或 provider。
- 批量入口：`build_selection_explanations()`；同一份 typed 结果由确定性适配器生成四种视图。
- CLI：`python -m pcs.cli pool-evidence ... --explain-selection`。此模式要求隔离 `--output-directory`，并拒绝与 `--upgrade` 同时使用。
- 能力登记：`docs/PCS_CAPABILITY_ROADMAP.md` 的 `Selection explanation v1.4` 条目，状态仅为第1步完成。

独立 Python 单票调用：

```powershell
$env:PYTHONPATH='src'; python -c "from pcs.pool.ai_evidence import load_selection_explanation_inputs,explain_selection; p=r'pool_scan_runs/main_6.0_reproduction_20260906/58363ffc66a246c185f429f3d878b3cc'; i=load_selection_explanation_inputs(p,['NVDA'],request_id='manual-nvda')[0]; print(explain_selection(i).model_dump_json(indent=2))"
```

核心也可以直接接收调用方已经准备并验证的 `ExplanationInput`；该路径不要求运行目录。

## 输入身份与复用证据

- 实际目录：`H:/workspace/PCSOS/pool_scan_runs/main_6.0_reproduction_20260906/58363ffc66a246c185f429f3d878b3cc/`。
- run ID：`58363ffc66a246c185f429f3d878b3cc`；`current=true`；`DAILY_TIMING=COMPLETE`；2,953 条 ticker result。
- manifest SHA-256：`2d927f244e57c3aa576ede819cfc9e83e50e50437713efbb4188a42e97f9cae9`。
- `daily_timing.json`：`ef844a7458eda3a619fcf0ea0cd9872fda435aa85cc074433232b96e37e365b7`。
- `ai_evidence_packets.jsonl`：`0db4c52e895940224d614c32f8dbcd2aad6d2ca43ddd3e35ed75ee67eb5641fe`。
- `ai_evidence_index.json`：`5c3d966ace118c318acd50c5fed4f916313d30fc5938ecfc2ac2c846193c6383`。
- universe identity：`global_pcs_candidates:c2ede1f139e783e3-fd5a6552aff369df:GLOBAL_CANDIDATE_UNIVERSE:2953:fd5a6552aff369df7709de84b089631cecdd70c3a34a269b005314dca502bd56`。
- 请求时刻：`2026-09-04T16:01:00-04:00`；有效已完成日线 session：`2026-09-04`；mode：`EOD`。
- 现有加载器对 manifest 内20个产物逐一校验，结果为有效。上述历史证据只支持该来源 run 的解释，不代表今天的行情或交易许可。

样本为 NVDA、PLTR、MSFT、HOOD、UBER、MDLZ，以及旧规则拒绝对照 AAL、AAOI，共八只。两只对照的固定规则是：在查看任何后续收益前，从 `eligibility_status=PCS_ELIGIBLE` 且 `final_action=REJECTED` 的保存结果中，排除指定六票后按 symbol 字典序取前两只。

## 实际实现语义

每票保留基础资格、metadata 状态、日线日期、结构、趋势健康、短期阶段、回调深度、支撑、确认、旧 timing、期权阶段、主次原因、下一条件、来源引用、实际 policy 和详细规则记录。

- `PCS_ELIGIBLE` 仅显示“基础筛选通过”。来源 run 未保存 instrument type、行业、业务质量证据、静态流动性证据和事件明细，因此 `static_metadata_status=UNKNOWN`。
- 旧产物没有独立 `trend_health` 和 `pullback_depth_atr` 字段；即使 reason code 含类似文字，也标为 `NOT_RECORDED`，不从文字补数。
- `_candidate()` 的 `business_quality=80`、`support_score=0`、`price_confirmation=0`、`sector_alignment=80` 均记录为 `CONFIGURED_ASSUMPTION`。80不是实测质量好，0不是实测支撑/确认差。
- `business_quality`、`support_score`、`price_confirmation` 只有在 DecisionEngine 评分路径实际运行时才分别进入质量、支撑、趋势分；`sector_alignment` 被填入 TradeCandidate，但当前 ScoreBreakdown/OpportunityScorer 不消费。
- `iv_premium=min(100, credit / max(short_strike-long_strike, 1) * 500)` 的真实语义是信用/宽度衍生分，不是真实IV premium。当前配置的0.08仅作参考默认；本来源 run 没有保存实际评分权重、该评分或真实IV评分诊断。
- 本八票均未留下 DecisionEngine 评分执行结果，因此 `score_validity=NOT_EVALUATED`，不生成总分或分项得分。
- 入口差异记录为实现差异而非多数表决：生产快照 pivot 3/3、TA-Lib ATR14、SMA200；旧 opportunity replay 使用 pivot 2/2、rolling true-range ATR14、EMA200；`market_context` 的 phase 直接映射与 Pool Scan 的 trend gate + pullback gate 不同。来源 run 只实际执行 Pool 路径，替代入口逐票结果标 `NOT_RECORDED`/`ALTERNATE_RESULTS_NOT_RECORDED`。
- `result_id` 只绑定语义输入、规则和内容哈希，不含 request ID、计算墙钟或物理复制路径；视图变化不会重算业务结果。

### 2026-09-07 复核缺陷与修正语义

本次复核只修正解释层的五项缺陷，不改变任何原判定、评分权重或策略参数：

1. DecisionEngine 调用、硬门槛检查和实际评分分开记录。硬门槛提前拒绝时，决策对象中的零分返回形状不是实际评分，质量、支撑、确认和 `iv_premium` 等评分输入不标为已消费。正常评分时只从保存的 `selection_result.decision.scores` 读取实际分项，`iv_premium` 不为 `null`。
2. 期权完成状态只使用现有 `OptionsStatus`，但不仅凭顶层状态推断子阶段。`PASS` 只在有明确 selector/selection 证据时才是正式合约评估通过；`REJECT` 可能是零价差后只完成发现，也可能是 selector 已执行后的正式拒绝；`DISCOVERED` 是只完成发现；`DATA_BLOCKED` 保留实际阻断层次；`NOT_EVALUATED` 为未执行。
3. 缺失 `timing_status` 是 `NOT_RECORDED` 且执行状态为 `NOT_EVALUATED`。空 `source_references` 时 `source_artifacts_hash_validated=false`，并记录 `SOURCE_REFERENCES_NOT_RECORDED`。
4. 解释入口校验 `requested_as_of`、context/ticker 的 `effective_daily_session`、ticker `as_of` 和 `feature_max_date`。请求与来源 run 时刻不一致、有效日线 session 晚于请求、或 feature 证据来自未来时，均以稳定 `EXPLANATION_*` `ValueError` reason code 失败关闭；日期比较不使用本机当前日期。
5. 生产 pivot “实际值”从保存的 `candidate_state.applicable_rules`/`effective_policy` 读取，3/3 只作 `TrendIndicatorConfig` 参考默认；保存 5/5 时报告必须显示 5/5。`trend_health` 只从 AI evidence、ticker result 或 candidate state 的明确结构化字段读取，没有才是 `NOT_RECORDED`。

中文 Markdown 视图与结构化输出使用同一对象，现在分别展示 options stage execution/completed evaluation kind、DecisionEngine invocation、hard-gate checks/outcome 和 actual scoring，不再用单一布尔值概括多层执行语义。

### 2026-09-07 追加复现的四项解释修正

1. 期权子阶段现在同时检查 `spread_count`/`discovered_contracts`、非空 `selection_result`、可选 `contract_selector_invoked` 明细及 selector status。`spread_count=0` 是所有 `TickerScanResult` 的默认形状，单独出现不证明 discovery 已运行：`options_status=NOT_EVALUATED` 时 discovery 仍为 `NOT_EVALUATED`；`options_status=DATA_BLOCKED` 且无 selection/selector 证据时 discovery 为 `BLOCKED`。runner 的真实零价差完成路径是 `options_status=REJECT`、`spread_count=0`、`selection_result=None`、`contract_evaluation_status=REJECT`；该组合只证明发现已完成，正式合约评估为 `NOT_EVALUATED`。顶层 `PASS` 缺少明确 selector/selection 证据时记 `NOT_RECORDED` 和 `OPTIONS_FORMAL_EVALUATION_EVIDENCE_MISSING`，不自动补成正式 PASS。
2. `trend_health` 按声明的优先级逐项查找，包装对象为 `UNKNOWN`/`MISSING`/`NOT_RECORDED`/`NOT_PROVIDED` 或 `value=null` 时跳过，继续寻找后续明确值；所有候选均未知才输出 `NOT_RECORDED`。
3. `selection_result.decision={}` 是空记录，`decision_record_status=NOT_RECORDED`，DecisionEngine invocation、hard-gate checks 和 actual scoring 全部为 `NOT_EVALUATED`，hard-gate outcome 也是 `NOT_EVALUATED`；不由“没有拒绝”反推 PASS。
4. 实际 `score_weights` 按 `input.effective_policy` → `candidate_state.effective_policy` → `candidate_state.applicable_rules` 的优先级读取，支持 `score_weights`、`scoring.weights`、`values.score_weights` 和 `values.scoring.weights` 形状。例如保存的 `iv_premium=0.20` 原值输出0.20；当前配置0.08单列为 `score_weights_reference_default`，缺少来源 run 权重时实际状态是 `NOT_RECORDED`。程序、AI、中文 Markdown 和 CSV 视图均从同一 `effective_policy` 生成。

## 实际结果与输出

| 股票 | 基础资格 | 旧 timing | 期权 | 原最终动作 | 主要记录原因 | 重要缺口 |
|---|---|---|---|---|---|---|
| NVDA | PCS_ELIGIBLE | WAIT | NOT_EVALUATED | WAIT | waiting_for_qualified_pullback | trend health、pullback depth未记录 |
| PLTR | PCS_ELIGIBLE | WATCH | NOT_EVALUATED | WATCH | trend_gate_not_pass | trend health、pullback depth未记录 |
| MSFT | PCS_ELIGIBLE | WAIT | NOT_EVALUATED | WAIT | waiting_for_qualified_pullback | trend health、pullback depth未记录 |
| HOOD | PCS_ELIGIBLE | WAIT | NOT_EVALUATED | WAIT | waiting_for_qualified_pullback | 支撑值及回调深度未记录 |
| UBER | DATA_BLOCKED | NOT_EVALUATED | NOT_EVALUATED | TEMP_BLOCKED | DAILY_STALE | 股票结构字段均未记录 |
| MDLZ | PCS_ELIGIBLE | TIMING_ENTRY_READY | DATA_BLOCKED | WAIT | OPTIONS_GENERATION_MISSING | 合约评估及评分未执行 |
| AAL | PCS_ELIGIBLE | WAIT | NOT_EVALUATED | REJECTED | trend_gate_reject | 支撑值及回调深度未记录 |
| AAOI | PCS_ELIGIBLE | WAIT | NOT_EVALUATED | REJECTED | trend_gate_reject | trend health、pullback depth未记录 |

隔离输出目录：`H:/workspace/PCSOS/selection_v2_outputs/step_01/58363ffc66a246c185f429f3d878b3cc/`。

- `selection_explanations.json`：完整程序视图。
- `selection_explanations.csv`：逐票摘要。
- `selection_explanations.zh-CN.md`：中文人工视图。
- `selection_explanations.ai.json`：有界 AI 视图。
- `selection_explanation.schema.json`：输入和输出 JSON Schema。
- `field_dictionary.json`：来源状态、执行状态、重要字段和单位字典。
- `manifest.json`：记录数、symbol、result ID 与上述六个文件的哈希。

正常样例使用 NVDA：结构 `STRUCTURAL_UPTREND`、短期阶段 `RECLAIM_CONFIRMED`、最近支撑 `220.08199999999988 USD/股`、支撑距离 `1.3925482042134019 ATR`，同时明确旧 timing 为 WAIT、期权和评分未执行。缺失样例使用 UBER：`DAILY_STALE`，结构/支撑/确认均为 `NOT_RECORDED`，基础资格 gate outcome 为 `UNKNOWN`，没有用0或FAIL代替未知。

## 精确验证命令与结果

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/pool/test_selection_explanation.py tests/pool/test_artifacts.py -q
```

结果：`12 passed in 2.41s`。覆盖 typed core、预设80、缺证据0、未执行评分、缺失输入、false确认不改原动作、request ID不改变result ID、JSON/AI/中文视图同源、批量读取和现有artifact回归。

```powershell
$env:PYTHONPATH='src'; python -m pcs.cli pool-evidence --run-directory pool_scan_runs/main_6.0_reproduction_20260906/58363ffc66a246c185f429f3d878b3cc --explain-selection --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --output-directory selection_v2_outputs/step_01/58363ffc66a246c185f429f3d878b3cc --request-id step01-real-sample
```

结果：`COMPLETED`，8条；未运行 scanner。随后对六个输出逐一重算 SHA-256，并把8条 JSON 重新交给 `SelectionExplanation.model_validate()`，全部通过。

```powershell
$env:PYTHONPATH='src'; python -m pcs.cli pool-evidence --run-directory pool_scan_runs/main_6.0_reproduction_20260906/58363ffc66a246c185f429f3d878b3cc --symbol NVDA
```

结果：原单票 AI evidence 读取仍成功，未执行 `--upgrade`。

`git diff --check` 通过。环境未安装 `ruff`（`No module named ruff`），因此没有把未运行的 lint 写成通过。

### 2026-09-07 复核修复验证

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/pool/test_selection_explanation.py tests/pool/test_artifacts.py tests/pool/test_context_adapters.py -q
```

聚焦范围新增成功评分、硬门槛提前拒绝、五种期权状态、缺失 timing/空来源、请求日期冲突、未来证据、pivot 5/5、已记录 trend health 以及中文视图分层执行状态断言。其中成功评分和 RED 硬门槛拒绝各有一个测试直接调用现有 `DecisionEngine.evaluate_candidate`，再将 `decision.model_dump(mode="json")` 的真实保存形状交给解释器；前者验证读取引擎实际 `iv_premium`，后者验证引擎硬门槛返回的零分形状不算实际评分。提交后最终复核结果：`47 passed in 5.11s`。

只读复用原 run 的 NVDA 做兼容性检查：原动作仍为 `WAIT`，期权状态仍为 `NOT_EVALUATED`，保存的实际 pivot 读为 3/3。没有执行扫描、期权请求、provider probe、canonical 数据运行或原 run 写回。修复提交为 `9a356e8`；交接提交完成后推送同一远端分支，不合入 `main`。

### 2026-09-07 追加复现修复验证

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/pool/test_selection_explanation.py tests/pool/test_artifacts.py tests/pool/test_context_adapters.py -q
```

提交前独立复核结果：`56 passed in 5.16s`。新回归覆盖 runner 零价差 `REJECT` 不误报正式评估、`NOT_EVALUATED + spread_count=0` 不推进 discovery、`DATA_BLOCKED + spread_count=0` 在无 selector/selection 证据时保留 discovery `BLOCKED`、selector 阻断层次、空 decision、早期未知 trend health 后续有效值、全部未知 trend health、实际 `iv_premium=0.20` 权重及缺失权重不回退当前配置。

用户本地复现环境报告的失败发生在 pytest 收集前的 DuckDB 原生依赖崩溃；该结果不是项目测试断言失败，也不能由本工作区的通过代替。本记录不声称用户已重跑或已通过。修复提交为 `e0b1aea`；交接提交完成后推送同一远端分支，不合入 `main`。

## 验收状态与剩余缺口

- 组件实现：完成；typed core、批量可信适配器、CLI和四视图均可调用，不是空接口。
- 历史真实样本：完成；8票来源身份和哈希有效，原动作逐票保持一致。
- 当前实时数据：不属于第1步，也未探测；不影响保存股票证据解释。
- 策略采用：无；规则、权重、门槛和旧决策全部未改。
- 未完成项：旧 run 本身未记录的 trend health、pullback depth、静态metadata、替代入口逐票结果和 DecisionEngine 分数仍保持缺失；MDLZ 的历史期权 generation 缺口和 UBER 的历史日线 stale 缺口没有在本步修复。
- 后续：只有收到下一步明确授权才继续；本提交结束在第1步边界。
