# NVDA Lifecycle Observability Errors

记录日期：2026-08-20

本文件记录当前 NVDA lifecycle observability 的数据与基础设施问题。不得据此修改生产策略、entry rules、stop rules、profit targets、Safe Strike、candidate population 或数据路由。

## ERROR-001 — 生命周期期权报价覆盖不足

- Required lifecycle rows: 31,549
- Valid executable spread marks: 21,564
- Coverage: 68.35%
- Source-gap rows: 9,985
- 当前 audit 中无法完整观察的 candidates: 458

需要逐行区分：

- `SOURCE_NOT_COVERED`
- `EXACT_CONTRACT_ABSENT`
- `INVALID_QUOTE_ROW`
- `TRUE_SOURCE_GAP`
- `PARTITION_READ_FAILURE`

不得将未处理分区直接标记为 `TRUE_SOURCE_GAP`。

## ERROR-002 — 权威 replay 与 resolved view 的报价覆盖不一致

当前 observable parity：

- Fully observable trades: 368
- Exact parity: 343
- Mismatches: 25

25 个 mismatch 当前归类为 `QUOTE_SOURCE_DIFFERENCE`。解析后的研究 view 保留了后续有效 quote marks，而历史权威 replay 在相同 20-calendar-day window 内更早结束。

需要确认：

1. `nvda_v2_v2_replay.parquet` 使用的实际 loader。
2. 权威 replay 与 resolved view 的物理 source、partition 和 coverage boundary。
3. 差异是否来自旧 source、旧分区缺失、旧 dedupe 或旧 artifact 生成过程。
4. canonical source boundary 由 contract owner 决定。

不得为强制 parity 修改 stop、profit、expiration 或 mark 规则。

## ERROR-003 — Exit parity 尚未完成全量验证

当前 parity 只覆盖完全可观测交易，不能表述为 826/826 全量通过。

仍需验证：

- exit date
- exit reason
- realized P&L
- missing-quote handling
- time-exit handling
- expiration handling

## ERROR-004 — 日级 regime history 缺失

状态：`NVDA_REGIME_HISTORY_BLOCKED_BY_CANONICAL_MARKET_INPUTS`

当前只有 entry-level regime，缺少 candidate-day 的 canonical regime inputs、producer version 和 PIT 记录。因此无法可靠区分：

- `ENTERED_R3`
- `DETERIORATED_TO_R3`
- `first_R3_date`
- deterioration-to-stop timing

不得用替代 regime model 填补。

## ERROR-005 — Post-exit observability 尚未单独完成

尚未形成历史 exit 到 expiration 的独立 exact quote coverage 报告。后续数据必须标记为 `POST_EXIT_RESEARCH_ONLY`，不得影响既有 exit 或 realized P&L。

## ERROR-006 — Resolved view 范围有限

当前 isolated duplicate-resolved view 主要覆盖 NVDA lifecycle 所需 exact contracts，不应视为完整 NVDA 全历史 normalized options store。

若要扩展为通用研究 source，需要补充：

- 全量 canonical-key uniqueness validation
- 完整 conflict-resolution provenance
- deterministic rerun hash
- partition-level row-count reconciliation

## 当前状态

`NVDA LIFECYCLE QUOTE OBSERVABILITY PARTIAL`

以上问题均属于 research/data infrastructure。当前没有生产变更。

## ERROR-007 — AMD canonical regime research blocked by source coverage

状态：`AMD_REGIME_HYPOTHESIS=UNRESOLVED_BY_DATA`

AMD 的 canonical `MarketRegimeEngine` 历史重建无法完成，因为权威 PIT 历史
输入中缺少：

- VIX
- market breadth

现有可用输入：

- QQQ
- SPY
- SOXX

验证结果：

- frozen candidate identity parity：474/474
- PIT lifecycle dates：682
- future leakage：0
- canonical regime rows：0
- status：`SOURCE_COVERAGE_LIMIT`

不得使用 year labels、AMD-only trend、简化 proxy 或手工 regime 分类填补。
不得使用 `MarketState` 默认值伪造历史输入。当前 regime hypothesis 不得标记
为 `SUPPORTED`、`REJECTED` 或 `REGIME_EDGE_FOUND`。

AMD 当前研究状态：

- Standard PCS：`RESEARCH — NO ROBUST EDGE CURRENTLY ESTABLISHED`
- Profit target：`40% ROBUST_TARGET, BUT NEGATIVE STRATEGY`
- Stop research：`PARTIAL — MIXED TEMPORARY + STRUCTURAL FAILURES`
- Entry profile：`NO ROBUST ENTRY PROFILE EDGE`
- Dynamic profit protection：`NOT PRIMARY REPAIR PATH`
- Regime research：`BLOCKED — missing authoritative VIX + market breadth`

当前结论：

- profit-target research 未使 AMD 转为正收益；
- weak-support + trend-A 是风险标记，但不是稳健 standalone entry filter；
- 删除该组合没有使 AMD 转为正收益；
- stop reconstruction 显示 temporary volatility stops 与 genuine structural
  failures 并存；
- prior-profit watermark：40% 为 14.6%，50% 为 2.8%，60% 以上为 0%；
- dynamic profit protection 目前不是主要修复路径。

处理：AMD research branch parked，等待权威 PIT VIX 与 market breadth 数据。未
修改生产规则，未新增 AMD 参数 sweep。

## ERROR-008 — Ticker Bear-State Rule is not a universal PCS block

状态：`RESEARCH_ONLY — NO_PRODUCTION_BLOCK`

新建的 PIT-safe ticker bear-state classifier uses only daily OHLCV and defines
`BEAR_CONFIRMED` as all of the following for at least five consecutive trading
days:

- close below SMA200;
- SMA50 below SMA200;
- drawdown from rolling 52-week high at least 20%.

The frozen-population validation for NVDA, AMD, TSLA, and AMZN does **not**
show that `BEAR_CONFIRMED` consistently identifies materially worse PCS
outcomes. TSLA bear-confirmed outcomes were worse, but AMD and AMZN
bear-confirmed cohorts were historically positive. Therefore this state must
not automatically block PCS production entries.

### AMZN 2025 limitation

AMZN had a bear-market-class drawdown in 2025, but the strict five-day
confirmation arrives after the first days of the decline. The damaging AMZN
2025 cohort was mostly classified as `NORMAL` before confirmation; the small
confirmed cohort had four trades and was profitable. This is confirmation lag,
not evidence that the early down-path was safe.

Required follow-up:

1. Treat `BEAR_CONFIRMED` as a research observation, not a production rule.
2. Do not reinterpret higher credit/ATR as protection during a bear down-path;
   it may be compensation for elevated risk.
3. Any earlier warning state or production exclusion requires separate PIT
   validation, frozen-population testing, and contract-owner approval.

Evidence: `research_outputs/ticker_bear_state_research/`.
