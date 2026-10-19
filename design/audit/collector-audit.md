# collector 审计报告（M0 / 修复阶段）

> 审计对象：`collector/` Python 采集器（M0 交付）+ `tests/collector/` + `tools/collector_*`
> 契约基准：`protocol.md` v1、`BUILD_PLAN.md` §1/§3.3/§3.4/§4/§9-M0、`HANDOFF.md`
> 审计方式：源码逐模块审查 + 58 项 pytest + 真实子进程/管道实验 + 本机真实 provider 冒烟 + 进程树 CPU/退出耗时实测 + 团队（lead/tauri-dev）集成反馈
> 约束：未修改 `ui/`、`src-tauri/`、`package.json`、`protocol.md`、`BUILD_PLAN.md`、`hb/`；未做 git 初始化/提交。

---

## 1. 结论摘要

M0 功能闭环成立：`--fake` 可确定性演示 5 种 kind、`level:null`+`approx`、`charging`、低电 alert、`replaces` 迁移、`gone`；`--stdio` 真实管道握手/命令/退出正常；真实 provider 在本机可启动并正常退出。协议信封、稳定 key、完整快照门控、stdio 五条硬化均已落地并有测试。

本轮审计共记录 **16 项发现**：其中 1 项为已修复的线上崩溃（`seen` NameError），4 项为 lead 指出的必修缺陷（真实模式 replaces、退出慢/孤儿、refresh 丢弃、`--once/--probe` 缺蓝牙），1 项为性能主因（PowerShell watcher），其余为已文档化的设计取舍/风险。

| 编号 | 级别 | 发现 | 状态 |
|---|---|---|---|
| F1 | P1 | `StateStore._apply` 引用未定义 `seen` → provider 线程 NameError，UI 只剩 hello + 空 tick | **已修复**（含 3 s stdio 子进程回归） |
| F2 | P1 | 真实 provider（无 hints）logitech slot→unit 升级不发 `replaces`，旧 key 等 2×TTL 才 gone → P9 抖动 | **已修复**（同轮唯一候选迁移；repro1 复核通过） |
| F3 | P1 | 退出慢/孤儿：`ThreadPoolExecutor` 非 daemon，阻塞 provider 拖住进程（实测 6.1 s）；PowerShell watcher 子进程未显式回收 | **已修复**（daemon 池 + watcher terminate→kill + 子进程 registry + Job Object；repro3 1.18 s、repro3b 无孤儿） |
| F4 | P2 | round 进行中收到 `refresh`，`_wake_all` 在空 wake 中被清 → 刷新被静默丢弃 | **已修复**（已完成的 provider 立即重投 + 标志延迟到下一轮；repro2 fast_polls=2） |
| F5 | P2 | `--once/--probe` 真实模式不含蓝牙设备：bluetooth 是 event provider，不进 ready gate，HID 轮 0.2 s 就退出 | **已修复**（等待 watcher 首快照 ≤4 s；真实 `--once`/`--probe --duration 8` 均含 FlyDigi 94%） |
| F6 | P3 | 真实模式 Bluetooth watcher 常驻 PowerShell：本机实测 CPU ~0.52%（tauri-dev 1.41%）、内存 ~136 MB，是 P2/P3/P4 超标主因 | **基本修复**：connection poll 2 s→**30 s**（运行时 patch，不改上游文件），60 s 采样 PowerShell 0.547%→**0.078–0.130%**、采集器树 **0.104–0.339%**（<0.5%，timers_per_s=0.99）；彻底去 PowerShell 仍待 CfgMgr32 直读（M3），见 §5.6 |
| F7 | 设计 | 快照门控采用"due 轮次 + 未 due provider 保留上次完整结果"的并集快照；round 未闭合/in-flight 时暂停 gone 判定 | 已文档化（BUILD_PLAN §4.2 伪代码的必要补强） |
| F8 | P2 | hello `capabilities` 曾多写 `replaces`（protocol.md 示例为四项） | 已改为与协议完全一致的四项 |
| F9 | 观测 | fake 模式调度频率 = speed×1 Hz（demo 加速虚拟时钟），其 CPU/P8 数据不可用于生产对标 | 已文档化（tauri-dev P3 采样口径提醒） |
| F10 | P3 | 上游 `DeviceStatus` 无 vid/pid/serial 字段，身份只能从 legacy key 解析 + provider 默认 VID + 可选 hints；无硬件 id 设备用持久化序号 | 设计限制，已文档化；本轮补快速重枚举复用与 hw-key 迁移 |
| F11 | P2 | 蓝牙 watcher 快照节奏 60 s，TTL 需 60/90 s 才不误报；BT 掉线 gone 偏慢（~2 min） | 已文档化，M3 直读后收敛 |
| F12 | P2 | 未接 suspend/resume → `new_session()`（协议已支持）；`offline` alert 未实现；`left_s` 需历史样本 | M0 范围外，已文档化 |
| F13 | P3 | WGI 常驻 helper（§3.4）未做：xinput 仍可能 spawn PowerShell ≤20 s；round/in-flight 保护避免误报 gone，但阻塞窗口内轮询节奏退化 | 未修，M3 |
| F14 | P4 | PyInstaller onedir / `--noconsole` 下 `sys.stdout is None` 仅单元覆盖，未打包实测（独立审计确认打包数据完整、build 恒 unknown） | 未验证 |
| F15 | P4 | 未做 24 h 长稳/P9 统计；key 迁移仅短期 fake/真实冒烟 | 未验证 |
| F16 | P4 | 真实设备全部未验证（本机除 FlyDigi 蓝牙外无外设）；hidapi 解析路径只做空环境冒烟 | 未验证 |

