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

真实验收命令、源码版本、结果与缺口将在干净提交执行后追加。
测试是TEST输入行为证据，不代表真实报价、生产采用或收益有效。
