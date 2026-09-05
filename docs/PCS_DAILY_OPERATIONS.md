# PCS 日常操作规范

本规范供执行任务的 agent 使用：用户以自然语言指挥，agent 调用系统、准备数据、
恢复阶段并报告结果，不要求用户手工执行命令、加载数据或连接内部模块。
这是现有 [Pool Scan 契约](architecture/pool_scan_contract.md) 和
[数据导入 runbook](data_import_runbook.md) 的操作入口，不新增执行框架。

## 固定环境（2026-09-05 核实）

| 项目 | 固定值 |
|---|---|
| 已核实程序版本 | `4e0566673a1c64e8d83b65f646fb8de44e689134`（授权日线维修：内部缺口、窗口准入、重叠值保护及日线独立预检） |
| 代码 checkout / 分支 | `H:/workspace/PCSOS-pool-closure` / `codex/pool-scan-closure` |
| 执行工作目录 | `H:/workspace/PCSOS`（该目录 main 的旧源码不是日常运行源码） |
| Python / PYTHONPATH | `C:/Python313/python.exe` / `H:/workspace/PCSOS-pool-closure/src` |
| 授权配置 PCS_ENV_FILE | `H:/workspace/PCSOS/.env`，存在；worktree 无 .env |
| canonical 数据根 | `H:/workspace/PCSOS/data/parquet` |
| 主 manifest | `H:/workspace/PCSOS/data/manifests/storage_manifest.csv` |
| options 路由 | 原工作区 `config/data_source_routes.yaml`；经 PCSDataAccess 解析，不猜物理版本 |
| 已存在 options manifests | 原工作区 `data/manifests/storage_manifest_options_v2.csv`、`storage_manifest_options_v3.csv` |
| 默认 universe | `global_pcs_candidates`；原工作区 `data/artifacts/global_pcs_candidates/active.json` |
| 日常报告 / checkpoint 根 | `H:/workspace/PCSOS/pool_scan_runs/daily_on_demand`，首次实际运行由系统创建 |
| 程序入口 | `pcs.cli.pool_scan` → `pcs.pool.runner.run_pcs_pool` |

universe 指针及目标快照已确认存在；核实时版本为
`c2ede1f139e783e3-fd5a6552aff369df`，配置含 2953 个标的。这是配置清点，
不是本次扫描结果；后续报告实际使用的 universe 版本和数量，不硬编码旧数量。
授权配置的既有加载及子进程继承证明见
[真实证据](history/pool_on_demand_20260905/real_evidence.json)；本次日线维修的实际加载、
恢复和 verified read-back 见[日线恢复记录](history/2026-09-05_daily_canonical_recovery.md)。禁止输出、复制或提交密钥；URL/USER 已有默认值，不因其
未显式设置就认定缺凭证，不擅自补造其他输入默认值。

每次只检查这组已知路径、git status、程序/配置身份和所需 checkpoint，
不重新搜索整个仓库。HEAD 可包含仅文档的后续提交，但 `src`、依赖声明及
执行配置必须保持已核实身份。不得自动 pull、切换到未检查代码或合并 main；
发现程序/依赖有差异时记录具体差异并停止依赖该差异的执行，转交独立维修/版本审查。
配置变化先有界核实其来源和授权，不默默接受未检查的规则/来源变化。

原工作区实际执行配置的核实 SHA256（不含 .env）：

- `config/pcs_rules.yaml`: `b15a415c4670c188ee954b10afeb23a33fd8193f22f1b3728e243b55a6e0bb74`
- `config/data_source_routes.yaml`: `c2deb0397cd66330fbbafe3caffb84cb57d3056e4cc6ad0ef9a203e751a45227`
- `config/market_data_source_registry.yaml`: `3786be73643aa46c68d9d3bf74ea75f9de4b0b07963f7eb5934b322e5d459b4f`
- `config/data_remediation_registry.yaml`: `acae669ecdc103ed3574f9b15672e66e0ef7f462e800997673b1f5b1b14b3d70`

## 自然语言指令与执行

- **“扫描今天的 PCS”**：取实际执行时间（America/New_York，带时区）作为
  decision_as_of，通过现有 XNYS 日历 `resolve_effective_market_session` 求最后
  完成 session，记录 market_data_as_of。默认 CURRENT EOD 扫描，明确行情日，
  不称实时扫描；不能为了候选改成其他日期。用固定 universe。
- **“扫描指定股票”**：同样的当前 EOD 流程，仅把用户明确指定的 ticker 作为范围；
  引擎仍可读取必要 benchmark，不把 benchmark 算进请求标的数量。