---

## 2. 交付物与结构

```
collector/
  __init__.py        版本
  __main__.py        CLI：--stdio(默认)/--probe/--once/--fake/--diag
  collector.py       ProviderTask 调度、线程池、BluetoothMonitor、命令循环
  state.py           StateStore：快照门控、语义 diff、last_seen/gone、告警
  keys.py            稳定 key 推导 + aliases.json（映射/序号/迁移历史）
  protocol.py        hello/device/gone/tick/alert/stat、seq/session、Device 校验
  transport.py       stdio 五条硬化（None 兜底/UTF-8/flush/stderr 线程轮转/行长）
  settings.py        %LOCALAPPDATA%\PeripheralBattery 路径、config.json、TTL 表
  merge.py           dedupe_controllers / drop_bluetooth_duplicates / merge_bluetooth（逐字保留）
  fake.py            确定性假设备 world + demo/static/migration/slow 场景
  history.py         从 hb/halo/history.py 原样复制（剩电模型）
  providers/         从 hb/providers 原样复制（30 个厂商协议，未改动）
tests/collector/     conftest + state/keys/protocol/stdio/merge 等 58 项
tools/collector_demo.ps1        一键验收（probe + 真实管道 stdio + once）
tools/collector_ndjson_check.py NDJSON 契约校验器
```

---

## 3. 关键设计审查

### 3.1 稳定 key（P9）
- 形式：`provider:vid:pid:(serial|mac|unit-<id>|uN)`；优先级 serial > mac > unit > 持久化序号。
- 序号按 `(provider, vid, pid, 归一化 name)` 首次分配，写 `aliases.json`（`next_ordinal`/`aliases`/`history`/`migrations`），删除不回收；重启/枚举顺序变化不改变 key。
- legacy key 只作为"上游→稳定"的查找种子，**不进入 key 组成**；无 slot/idx/usb_count/进程内计数。
- 真机风险（F10）：上游 `DeviceStatus` 不带 vid/pid/serial，需从 legacy key 解析；logitech unit key、pS mac/序列号可解析，xinput/steam/keychron 等无硬件 id 的走序号。vid 缺省来自 provider 源码常量。
- 迁移：同 legacy key 内 identity 升级（hints 出现 unit）走 `_migrate`；legacy key 换形走本轮修复的"可见旧 key 唯一候选"迁移（F2）。

