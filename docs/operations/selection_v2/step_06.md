# 第6步：突破后回踩

2026-09-08复核对本页原实现结论为暂不通过。以下版本、170项测试及5/3/0统计均为
历史记录，不能作为修复后验收。R1–R5修复与版本迁移见
[step_06_review_fixes.md](step_06_review_fixes.md)；最终提交的重新导出、逐票对账和
运行身份以该页指向的独立产物为准。仍停在第6步，等待复核，不合并main。

## 已接受基线与范围

用户已接受第5步完整 HEAD
`57a1184c8b823fb67ccaff91bdc4eb5f33a704cc`（计算提交
`f37f7560a421a402f4fea02070761f9e721a84a9`），并授权从该提交建立
`codex/selection-v2-step-06`。本步只增加突破后回踩这一独立观察通道，复用第4步
机会状态机和第3步固定支撑语义；不改变交易规则、评分、仓位、旧研究结果或生产扫描。
未运行全池扫描、期权请求、收益研究或 FINAL OOS，也未合并 main。

## 公共接口、语义与身份

公共纯函数：

```python
detect_breakout_retest(input: BreakoutRetestInput) -> BreakoutRetestResult
```

核心位于 `pcs.trend.breakout_retest`，不读取供应商或扫描器。调用方先通过现有
`OpportunityDataReader` / `PCSDataAccess` 形成验证过的 `OpportunityFeatureView`。
`evaluate_entry_opportunity` 可按固定顺序独立启停
`HEALTHY_PULLBACK`、`SHALLOW_PULLBACK`、`BREAKOUT_RETEST`；加入第三通道不改变前两通道
子结果身份。

- 阻力为突破日前20个已完成交易日最高价，平局取最近日期；当天不进入阻力窗口。
- 突破要求收盘严格高于 `R + 0.10 * ATRb`、RVOL20至少0.90且结构为bullish。
- 在突破日收盘后建立固定区域：`R ± 0.175 * ATRb`；失效线为下沿再减
  `0.35 * ATRb`。边界、ATR和失效线不随新高或ATR扩大移动。
- 突破日不能同时算回踩。首个回踩窗口为后续1—15个交易日；区域相交后复用固定
  支撑测试。回踩后1—3日确认，确认后1—3日为当前资格窗口。
- 盘中穿透与收盘失效分开；缺日停止推进并保留最后可知状态。过期或失效后，必须先有
  收盘回到旧阻力及以下，之后更晚的新突破才可重启，并保存父突破ID。
- 完整前缀冷算、合法增量恢复与同日恢复产生相同业务结果ID；run_id、request_id、
  received_at和恢复方式不参与身份。身份只包括本通道实际消费的突破、确认、入场和固定
  支撑政策，不受健康／浅回调专属阈值影响。

结果 schema 为 BreakoutRetestResult 1.0 / `breakout-retest-v1`；聚合机会计算版本为
`entry-opportunity-v2.3`。JSON、CSV、中文、AI视图及
`breakout_events.json`、`breakout_retest_timeline.json`均由同一typed结果生成；
`find_breakout_event`和`find_breakout_day`支持独立查询。

## 实现提交与验证

计算提交集合：

- `ceba4d3cecd61a8029c5aefdcba19a2b088ede71`：突破／回踩生命周期、聚合、输出、示例和专项。
- `259213715489ef69d893d518dc7bd569a6467cca`：公开API与恢复身份对齐、真实验收器。
- `a55963b67675314929f14a950bf101a2ee053a0a`：只绑定实际消费政策的业务身份，并为示例增加有界摘要。

