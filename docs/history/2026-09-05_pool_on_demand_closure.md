# Pool Scan 按需加载与局部恢复交付（2026-09-05）

任务分支 `codex/pool-scan-closure`，基线 `6bf9dd2`，已包含 `8c7f948`，未重复应用。
本次实现复用 run_pcs_pool、PoolRuntime、PCSDataAccess、control plane、现有
ClickHouse loader、ImportEngine 和合约评估适配器。没有第二套扫描器、loader、
数据注册表或策略引擎。账户仍不是扫描前提；未读取 FINAL OOS，未修改策略阈值，
未下单，未运行全池扫描。用户已有文件和修改保留，任务分支不合并 main。

## 根因和实际改动

- runner 原先只在编排层准备日线，扫描工作函数的 auto_prepare_data=False
  使内部期权准备成为不可达路径。现在扫描器只读；编排器先保存 timing 候选，
  再将准确需求交给 ensure_market_data，独立 verified read 后局部继续合约评估。
- 原加载器把 start 推到最新报价的下一天，漏掉内部缺失 session。现在按请求
  报价窗口修复；required_start/end 不再受到“已有更晚报价”的错误截断。
- 首次入库没有可读 manifest 路由。现有 ImportEngine 可绑定 access 默认
  canonical 目的地，仅用于首次入库；已有路由和歧义检查保留。读取须等待
  promotion 的真实 generation，不能提前写 manifest 或伪造就绪。
- 已有分区日期列与 staging 日期类型不同，增量合并可能触发 ArrowTypeError。
  promotion 在去重、序列化前统一 trade_date/expiration_date 日期类型。
- 日线 preflight 原先排除跨越历史 as-of 的整个分区。现在允许其进入独立
  verified audit，由 audit 检查截断后的真实 PIT warmup 数量。
- 增加哈希保护的 run-local candidate checkpoint、身份核对、选项阶段缓存失效、
  同需求 single-flight 和非阻塞跨进程锁。保留 timing 时间、code/rules/data
  identity、精确需求、回执、各阶段状态、恢复点和 next_review_at。
- 区分未加载配置、认证失败、超时、来源零行、canonical 读取失败及未检查。
  只保存白名单机器证据，不记录密钥、HTTP body、headers 或自由格式 provider detail。
- 原子 provenance 注册测试发现既有调用漏传目标路径；修正一个实参，并验证
  写入失败保留原 manifest 字节。该修复没有注册任何实际数据的虚假 provenance。

文件：src/pcs/pool/{runner,models,runtime,artifacts}.py；src/pcs/cli.py；
src/pcs/data/{access,control_plane,clickhouse,canonical_generations}.py；
tests/pool/test_on_demand_recovery.py；Pool Scan 契约、导入 runbook 和本交付记录。

## 使用命令

在原工作区使用任务分支代码和既有授权配置（PowerShell，每个环境只需设置一次）：

```powershell
Set-Location H:\workspace\PCSOS
$env:PYTHONPATH = 'H:/workspace/PCSOS-pool-closure/src'
$env:PCS_ENV_FILE = 'H:/workspace/PCSOS/.env'
```

日常自动准备扫描，一次执行即可按需加载并局部继续：

```powershell
python -m pcs.cli pool-scan --universe-id global_pcs_candidates --mode EOD --as-of latest --data-mode PREPARE_THEN_SCAN --auto-prepare-data --output-directory pool_scan_runs/daily_on_demand
```

只读扫描（相同目录复用兼容 checkpoint）：

```powershell
python -m pcs.cli pool-scan --universe-id global_pcs_candidates --mode EOD --as-of latest --data-mode READ_ONLY --output-directory pool_scan_runs/daily_on_demand
```

真实验收标的的当前 EOD 恢复命令：

```powershell
python -m pcs.cli pool-scan --symbol GSHD --mode EOD --as-of latest --data-mode PREPARE_THEN_SCAN --auto-prepare-data --output-directory pool_scan_runs/on_demand_acceptance_20260905
```

每个需求每次运行最多发起一次 control-plane 准备；provider 自身沿用已有有限
重试。来源零行时保留 WAIT 候选、查询时间和回执，等待下一次调用且达到重试
时间后再尝试。没有后台服务或后台监控。新 session 会重新计算 timing。
准备 deadline 停止启动后续导入，不强杀已开始的事务；provider 每次请求仍受
既有 ClickHouseConfig 的有限超时约束。provider_calls 计逻辑来源尝试，不计
底层 HTTP 重试次数；promotion_calls 计回执中的分区 promotion。

## 真实运行：成功与缺失的边界

完整机器证据见 [real_evidence.json](pool_on_demand_20260905/real_evidence.json)。

授权配置为 H:/workspace/PCSOS/.env：存在且加载后 password 存在；worktree
没有 .env；URL 未显式设置但使用已有默认值。子进程继承 PCS_ENV_FILE 和
已加载 password。报告只含存在/加载布尔值和路径，没有复制密钥。实际来源
结果通过标准 control plane 和其构造的 client 获得，没有另设导入入口。