### 3.2 完整快照门控与 gone（P0 项）
- 每轮 `begin_round(due)`：本轮所有 due provider 回报前不合并；合并只在 `_merge()` 的完整并集快照上运行三个上游合并函数。
- 未 due provider 保留上次完整结果参与并集（否则 5 s TTL 的鼠标会被 15 s 的键盘轮次拖到误报 gone）。
- `gone` 只由全局 tick（真实模式 1 Hz）判定：`now-last_seen > max(2*TTL, 10s)`，禁止"缺几轮"计数；round 未闭合与 provider in-flight 时暂停判定（防慢 provider 造成误报）。
- `device` 只在语义字段变化时发送；key 变化发送 `device.replaces`；`tick` 30 s 全量对齐；`stat` 60 s 自报 polls/avg_ms/timers_per_s/key_migrations。
- 审计确认：三个合并函数没有任何增量调用路径（`_merge()` 的唯一入口是 `_merge_and_apply()`，来自完整轮或 force_merge）。

### 3.3 stdio 五条硬化
1. `sys.stdout/stderr is None` → `os.fdopen(fd,...)`（单测覆盖，打包未验证）。
2. 双向 UTF-8、`\n`、`line_buffering=True`，无 BOM。
3. 每条消息 `flush()`；真实子进程测试验证 hello 无需额外输入即到达。
4. stderr 独立线程消费队列写 2 MB×5 轮转日志；provider/线程 traceback 不会阻塞管道（日志文件断言无 traceback）。
5. 双向 1 MiB 行长：超限整行丢弃并在下一 newline 重新同步；EOF 残片丢弃。

### 3.4 调度与 TTL（§3.3）
- 真实模式 scheduler 固定 1 wake/s（P8 目标），provider 按 kind 类别的 TTL 到期才进轮次；Steam 有控制器时 8 s。
- fake 模式为演示加速：wake 频率 = speed×1 Hz（仅 demo，F9）。
- wake/refresh：本轮修复"round 中 refresh 被丢弃"（F4）。

---

## 4. 测试与证据（审计时快照）

### 4.1 pytest
```
.venv/Scripts/python.exe -m pytest tests/collector -q
94 passed in 7.95s   (M0 基线 58；修复阶段新增 36)
```
覆盖：快照门控、乱序/并发无误报 gone、gone 时间门槛、in-flight 抑制、语义 diff、null level、approx、低电/满电告警、replaces、key 跨重启稳定、双同型号不合并、合并函数、协议信封/seq/session、stdio 行长/残片/UTF-8、stderr 轮转、真实子进程 `--stdio --fake` 3 s 端到端（≥4 device、stderr+日志无 traceback）、`--probe` stderr 干净。

### 4.2 真实进程证据
- `--probe --fake --scenario demo --speed 8 --duration 46`：9 device、1 migration、2 gone、2 tick、0 traceback。
- `tools/collector_demo.ps1`：probe + 真实管道 stdio（hello→get snapshot→shutdown）+ `--once`，两份 NDJSON 通过 `collector_ndjson_check.py`（`OK: NDJSON matches protocol.md §1`）。
- `--once`/`--probe` 真实模式（无外设）：exit 0、0 traceback、0 设备、~0.2 s 完成（修复 F5 后会等待蓝牙首快照 ≤4 s）。
- 退出耗时（修复前）：带阻塞 provider 的 stdio 进程 shutdown 后 **6.1 s** 才退出（F3）。

### 4.3 CPU/进程（隐藏态 10–12 s 窗口）

| 模式 | collector python | PowerShell 子进程 |
|---|---|---|
| `--fake --scenario static --speed 6` | ~0.39% | 0 个 |
| `--fake --scenario static --speed 1` | ~0% | 0 个 |
| 真实 provider（默认） | ~0% | ~0.52%（1 个常驻 Bluetooth watcher） |