最终源码专项命令：

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-06
$env:PYTHONPATH='src'
python -m pytest tests/trend/test_breakout_retest.py tests/trend/test_shallow_pullback.py tests/trend/test_entry_opportunity_v2.py tests/trend/test_opportunity_engine.py tests/trend/test_support_zones.py tests/trend/test_support.py tests/trend/test_market_structure.py tests/trend/test_pullback.py tests/trend/test_indicators.py tests/trend/test_snapshot.py tests/trend/test_cleanliness.py -q -k 'not talib_dependency_stays_inside_indicator_implementation'
```

结果：**170 passed, 1 deselected in 8.54s**，其中本步专项21项。覆盖20日窗口、平局规则、
严格突破边界、RVOL、固定ATR、b/r/c交易日边界、连续触及、盘中穿透、收盘失效、过期、
重启、未来隔离、缺日、修订前缀拒绝、等待回踩／等待确认恢复、同日恢复、family独立性、
typed产物和查询。被排除的旧测试检查TA-Lib只能在`indicators.py`导入；接受基线中的
`underlying_profile.py`已经导入TA-Lib，本步未修改该历史边界，也不把它报告为通过。

## 真实有界验收

最终目录：
`H:/workspace/PCSOS/selection_v2_outputs/step_06_acceptance_a55963b_20260904`。

本次没有重复读取canonical或供应商。它复用第5步已验证、保存且逐文件校验的完整输入：
`H:/workspace/PCSOS/selection_v2_outputs/step_05_acceptance_f37f756_20260904`。
Step 6加载器先验证该bundle manifest及产物哈希，再读取7票各260根日线；观察范围
2026-06-11—2026-09-04，指标种子起于2025-08-25。原canonical身份为
`bcf968b0aff51058367d31fbc9fb129b7cdd8e153995e44cef92de3dea207a6e`，
manifest SHA256为
`d3873cb0c5d8321376c44ae36cf05efd4d80dd0fb95802296c06fd987acf36c4`。
这证明保存输入复用及哈希未变，不声称本轮重新读取了canonical路径。

| 股票 | 突破/回踩/确认 | 截止9月4日状态 | 关键事实 |
|---|---:|---|---|
| NVDA | 0 / 0 / 0 | NO_SETUP；不合格 | 窗口内无满足全部门槛的突破 |
| PLTR | 0 / 0 / 0 | NO_SETUP；不合格 | 窗口内无满足全部门槛的突破 |
| MSFT | 1 / 0 / 0 | EXPIRED；不合格 | 07-30突破R=405.08，ATR=15.9002，08-21首回踩窗口过期 |
| HOOD | 2 / 1 / 0 | WATCH；不合格 | 06-15突破及06-16回踩未确认后过期；09-03新突破R=112.45，等待首回踩 |
| MDLZ | 1 / 1 / 0 | INVALIDATED；不合格 | 07-29突破、07-30回踩，07-31收盘失效 |
| AAL | 1 / 1 / 0 | INVALIDATED；不合格 | 06-24突破、07-08回踩，07-14收盘失效 |
| AAOI | 0 / 0 / 0 | NO_SETUP；不合格 | 窗口内无满足全部门槛的突破 |
| UBER | 未生成 | 未知 | `INSUFFICIENT_FEATURE_WARMUP`；未补数据 |

共5个真实突破、3次首回踩、0次确认。真实样本因此验证了无事件、等待、过期、失效和重启，
但**没有验证真实市场中的确认成功／ENTRY_READY路径**；该路径只有确定性TEST专项证据。
没有为凑成功而换股票、改阈值或补行情。所有结果均为研究／描述证据，不是交易许可。

`artifact_manifest.json`登记主产物哈希；`validation_run.json`与
`acceptance_validation_run.json`均为VALID。`step_06_acceptance.json`确认7个typed结果、四种
视图、突破明细、单family、独立API、完整回放、前缀恢复和同日恢复一致。UBER缺口保持独立，
不阻止其他合法结果。

## 可复制命令

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-06
$env:PYTHONPATH='src'
$bundle='H:/workspace/PCSOS/selection_v2_outputs/step_06_acceptance_a55963b_20260904'

# 独立公开API的有界摘要；去掉--summary可输出完整typed JSON
python examples/breakout_retest.py --input-directory $bundle --symbol HOOD --as-of 2026-09-04 --summary

# 仅突破回踩通道
python -m pcs.cli entry-opportunity --symbols HOOD --families BREAKOUT_RETEST --as-of 2026-09-04 --run-id manual-breakout --input-directory $bundle --output-directory H:/workspace/PCSOS/selection_v2_outputs/step06_manual_breakout

# 从等待首回踩的2026-09-03状态继续到2026-09-04
python -m pcs.cli entry-opportunity --symbols HOOD --families HEALTHY_PULLBACK,SHALLOW_PULLBACK,BREAKOUT_RETEST --as-of 2026-09-04 --run-id manual-resume --input-directory $bundle --resume-directory H:/workspace/PCSOS/selection_v2_outputs/step_06_commands_a55963b/prefix --output-directory H:/workspace/PCSOS/selection_v2_outputs/step06_manual_resume

# 只重建视图，不运行检测器或读取canonical
python -m pcs.cli entry-opportunity --symbols HOOD --as-of 2026-09-04 --run-id manual-render --input-directory $bundle --render-only --output-directory H:/workspace/PCSOS/selection_v2_outputs/step06_manual_render
```

上述四类命令均已实际运行。演示结果在
`H:/workspace/PCSOS/selection_v2_outputs/step_06_commands_a55963b`；HOOD恢复结果ID与批量冷算相同。
完整验收命令及其输入身份保存在最终目录manifest和read_audit中。

## 明确保留的能力缺口

“公司领袖／管理层特点档案”仍是未来能力，当前没有合法结构化输入、证据契约或消费接口；
本步没有从聊天或原因文本补造该信息。期权接入、经济效果、生产采用、扫描规则和主分支合并
也不属于第6步。完成代码、针对性验证、真实有界输出及交接后，停止在第6步边界等待复核。