- 两种扫描都检查兼容 checkpoint，自动执行 PREPARE_THEN_SCAN + auto_prepare_data：
  日线准备/eligibility/timing → 保存通过者 → 检查期权 → 仅补必要报价 →
  标准 control plane/已有 loader → staging/validation/promotion → 独立 verified read →
  局部继续期权评估。报价日期与 expiration 分开，不下载全池期权或全历史。
- 仅复用身份兼容的结果；新 session 重新判断 timing。来源零行保留 WAIT 候选、
  原因和来源查询时间；其他 ticker 继续。遵守已有有限 retry/deadline、single-flight
  和 next_review_at，不因 agent 再次解释结果而重复查询，不无限等待，不声称后台监控。
- 保存本次报告、真实准备回执及恢复证据。每只请求 ticker 恰好一条结果。
  标准运行和上述按需数据准备是用户的持续日常授权，不逐次询问。

以下只是 agent 内部调用配方，不能转交用户自行运行：

```powershell
Set-Location H:\workspace\PCSOS
$env:PYTHONPATH = 'H:/workspace/PCSOS-pool-closure/src'
$env:PCS_ENV_FILE = 'H:/workspace/PCSOS/.env'
& C:/Python313/python.exe -m pcs.cli pool-scan --universe-id global_pcs_candidates --mode EOD --as-of latest --data-mode PREPARE_THEN_SCAN --auto-prepare-data --output-directory pool_scan_runs/daily_on_demand
```

指定股票时把 `--universe-id global_pcs_candidates` 替换为重复的 `--symbol TICKER`。
由入口记录实际 as-of/session，沿用现有规则、超时及 provider 重试配置。若用户明确
要求只读，使用 READ_ONLY 且不传 auto_prepare_data；仍由 agent 操作和报告。

## 操作与维修严格分开

日常仅允许调用既有引擎、控制面、loader，补所需数据，读日志、有界诊断、
恢复兼容 checkpoint 和解释结果。不得修改代码、测试预期或策略参数；不得放宽
timing、流动性、事件、风险或数据验证；不得为获得候选换日期、换来源或填默认值；
不得删除有效数据、强改 manifest/generation、合并分支、跑研究或读取 FINAL OOS。
不得下单、撤单、roll 或做其他账户交易动作。

发现疑似代码问题，记录失败阶段、系统原始 reason_codes、run/checkpoint ID 和
最小证据，继续可独立完成的工作。最终列出“需要单独维修的具体问题”，不把
普通扫描变成代码改造。只有用户明确要求修复，才进入维修任务；不为了验收改测试。

## 账户与当前交易决策

普通扫描不要求账户信息。用户说“结合我的账户判断”“能否加仓”“准备做这一笔”
才读取明确目标账户的当前持仓、待成交订单及风险。账户身份不明确先确认账户；
当前尚未核实可用的账户连接，不假定能够读取 Robinhood。只使用用户已授权的
现有账户来源；不可用时明确缺失输入，保留市场扫描结果，不造空账户或默认资金。

通过已有 schema v2 CURRENT_EOD context adapter、DecisionEngine 与组合风险引擎
检查，不创建另一套引擎。记录 decision_as_of、market_data_as_of、
portfolio_observed_at、event_known_at，执行相应 freshness 与事件覆盖验证，
待成交订单风险也必须有真实证据并由现有能力检查；能力缺失按维修边界处理。
历史 PIT 不得使用未来账户快照；当前决策不得伪装历史回放。

候选/DISCOVERED、合约选择通过、事件检查、账户批准分别报告。必要检查未完成
不得报告 PCS_TRADE_READY；DISCOVERED 不等于 DecisionEngine 通过。
任何下单另需明确授权，评估候选不构成交易授权。

## 固定简洁输出与验收边界

每次只报告：代码版本、行情日期、范围；请求数、实际完成评估数、数据阻塞数；
候选及实际通过阶段；已选合约和报价时间（如有）；关键等待/拒绝原因；本次
补齐内容和下一次恢复等待项。评估数必须注明阶段，日线/timing 与期权成功
评估分母分开，不能把结果行数当成功评估数。机会数量注明成功评估分母。
数据未评估成功就说“未评估”，不能说“没有机会”。不展示长日志、历史测试数，
相同 blocker 无新证据时不反复展开。

`d1bcf33` 已有代码/针对性测试、真实等待数据和只读 timing 复用证据。
真实导入成功后同次自动继续期权评估的正向验收仍待完成。下一次正常扫描自然
遇到合适真实样本时，顺带记录 ticker/session/source、加载及 promotion 回执、
verified identity、候选数量、实际到达阶段和后续调用是否避免重复导入。
不额外扩展日期搜索、修改条件、删数据制造缺口或重试已确认零行来源来完成验收。