真实 run `5f63eede52764515ad3725a7bf4bc80d`：

- GSHD，EOD 行情 session **2026-09-04**，指定 as-of 2026-09-04T17:00:00-04:00。
- 1/1 请求标的完成真实 daily/timing，timing 通过且在 provider I/O 前保存候选。
- 准确 options 需求：PUT，报价 required_start=required_end=2026-09-04；
  DTE 30–45，到期窗口 2026-10-04..2026-10-19。报价分区是 **2026Q3**。
- 现有 ClickHouse loader 查询 firstrate.options_kline_1d，TradeDate 单日过滤；
  查询 2026-09-05T17:03:50.540266Z 开始，17:03:52.996838Z 完成。
- 回执 request_id `bfd7b68fb9ca4ae593bc1e442050f23e`，
  AUTHORIZED_SOURCE_NO_ROWS，physical_rows=0。独立 canonical 读取失败，
  OPTIONS_GENERATION_MISSING；没有 promotion，没有可声明的 options verified identity。
- 候选保留 TIMING_ENTRY_READY / options DATA_BLOCKED / final WAIT。
  event、portfolio 均 NOT_EVALUATED。**成功合约评估分母为 0，不能据此声称零机会。**
- 此次验收使用 provider connect=5s、read=30s、total=30s、max_attempts=2；
  下一次允许尝试记录为 2026-09-05T17:04:22.996838Z。该时间是恢复条件，
  并不表示后台正在等待。未重复查询同一已确认零行的来源。

为选择验收 session，另外只读检查过 AUPH/BKKT/GSHD/IDYA 的 9 月 3 日：
4/4 完成 timing，0 个 timing 通过，因此没有导入期权；这些结果仅作选样说明。
没有扩大为全池或历史数据导入。

最终代码的真实只读恢复验证：

- 第一轮 9d636a4dd3f642d2b9da1b322dee3359，第二轮 d92777b543864a23a48ab2c4059a5dda；使用 --as-of latest，行情仍为 9 月 4 日。
- 第二轮 timing_reused_count=1，timing_computed_at 与第一轮相同；两轮 options
  准备次数均为 0，保留原来源查询回执，三个 canonical manifest 哈希前后相同。
- 实际 code content identity：sha256:c3a1e2c710e55a298ad9c2faba367d067c7b02060fb3b83ea459e01713c7c364。
- 可恢复 checkpoint 位于 H:\workspace\PCSOS\pool_scan_runs\on_demand_acceptance_20260905\d92777b543864a23a48ab2c4059a5dda\candidate_checkpoints。其哈希与身份已校验；
  后续代码或数据身份变化会按契约失效相应阶段，不把历史证据当成当前批准。

本次真实新增 options 覆盖 **0**。正向真实闭环（入库成功后继续实际合约评估）
仍未完成：选定 GSHD session 的授权来源确认零行。恢复条件是来源发布所需
session 报价，或标准控制面已有相同 session 的有效 canonical generation。
fixture 成功不能替代这个外部输入，也不证明全市场没有合适数据。

## 定向验证

在 H:/workspace/PCSOS-pool-closure，PYTHONPATH 指向其 src：

```powershell
python -m pytest tests/pool/test_on_demand_recovery.py tests/pool/test_verified_boundary.py tests/pool/test_runner_contract.py tests/pool/test_preparation_orchestration.py tests/pool/test_artifacts.py tests/pool/test_quote_sessions.py tests/pool/test_runtime_performance.py tests/pool/test_process.py tests/pool/test_final_gates.py tests/data/test_control_plane.py tests/data/test_control_plane_boundary.py tests/data/test_clickhouse.py tests/data/test_generation_lifecycle.py tests/data/test_generation_provenance.py -q --disable-warnings --maxfail=2
```

结果：169 passed，39.96s。最后回执/统计微调后仅重跑受影响恢复测试：

```powershell
python -m pytest tests/pool/test_on_demand_recovery.py -q --disable-warnings
```

结果：16 passed，13.65s。16 个新增测试覆盖只读无写入、完整标准 loader/staging/
validation/promotion/独立读取/同次合约发现、同需求并发去重、来源零行与其他
标的继续、loader 成功但读取失败、checkpoint 哈希/身份/新 session 失效、
期权身份变化仅重算期权、代码失效仍保留来源退避、首次 canonical 路由、
内部报价缺口、失败不覆盖 timing、未评估账户不产生交易 READY。
这些都是隔离 fixture 测试，成功合约数不计入真实机会数。

既有依赖实际 canonical 存储的路由测试，在 worktree 缺本地数据时失败；在
原工作区以任务代码执行以下同一测试后 5 passed，2.49s：

```powershell
python -m pytest H:/workspace/PCSOS-pool-closure/tests/data/test_logical_options_routing.py -q --disable-warnings
```

三项 provenance 测试曾暴露漏传 target 的原有错误；修复后该测试文件及恢复
测试 24 passed。未保留失败为成功证据。最终执行 git diff --check 并校验本文
链接和真实运行 manifest 哈希。没有把此前全池统计重复作为本次验收结果。
