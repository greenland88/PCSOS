# 第2步：可靠的股票风险特点档案

## 边界与版本

前置验收提交：`45c17d50fb88ea865567819ff98383ff358d9bd0`。
分支：`codex/selection-v2-step-02`；独立工作区：`H:/workspace/PCSOS-selection-v2-step-02`。
本步新增组件描述日线风险特点，不调用期权、扫描、账户或收益研究，不修改交易规则、评分、仓位或旧研究 resolver。
第1步源码、run 和验收输出保留；不合并 main。

公开接口：`pcs.trend.underlying_profile.measure_underlying_profile(input: ProfileInput) -> UnderlyingProfile`。
业务模型在 `pcs.trend.selection_models`，复用 `pcs.analysis_contracts` 的 CallContext、SourceReference、EvidenceValue 和独立来源／执行／能力状态。
schema `1.0`，计算版本 `underlying-profile-v1`，测量政策 `underlying-profile-descriptive-v1`。
实现提交：`d31c978c8ab28b116db31a88eb6ad587a9585587`；有界分区修正：`12b5170abd015a96f6ce97d32c037344f7feae37`。
真实验收使用后者的干净源码。交接文档提交为这两项之后的独立提交，未再改变实现。

## 输入、窗口和复用

可信适配器 `pcs.pool.underlying_profiles.ProfileDataReader` 通过 PCSDataAccess、现有 active verified daily resolver 和 ManifestSnapshot 读取。共享基准按 symbol/session/window 缓存一次。
resolver 新增显式 `allow_partial_history`，仅用于分项覆盖描述，默认 false 保持旧调用行为。可选 `required_start_session` 按日期约束分区，防止近期缺日被窗口外旧行数代替；默认None保持旧行为。它不放宽身份、哈希、重复、重叠、结束日或 canonical 输入校验，也不会触发补数。
不完整的外部文件不能伪装为 canonical；既有 canonical 完整性门槛拒绝的源仍拒绝。规范输入层的缺量/缺基准行为用明确 TEST 输入验证。

默认需要 326 个交易日：252观察日 + 60日前高点前缀 + 14日ATR额外起算前缀。
先由 XNYS 日历计算需求，再解析最小的必要年度分区。既有 verified loader 必须校验所选分区的完整身份，物理验证可能包含窗口外行；交付的 feature view 和全部计算只保留声明的合法区间。
结果记录请求完整 session 列表、观察 session 列表、实际 session、缺日、前缀覆盖、ATR分段起点、合法输入hash。不能用60根展示尾部冒充252日历史。
遇到缺日会按交易日历补成缺值，不把跨多日收益当单日收益。短历史不会使合法20日指标丢失。递推ATR每个连续有效HLC片段单独起算，缺日后需重新暖机；记录全部起算日。

ATR复用现有生产使用的 TA-Lib Wilder ATR 定义；`calculate_base_indicators` 的200日/全OHLCV验证和 `cleanliness` 的复合分类不适合本步字段独立缺失语义，因此不调用它们、不填假volume。
相对收益复用 `relative_strength._safe_return`，窗口公式仍为股票收益减基准收益。新接口按20/60日分别检查，不能被旧函数的整体61根要求阻断20日。
旧 `adaptive_profiles.measure_characteristics` 和策略 resolver 完全保留，包括其历史局限；不推荐将旧恢复统计用于本组件。

## 指标与缺失语义

- 成交额：20日 `median(close * volume)`，USD/session；USD是美国股票的配置口径，标在来源中，不声称为供应商实测币种字段。缺volume不填0。
- 实现波动：20/60个简单收盘收益率的样本标准差（ddof=1）乘sqrt(252)，需要21/61个相同日历session的收盘价。0是合法的零波动。
- ATR/价格：TA-Lib ATR14 / 当日收盘；起点与旧快照不同可能产生递推差异，不声称数值天然相同。
- 相对强弱：相同session上的股票窗口收益减SPY窗口收益；缺基准仅限制对应窗口。不是分数或准入判断。
- 上下跳空：分别max(0, open-previous_close)/previous_ATR、max(0, previous_close-open)/previous_ATR。20/60/252日给出含零样本的50/90/95分位数、>=1及>=1.5 ATR次数和频率，逐日保存同条记录分子/分母身份。
- 回撤分布：每日max(0,1-close/此前60日最高close)，含零值；252日50/90/95分位数和当前历史经验分位位置。该滚动描述值下降不表示episode恢复。
- 每项有数值、单位、定义、窗口、实际参数、样本数、覆盖、来源、数据session、来源状态、执行状态、能力状态及缺失原因；无值为null。
- 事件类指标 sample_count 单位是episode，coverage为观察日线覆盖；恢复中位数无样本或KM未过0.5时为null。