结论：P3 超标的 PowerShell 成本来自真实模式 Bluetooth watcher（§3.4 预判，F6）；fake 模式无 PowerShell；fake 的 6 Hz 调度是 demo 加速，不对标 P8。

---

## 5. 修复记录（本轮，2026-10-19 01:00–01:15）

> 独立审计员另出 `design/audit/collector-audit-independent.md`（未覆盖，文件名不同）；本节把两份报告的必修/建议项合并记录。修复范围仅 `collector/`、`tests/collector/`、`tools/collector_*`（另写本报告），未动 Rust/UI/协议/构建脚本。

### 5.1 必修（P1）

| 项 | 修复 | 证据 |
|---|---|---|
| **F2 / 独立 P1-1** 真实模式 logitech slot→unit 不发 replaces | `keys._visible_migration()`：同轮完整快照内，新 hw-key 若与"旧 upstream key 不在 live、旧 stable 仍可见/刚 gone/被 hidden、同 provider+同归一化 name+兼容 vid/pid"的唯一候选配对，则 `_adopt()` 发 `device.replaces` 并迁移 aliases/history。pid 从旧 alias 继承（logitech unit key 自身不带 pid）。 | `repro1_logitech_nohints.py`：`replaces=logitech:046d:c547:u1`、`migrations=1`、snapshot 只剩新行 |
| **A / 独立 P1-2** xinput slot / Steam interface 快速重枚举 → u1→u2 | `_reuse_by_fingerprint()`：旧 upstream key 不在本轮 live、同 fingerprint 的非 live alias **唯一**时立即复用其 ordinal（去掉 30 s 限制）；`xinput/steam` 的 `" 1"/" 2"` 名称后缀不参与 fingerprint。live 上下文缺失时不做复用（避免直接 API 误合并）。 | `repro9_xinput_slot_jitter.py`：slot0→slot1→slot0 始终 `u1`，replaces None，migrations 0；`test_xinput_slot_change_reuses_the_same_ordinal` |
| **C / 独立 P2-5** hw-key→hw-key（PlayStation USB 换口）不发 replaces | `_visible_migration()` 候选从"旧 stable 必须是 ordinal"放宽到任意 stable；直接 KeyStore API（无 visible）提供唯一非 live fingerprint 迁移兜底。安全条件仍要求唯一候选，双同型号并存不合并。 | `repro5_hwkey_change.py`：`replaces=playstation:...usb711aa00000`，B 保持 `...usb722bb00000`，migrations=1 |
| **D / 独立 P2-6** key 迁移不迁移 names/hidden | `Settings.migrate_key(old,new)`（name/hidden 跨 key 搬迁并持久化）；`StateStore._apply` 在 hidden 判定前处理 replaces；hidden key 加入可见集合，隐藏设备迁移后仍隐藏且不再出现；不向 UI 发 hidden 设备的 device 行。 | `repro6_prefs_lost.py`：hidden/name 设置均落在新 key，visible 仍为空；`test_migration_carries_hidden_and_rename` |
| **B / 独立 P1-3** 蓝牙 watcher 失败后兜底轮询 ~2 s/次 | 删除 `BluetoothMonitor` 的 2 s 轮询循环；watcher 健康时监视线程零定时唤醒，失败检测交给 1 Hz scheduler；兜底 one-shot 由 scheduler 按 `poll_interval=60 s` 限频提交到 daemon 池，并计入 `stat.providers.bluetooth`（fallback_polls/watcher/snapshots）。 | `test_bluetooth_fallback_polls_at_the_configured_interval`（60 s 内只 1 次，到点后第 2 次）；真实模式实测 `timers_per_s=0.995`、`bt_fallback_polls=0`、watcher healthy |
| **E / 独立 P2-4** provider 挂死 → 永久 in-flight、设备冻结、线程池耗尽 | `ProviderTask.started_mono/timeout/timeouts` + `Collector._reap_stuck_tasks()`（>max(60s,3×interval) 视为挂死：清 in-flight、计时、让 StateStore 走 TTL/gone）；`DaemonPool` 增加 busy/free_slots 与 `submit() -> bool`，调度器按空槽数提交、满了就延后而非堆积；`stat` 自报 `timeouts`。 | `test_stuck_provider_is_reaped_and_its_device_can_expire`（reap 后设备可 gone、stat 有 timeouts）；`test_pool_capacity_defers_instead_of_piling_up` |

