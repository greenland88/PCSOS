# 第3步：可验证支撑区域

## 边界与版本

- 基线：`fd5cecbcc9f5d44f36400b512abc45a36c89050e`。
- 分支：`codex/selection-v2-step-03`；工作区：`H:/workspace/PCSOS-selection-v2-step-03`。
- 公共接口：`pcs.trend.support_zones.evaluate_support_zones(input: SupportZoneInput) -> SupportZoneResult`。
- schema `1.0`；算法 `support-zones-v1`；policy `support-zones-research-v1`。
- 本步只生成研究/描述证据。旧 `trend.support.SupportResult`、扫描、评分、交易规则及第1、2步成果不变。

核心函数仅消费准备好的 `SupportFeatureView`、显式policy与可选 `SupportZoneState`，不读路径、供应商、扫描器、benchmark、期权或账户。可信读取适配器、视图和保存位于 `pcs.pool.support_zones`。

## 输入与指标身份

canonical适配器先用 `ProfileDataReader` 的 PCSDataAccess、active verified daily handle、ManifestSnapshot及前后文件hash检查，读取60日分析窗口和200日生产指标暖机。随后一次调用现有 `calculate_base_indicators()` 得到TA-Lib SMA20、SMA50、ATR14；一次调用现有 `analyze_market_structure()` 得到3/3 confirmed swings。指标起点、参数、输入generation/checksum和 `indicator_identity` 均落盘。

分析日列表来自XNYS交易所日历。adapter附加未来3个session只用于明确测试截止日，不读取未来K线。核心先按有效行情日截断；未来bar不会参与过去结果。
缺benchmark和期权与本组件无关。SMA20或SMA50单项缺失时，另一来源和confirmed swing仍可建区；缺OHLC/ATR只停止其后依赖这些字段的状态推进。

## 固定区域和来源

每个session先更新已经可知的区域，再处理该日收盘后可知的新来源。候选为当日SMA20、SMA50和该日刚完成确认的摆动低点。pivot `formed_at` 保留转折日，`available_at` 为右侧第3根完成日；此前交叉只写 `RETROSPECTIVE_INTERSECTION`，不生成测试。

候选按价格、source_id稳定排序。一次聚类从组内最低价起算，只有最高价减最低价不超过 `0.35 * formation ATR` 才合并，因此不存在按移动代表值连续扩张的链式合并。区域锚点是组内最低/最高候选的中点，总宽度固定 `0.35 ATR`，上下各 `0.175 ATR`。区域ID绑定symbol、锚点、冻结ATR、形成/可知日、创建来源、算法、价格口径和公司行动版本。

后续均线落在既有区域内只记录来源共振，不移动边界、建区ATR或失效线，也不增加价格测试次数。来源移出已有区域时可形成新区域；被替代且从未触发测试的未绑定纯均线参考区转入归档，已有测试、已绑定或摆动低点/共振区域继续按固定边界更新。旧区域及其全部事件始终保留。区域总类为MA_REFERENCE、SWING_LOW或CONFLUENCE。

## 测试、失效和选择

区域可知后的完成K线首次与上下沿相交时启动测试。触及日只记 `IN_PROGRESS`；连续触及持续更新同一test的累计低点。触及后的第1至第3个交易日内同时满足 `close >= upper` 与 `close - cumulative_low >= 0.5 * formation ATR` 时，首次记录HELD。第3日有效，第4日才满足则写UNCONFIRMED_TEST；它不会单独将区域改成BROKEN。

一次测试结束后，先有更晚完成K线 `close >= upper + 0.5 * formation ATR`，再由再后一根相交K线开始下一测试。同一K线不会同时证明离开及重触。每次测试保留反弹ATR、穿透ATR、累计低点、首次确认、截止和离开日期。

固定失效线为 `lower - 0.35 * formation ATR`。日内low低于该线单独写INTRADAY_PENETRATION；只有close严格低于固定线才BROKEN。过去的HELD测试不会被删除。状态值为REFERENCE_ONLY、TEST_IN_PROGRESS、SINGLE_HELD_TEST、REPEATED_HELD_TESTS、BROKEN；证据等级另存，次数不替代幅度明细。

输出分别选择最近观察支撑、候选关键支撑和已绑定支撑。候选关键支撑先排BROKEN，再按HELD次数、可知时间和距现价排序。没有机会绑定输入时BOUND_SUPPORT为UNBOUND。未选区域及原因完整保留。

## 状态、身份和缺日

`SupportZoneState` 保存完整区域、历史、`evaluated_through`、revision、合法前缀hash、source/policy/indicator/价格身份。续算先复核全部身份及截至旧session的bar hash；不兼容则从当前合法前缀重放，并返回PRIOR_STATE_INVALIDATED_REPLAYED。原保存状态不被覆盖。

遇到中间交易日缺失即停在最后已知session，返回INTERMEDIATE_DAILY_SESSION_MISSING；不会跳过未知日补造HELD/BROKEN。相同输入重复调用不会增加区域或测试。`run_id`、`request_id`、`received_at`和本次state_changes不参与最终业务 `result_id`；有效行情日、完整业务状态、policy、指标身份和canonical来源身份继续参与。

## 输出与独立查询

输出目录必须为空，采用原子写入并保存各文件SHA256：

- `support_zone_results.json`：完整typed结果；
- `support_zones.json`、`support_tests.json`、`support_history.json`：可按实体独立查询的明细；
- `support_states.json`：可校验恢复状态；
- `support_zones.ai.json`、`support_zones.zh-CN.md`：从同一结果生成；
- 输入/结果schema、字段字典、读取审计和artifact manifest。

Python核心TEST示例：

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-03
$env:PYTHONPATH='src'
python examples/support_zones.py --fixture
```

单票canonical公开API示例：

```powershell
Set-Location H:/workspace/PCSOS
$env:PYTHONPATH='H:/workspace/PCSOS-selection-v2-step-03/src'
python H:/workspace/PCSOS-selection-v2-step-03/examples/support_zones.py --symbol NVDA --as-of 2026-09-04
```

批量只读入口及验收：

```powershell
python -m pcs.cli support-zones --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --as-of 2026-09-04 --run-id step_03_acceptance_20260904 --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_03_acceptance_20260904
python H:/workspace/PCSOS-selection-v2-step-03/scripts/accept_support_zones.py H:/workspace/PCSOS/selection_v2_outputs/step_03_acceptance_20260904
```

`find_support_zone()`、`find_support_test()`按稳定ID查询；`load_support_zone_state()`先验证state文件hash再恢复typed状态。

## 专项验证

```powershell
python -m pytest tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py -q
git diff --check
```

覆盖未确认pivot、真实known_at、单来源、均线共振、非链式聚类、触及日限制、连续触及去重、第3/4日边界、离开后重触、固定失效线、盘中/收盘跌破分离、历史HELD保留、未来隔离、批量/续算/重复等价、来源修正重放、缺日停止、绑定角色、调用时间身份及结构化输出。旧support和market structure测试原样运行。

## 真实验收

待实现提交后使用干净源码执行，将固定日期、读取身份、逐票区域/测试/缺口、视图/状态/来源hash对账和最终提交补记在此。