## 回撤事件与删失

收盘跌至此前60日内已知最高收盘P的95%或以下时触发一次，冻结P与其日期，直到首次close>=P才恢复。最大深度为1-episode最低收盘/P。
一个未恢复episode期间不再启动新episode。起始日年龄0，往后按交易session间隔计时。同日恢复和删失时，KM先处理恢复再移出删失。
窗口前已开始或起点/高点前缀无法完整确定的事件保留左截断标志，不进入恢复天数或KM样本。有限前缀只证明可见历史；不能推断前缀之前的隐藏高点。未知起点为null，已知的窗口前触发日期保留。
没有收复P则保留右删失、冻结价、最低点和观察年龄；缺收盘导致随访中断时按最后有效日删失，不能跨缺日假定首次恢复日期。
KM采用product(1-d/n)，不作未来恢复概率保证；已恢复样本中位数是条件统计，旁边始终保留未恢复事件。

## 独立使用

从保存canonical数据的项目目录执行，PYTHONPATH明确指向本步源码：

```powershell
Set-Location H:/workspace/PCSOS
$env:PYTHONPATH='H:/workspace/PCSOS-selection-v2-step-02/src'
python H:/workspace/PCSOS-selection-v2-step-02/examples/underlying_profile.py --symbol NVDA --as-of 2026-09-04
```

纯计算样例（TEST身份，无数据源读取）：同命令改用 `--fixture`；缺量和缺基准例增加 `--missing`。完整恢复样例的冻结价100、最低90、只产生一个episode、3个交易日恢复。

现有CLI的独立接线：

```powershell
python -m pcs.cli underlying-profile --symbols NVDA,PLTR,MSFT,HOOD,UBER,MDLZ,AAL,AAOI --as-of 2026-09-04 --benchmark SPY --run-id step_02_acceptance_20260904_bounded --output-directory H:/workspace/PCSOS/selection_v2_outputs/step_02_acceptance_20260904_bounded
python H:/workspace/PCSOS-selection-v2-step-02/scripts/accept_underlying_profiles.py H:/workspace/PCSOS/selection_v2_outputs/step_02_acceptance_20260904_bounded
```

目录必须为空，禁止覆盖旧输出。包含JSON、AI JSON、中文逐指标及逐episode表、输入/输出schema、字段字典、读取审计及产物hash。
独立验收脚本从公开API再度量NVDA，与批量的result_id、全部指标和episode比较；复核其他视图和文件hash。上述为实际已执行命令；重现时另给空目录名，不能覆盖现有验收目录。
只读取保存结果可重新生成中文/AI视图、比较已算指标；更改测量窗口、ATR起点、基准或数据身份需重算并生成新result_id。仅更换run/request ID或视图不改变结果身份。

## 必要验证

```powershell
Set-Location H:/workspace/PCSOS-selection-v2-step-02
python -m pytest tests/trend/test_underlying_profile.py tests/data/test_daily_suffix_resolution.py tests/research/test_adaptive_profiles.py -q
git diff --check
```

测试仅证明确定输入行为；真实canonical验收另记。范围涵盖恢复、持续未恢复、去重、左右删失、KM同日顺序、前日ATR、缺量/基准、长短窗口、日期冲突、未来隔离、缺日、来源身份、视图、schema与政策重算，以及旧研究行为保护。

## 实际验收

组件实现完成；26项针对性检查全部通过（`26 passed in 3.75s`）。`git diff --check`通过。
真实验收于2026-09-08 UTC（本地9月7日晚）执行，固定行情日2026-09-04。
最终目录：`H:/workspace/PCSOS/selection_v2_outputs/step_02_acceptance_20260904_bounded`。
其中 `artifact_manifest.json` 记录源码 `12b5170abd015a96f6ce97d32c037344f7feae37`、`tracked_source_dirty=false`。
人工入口 `acceptance_report.md`，逐指标/逐回撤报告 `underlying_profiles.zh-CN.md`，机器结果 `underlying_profiles.json`；`acceptance.json`为实际对账回执。

| 股票 | 实际日线数 | 可用指标/58 | 本次缺失 |
|---|---:|---:|---|
| NVDA | 298 | 54 | 252日回撤深度50/90/95分位数、当前回撤历史分位位置；28根前缀缺失 |
| PLTR | 326 | 58 | 无数值缺项；保留1个左截断事件 |
| MSFT | 326 | 56 | 已恢复条件中位天数、KM中位天数没有合格证据 |
| HOOD | 326 | 56 | 已恢复条件中位天数、KM中位天数没有合格证据 |
| UBER | 未生成 | — | 来源仅登记至2026-09-01，未达到9月4日；reader返回INSUFFICIENT_FEATURE_WARMUP |
| MDLZ | 326 | 56 | 已恢复条件中位天数、KM中位天数没有合格证据 |
| AAL | 326 | 58 | 无数值缺项；保留1个左截断事件 |
| AAOI | 326 | 58 | 无数值缺项；保留1个左截断事件 |