### 5.2 建议（P2/P3）

| 项 | 修复 |
|---|---|
| **F / 独立 P2-7** 真实模式 transport 恒 unknown | `infer_transport()` 改进：BT source/`bt:`/via→bluetooth；PlayStation key 12-hex MAC→bluetooth，否则 usb；Steam→2.4g；name/key 的 LIGHTSPEED/HyperSpeed/receiver/dongle/puck→2.4g；USB/wired/cable→usb；xinput 不再硬编码 usb（无 WGI IsWireless 时诚实 unknown）。真实 key 证据见 `repro8`（Steam 2.4g、DualSense BT、FlyDigi BT 已正确；无标识设备仍 unknown，M2 需 provider 侧字段）。 |
| **G / 独立 P2-3 + P3-7** 真实 `--probe --duration` 未接线；全禁用 `--once` 挂 120 s | 真实 `--probe` 接 `Collector.run_for(duration)`（默认 5 s）；`StateStore.coverage_complete` 要求所有 gated provider 至少上报一次，避免小线程池的首轮半快照；`on_tick` 在"无 gated provider"时立即 ready。 | `--probe --duration 8` 实测 8.22 s 且包含 FlyDigi；全禁用 `--once` 子进程回归 <15 s 退出；真实 `--once` 8.21 s（含覆盖等待）且包含 FlyDigi |
| **H / 独立 P2-1 残余** subprocess.run 子进程无句柄、强杀留孤儿 | 新增 `collector/procs.py`：全局 registry 包装 `subprocess.Popen`（`subprocess.run` 内部同样经过），`stop()` 统一 terminate→500 ms→kill；并创建 Windows **Job Object（KILL_ON_JOB_CLOSE）** 把每个子进程纳入，父进程被 TerminateProcess 时由内核回收（嵌套 Job 失败只 warning，registry 兜底）。 | `repro3_exit_waits.py` 总 wall 6.1 s→**1.18 s**；`repro3b_driver.py`（强杀父进程）：orphan PowerShell **未写 marker、PID 已不存在**；`test_child_registry_*` / `test_job_object_kills_children_when_the_collector_dies` |
| **I / 独立 P3-1/3-4/3-9** | ① `read_commands` 一律按**字节**判断行长（含多字节超长行丢行重同步）；② key suffix 只保留 `[a-z0-9]`（PS USB instance 的 `&` 不会再让 ndjson_check FAIL）；③ full 告警只对"曾被看到 charging 且 <100%"的设备发，首见 100%+charging 静默。 | `test_read_commands_multibyte_overlong_line_resynchronises`、`test_key_suffix_is_ndjson_checker_safe`、`test_first_sighting_full_and_charging_does_not_alert` |

### 5.3 回归测试

`58 → 94 passed`（`tests/collector/`，7.95 s）。新增 36 项，覆盖：

- `test_collector.py`（新文件）：refresh round 中不丢 + 立即重投、provider 级 refresh、线程池满延后、挂死 provider reap、BT 兜底 60 s 限频、健康 watcher 不兜底、run_once/run_for/BT 首快照等待、子进程 registry、Job Object 强杀回收、stat 暴露 bt_watch_ms、Collector.start 接线 interval；
- `test_btconfig.py`（新文件）：默认 10000 ms、env 覆盖与 2000 ms 下限、重新配置总从原始脚本出发、上游文件未被改、**首次快照补采队列（+3/+8/+15/+30 s）存在且不破坏连接变化 +3/+8/+15 逻辑**、**门控回归：只有离线配对设备（status OK + 缓存电量）+ roots 非空时必须排队，roots 为空不排队**；
- `test_keys.py`：xinput slot / Steam interface 复用 ordinal、name 后缀不拆 fingerprint、双设备换槽不合并、hw-key→hw-key replaces、key 字符集安全；
- `test_state.py`：真实无 hints logitech 迁移、双同型号歧义不合并、hidden/name 迁移、xinput slot 抖动、transport 推断、无 provider 立即 ready、首见满电告警；
- `test_stdio.py`：CJK 超长行重同步、全禁用 `--once` 快速退出、慢 provider shutdown <2 s 且无残留。

### 5.4 最终证据（本机 Windows 11 build 26300）

```
.venv/Scripts/python.exe -m pytest tests/collector -q      -> 94 passed in 7.95s
--probe --duration 8 (real)                              -> 8.22 s，device bluetooth:...:c817f5498e4e "FlyDigi BS2PRO" 94% transport=bluetooth
--once (real)                                            -> 8.21 s，hello devices=[FlyDigi BS2PRO 94]
真实模式 8 s 观测                                          -> timers_per_s=0.995, bt_watcher=True, bt_snapshots=1, fallback_polls=0, providers=29
tools/collector_demo.ps1 (fake)                          -> PASS；stdio NDJSON + once NDJSON 均 "OK: NDJSON matches protocol.md §1"
独立审计 repro1/2/3/3b/4/5/6/7/9/10                       -> 全部通过（repro2 fast_polls=2；repro3 wall 1.18 s；repro3b 无孤儿）
dist\collector\collector.exe                              -> 重建 2026-10-19 13:10:58（19.4 MB）；打包版 fake REPLACES/migration、
                                                             真实隐藏态冷启动 ×10：首条 tray/device 与首条蓝牙 10/10 ≤1.45 s
```

### 5.6 P2：蓝牙 watcher 轮询间隔可配置 + 冷启动补采（2026-10-19 01:35–13:10）

**配置**：`config.json:bt_watch_ms`（最终默认 **30000**，范围 2000–300000；中间版本曾用 10000），运行时可用环境变量 **`PB_BT_WATCH_MS`** 覆盖（优先级高于 config，低于 2000 会被夹到下限）。`collector/btconfig.py` 在 `Collector.start()` 创建 watcher 前，把 `providers.bluetooth.WATCH_SCRIPT` 模块属性中的 `Start-Sleep -Milliseconds 2000` 替换为配置值——**上游 `providers/bluetooth.py` 源码保持原样**（pytest 直接读源文件断言仍含 2000）。实际生效值写入 `logs/collector.log` 与 `stat.providers.bluetooth.watch_ms`。

**F3 冷启动补采**（同一条 patch，01:35 初版 → 12:45 修正门控）：首次 `Emit` 后，**只要 `roots.Count > 0` 就一次性**用现有 `$queue/$due` 机制排入 `now+3/+8/+15/+30` 秒四次补采；后续不再重复，连接变化仍沿用原有 `0/3/8/15` 秒逻辑（pytest 断言两者共存且顺序正确）。门控刻意**不用**“有 status OK 的配对设备 / props 有缓存电量”：独立复验发现只剩 WH-1000XM4（已配对、离线、status OK、带缓存电量）时旧门控会误判健康，而唯一可读的 FlyDigi 不在首快照里。产品折衷：连接/断开检测延迟上限从 2 s 变为 **30 s**；电池快照仍为事件触发 + 3/8/15 s 重试 + 60 s 全量；每次启动多 4 次 snapshot 的一次性成本可接受。

**CPU 证据**（本机 Win11，`tools/collector_bt_cpu.ps1`，真实模式隐藏态，PowerShell 子进程 + 采集器树，60 s 采样）：

| interval | python | PowerShell | 采集器树 |
|---|---|---|---|
| 2000 ms（旧上游基线） | 0.104% | **0.547%** | **0.651%** |
| 10000 ms（中间版本） | 0.104 / 0.182 / 0.26%（采样噪声） | **0.234%**（三次一致） | **0.339 / 0.417 / 0.495%**（均值 ≈0.42%） |
| **30000 ms（最终默认）** | 0.208 / 0.026% | **0.130 / 0.078%** | **0.339 / 0.104%** |