全体请求读取2025-05-20至2026-09-04的326个交易日；观察窗口2025-09-05至2026-09-04，共252日。
NVDA实际2025-07-01起，共298日，前缀46/74。其他6票及基准SPY均326日，SPY只读取一次。
7票均可计算成交额、20/60实现波动、20/60相对SPY变化、ATR/价格、20/60/252上下跳空、当前滚动回撤；报告逐指标保留单位和明细。
7票均保留未恢复事件及年龄。NVDA完整恢复样本为10、116、7个交易日，条件中位数10来自这些实际样本，不是旧代码默认10。

初次验收目录 `step_02_acceptance_20260904` 原样保留：该次6票生成、NVDA读取窗口外分区时checksum阻断、UBER末日不足。
基于实际入口问题加入日历起点边界后，只验证声明区间内所需分区；NVDA2025/2026分区正式验证通过，窗口外历史没有被纳入新档案，也未修复或修改。
本轮没有补任何日线、请求供应商、读取期权、扫描股票或改变已有判断。

### 来源身份和验证回执

- canonical manifest：`H:/workspace/PCSOS/data/manifests/storage_manifest.csv`。
- 原始文件SHA256：`e1045bf72a80265f6d90a597655d032bdf2c7dcc9eab970c5536b29ab5b9537d`。
- ManifestSnapshot identity：`5af72f892609519c0293b922f8515b3abdeb14683b904e7922a30501683c6400`。
- 7票+SPY的16个canonical分区，前后文件哈希一致。逐分区路径、generation、checksum、fingerprint、物理日期/行数及逻辑读取范围保存在 `read_audit.json` 和各票 provenance。
- NVDA generation：`cb4b8b1c182eebf49160d81e|d661221372d6952a086858bd`。
- NVDA verified handle checksum：`086453df01f4a089e078e96039617aac6cef670b0759fa8d4a32957165358fa2`。
- NVDA合法输入hash：`f5d6a7e46baf7b0b487570034543f9f1add4d8151186498a827484ac2f45f9a6`。
- NVDA独立与批量共同result_id：`sha256:56e928a4c3498934b1c544e71339712409ab017fc8b7695154cdd5357261b4bc`。
- 最终导出manifest SHA256：`9445f4fa085a941d70f9b632703e111a4c1f1a1ba68baddd464a9acea1da18d3`。

实际通过：8票处理身份唯一齐全（7结果+1明确失败）；全部7个结果通过UnderlyingProfile读取；JSON、AI、中文视图共同事实一致；全部导出文件hash一致；NVDA独立Python API的result_id、指标、episode与批量一致；来源manifest及16文件未变化。
真实验收总状态PARTIAL专指UBER缺少目标日期和部分指标缺证据，不代表组件未实现。

正常及缺失TEST样例另存在最终目录 `fixtures/normal` 和 `fixtures/missing`，分别包含JSON、schema、中文、AI及文件hash；不当成canonical业务验收。公开example脚本的 `--fixture` 实际执行返回COMPLETED，样例条件恢复3日。

第1步验收manifest仍为 `cd4439c087dba0f9343b5d64b481074e73b5c21117df6eb8e6a8cd6b532e578b`；原run manifest仍为 `2d927f244e57c3aa576ede819cfc9e83e50e50437713efbb4188a42e97f9cae9`。
`adaptive_profiles.py`、`analysis_contracts.py` 和 `pool/ai_evidence.py` 与45c17d5无差异。主分支仍为a463e46。

### 剩余输入与停止边界

UBER须先通过既有canonical流程取得并验证9月2日至4日日线，才可单独补其档案；本轮不执行导入。
NVDA声明的326日读取范围缺2025-05-20至06-30共28根。仅补齐252日回撤分布至少需要2025-06-10至06-30的14根，使窗口首日前高点前缀从46达到60；若要完整复用声明的ATR起算条件，还需前面14根。取得合法数据后重新度量；现有其他54项和合法episode可直接使用。
MSFT、HOOD、MDLZ的恢复统计缺失是本观察范围内缺合格恢复证据，不是API故障；不增加虚构样本或默认值。
正式交易未采用本组件。本步到组件、确定输入检查和上述真实样本验收为止，不进入第3步。