PowerShell 相对旧基线降 **−76%～−86%**（0.547%→0.078–0.130%），达到 ≤0.2% 目标；采集器树两次 60 s 采样 0.104%/0.339%，均 <0.5%；`timers_per_s=0.99`（≤1）。10 s 版本未达 PS≤0.15% 的问题由 30 s 参数解决。彻底去掉 PowerShell 常驻仍需 §3.4 的 CfgMgr32/WinRT 直读（M3）。

**真实冷启动验证**（**packaged `dist\collector\collector.exe`（13:10:58 构建，默认 30000 ms）**，隐藏态全新进程 ×10；秒数 = app start→首条事件）：

| # | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 首条 tray/device | 1.38 | 1.42 | 1.39 | 1.39 | 1.34 | 1.33 | 1.36 | 1.33 | 1.45 | 1.33 |
| 首条蓝牙设备 | 1.38 | 1.42 | 1.39 | 1.39 | 1.34 | 1.33 | 1.36 | 1.33 | 1.45 | 1.33 |

**10/10 ≤30 s（实际 10/10 ≤1.45 s）**。更早的 10 s 构建同口径 10 次曾出现 2 次首快照漏 FlyDigi（首条 tray-state 3.75–4.31 s、首条蓝牙 12.42–13.06 s），均由 +3/+8/+15/+30 补采接住，证明门控有效。补采门控另有 PowerShell 片段回归：只有离线配对设备（status OK + 缓存电量 + roots 非空）时 offsets=3,8,15,30；roots 为空时 queue=0。打包版 `--probe --duration 15` 首次设备 1.38 s。

**交付**：`dist\collector\collector.exe` 最终重建于 **2026-10-19 13:10:58**（19.4 MB，PyInstaller onedir），打包版 fake/real 冒烟均 exit 0。

### 5.5 仍未修 / 未验证

- **F6** 蓝牙常驻 PowerShell 仍有 ~0.13% CPU / ~136 MB（30 s 间隔）；`CfgMgr32` 直读未做（M3，需真机验证）。
- **WGI 常驻 helper** 未做，xinput 仍可能 spawn PowerShell ≤20 s（watchdog/round 门控只保证不误报 gone）。
- suspend/resume `new_session()`、offline 告警仍未接（M0 外）。
- 真实 HID 外设仍无（只有 FlyDigi 蓝牙），logitech/PS/Steam 的真机 key 行为与 P9 24 h 统计未验证；provider 侧 transport 字段（M2）未做。
- Job Object 在真实 Rust 应用内是**嵌套 Job**，Windows 11 允许但受 Rust Job 配置影响；失败时仅 registry 兜底（本机独立进程场景已验证）。
- P3 剩余：History 无锁、迁移时锁内 fsync、fake 临时目录不清理、跨 provider 同 legacy key、provider 异常置空快照、build id/PyInstaller `--noconsole`、日志双写等，未在本轮改动。

---

## 6. 建议（M3/M4 排序）

1. **F6 + F13**：`CfgMgr32 CM_Get_DevNode_PropertyW` 直读 `DEVPKEY_Bluetooth_Battery` + WinRT/设备属性查连接态，替换常驻 PowerShell；WGI 改常驻 helper。目标 P3<0.8%、P4 降 ~130 MB。
2. **F12**：接 `WM_POWERBROADCAST`（恢复后新 session + HID 重开 + 全量重发）；实现 offline alert。
3. **F14/F15**：PyInstaller onedir 打包实测（`--noconsole` 下 stdout None、杀软误报）、24 h key/内存长稳、P9 自动统计。
4. 真机全覆盖：logitech 双接收器/双同型号、PS 蓝牙↔有线换形、xinput 双同型号、Steam 多槽位，验证 F2/F10 的真实迁移质量。
