# 采集器独立审计报告（`collector/`，M0 基线 + 并行修复复核）

> **本文件与 `design/audit/collector-audit.md` 的关系**：后者由并行修复任务在 00:58 创建（内容是 F1–F16 的修复记录）；本文件是独立审计员的只读审计结果，写入不同文件名以免覆盖并发工作。若需要合并/改名请 Lead 决定；本报告不改任何源码/测试/配置。
>
> - 审计对象：`collector/`（`collector.py` / `state.py` / `keys.py` / `protocol.py` / `transport.py` / `settings.py` / `history.py` / `fake.py` / `merge.py` / `__main__.py` + `providers/` 35 个模块）、`tests/collector/`、`scripts/build-collector.ps1`、`dist/collector/`。
> - 契约基准：`BUILD_PLAN.md` v1.1（P1–P10、§1.1 stdio、§3.3 调度/TTL、§3.4 蓝牙/WGI、§4 数据层、§9-M0）、`protocol.md` v1、`HANDOFF.md` §3/§4。
> - 审计方式：只读逐行审阅 + 58 项 pytest + `dist\collector\collector.exe` 冒烟 + 真实模式短时运行 + `%TEMP%\pb-audit\` 下独立复现脚本（毫秒级纯内存脚本，不访问真实 HID）。**未修改** `collector/`、`tests/`、`ui/`、`src-tauri/`、任何配置。
> - **基线与快照说明**：我审阅/复现的 M0 基线是 00:20–00:30 的源码（`collector.py` mtime 00:20:46、`keys.py`/`state.py` 00:20:46）。审计期间（00:58:12–00:59:0x）另一个修复任务并行修改了 `keys.py`/`state.py`/`collector.py` 并实施 F2–F5。本报告：**P1/P2 的“基线证据”来自我自己的复现脚本；凡是标注“当前已修/未修”的，是我在 00:59 用同一脚本对当前工作区重新跑过的结果**（源码仍在变化，行号以我引用时的当前文件为准；基线行号会另行标注）。
> - 运行环境：Windows 11 build 26300，Python 3.12.9（`.venv`），hidapi 已装；本机无受支持 HID 外设（MCHOSE Ace 68 Turbo 16K 无 vendor collection、XInput 无手柄），有蓝牙设备 **FlyDigi BS2PRO（level 94）**。
> - 基线证据：`pytest tests/collector -q` → `58 passed in 4.47s`；`dist\collector\collector.exe --probe --fake --scenario demo --speed 8 --duration 6` → exit 0、`REPLACES logitech:046d:c547:u1`、`key migrations: 1`；`--once` 真实模式 exit 0；真实 `--stdio` 15 s 后收到 `device bluetooth:0000:0000:c817f5498e4e "FlyDigi BS2PRO" level=94`。

## 结论摘要

- **未发现 P0。** 未发现死锁、provider 结果丢失、或当前代码路径下稳定复现的 `gone` 误报。
- 基线 **P1 共 3 条**。其中 **P1-1（真实模式 logitech key 升级不发 replaces）已在并行修复 F2 中修复，我用基线复现脚本复核通过**（但仍有两个未覆盖角落：hidden 设备、hw-key→hw-key，见 P2-5/P2-6）。
- **仍为 P1（截至 00:59 未修）**：
  1. **快速重枚举（xinput slot / steam interface）重新分配 ordinal key** → 非物理原因的 key 变化，直接违反 M0/P9（`repro9` 仍复现：10 s 换 slot → `u1`→`u2`，无 replaces、migration=0）。
  2. **蓝牙 watcher 失败后的“每分钟兜底轮询”实际约每 2 s 一次**（`poll_interval` 从未使用，当前 `collector.py:129-131` 仍是每轮 `_poll_once()` + `wait(2.0)`）。
- **P2 仍开**：单个 provider 挂死 → 任务永久 in-flight、设备冻结且不再轮询（daemon 池没有解决这个）；hw-key→hw-key 变化无 replaces（`repro5` 仍复现）；key 迁移不迁移用户 names/hidden（`repro6` 仍复现）；真实模式 `transport` 恒 `unknown`；WGI 仍每 8 s spawn PowerShell；suspend/resume、`offline` 告警、CfgMgr32 直读仍未做。
- 并行修复复核：**F2 已复核通过（本报告 P1-1，含 hidden/hw-key 两个未覆盖角落）**；**F3/F4/F5 静态看已改**（daemon 池 + watcher 子进程 kill；refresh 标志延迟消费；`run_once` 等待蓝牙首快照 4 s），但 F3 仍不跟踪 `subprocess.run` 子进程、F5 之后真实 `--probe --duration` 仍是一次性（`run_for` 未接线），且 `tests/` 尚未新增回归用例（截至 00:59）。
- 协议信封层面基本正确（见 §五逐项表）：字段集合/类型、`session/seq`、`replaces` 只在 `device`、`gone` 只由全局 tick、`level=null` 透传、命令 `set/get/refresh/shutdown` 均实现；主要偏差是“真实模式语义/覆盖”而非字段。

### 严重度定义

| 级别 | 含义 |
|---|---|
| P0 | 崩溃/数据丢失/安全问题/核心功能完全不可用 |
| P1 | 主要功能缺陷：默认路径可用性受损，用户可稳定复现，必须修 |
| P2 | 明确 bug 或契约偏差，影响体验/稳健性，建议本轮修 |
| P3 | 边界/加固/文档/测试缺口，可排期 |

---

# 一、P1（必须修）

## P1-1 真实模式 logitech slot→unit key 升级：基线不发 `replaces`、生成 `pid=0000` 新 key、同一设备短暂两行 —— **当前已由并行 F2 修复，复核通过（附 2 个未覆盖角落）**

- 基线与证据（修复前）：`collector/keys.py`（基线 `parse_legacy` logitech 分支，当前 `:180-193` 附近）对 `logitech:<unit>` 只解析出 unit，pid 保持 `0000`；基线 `_resolve_new` 用精确 fingerprint（provider|vid|pid|name）找旧 alias，`pid` 不匹配 → 新分配 key。实时证据（`%TEMP%\pb-audit\repro1_logitech_nohints.py`，修复前）：
  ```
  == real mode: hints_for=None
  round2 device: [('logitech:046d:0000:unit-0a1b2c3d', None)]
  round2 snapshot keys: ['logitech:046d:c547:u1', 'logitech:046d:0000:unit-0a1b2c3d']
  migrations: 0
  ```
  fake 之所以正确，是因为 `fake.FakeDevice.hint()`（`collector/fake.py:47-52`）给 unit key 补了 `vid/pid/unit_id`；真实 `LogitechProvider`（`collector/providers/logitech.py:399`）的 unit key 不带 pid，而真实模式 `__main__.py:168-172` 传 `hints_for=None`。
- 当前状态（00:59 复核，同一脚本）：`logitech:046d:c547:unit-0a1b2c3d` + `replaces=logitech:046d:c547:u1`，snapshot 只剩一行，`migrations=1`（`reason=same-round-key-change`）。**修复通过。**
- 修复实现（当前 `collector/keys.py:463-521/559-600`）：新增 `_visible_migration()`——按 provider + 归一化 name + 兼容 vid/pid（`0000` 通配）+ 旧 legacy 不在 live + 旧 stable 仍 visible、且**唯一候选**时 `_adopt()` 发 replaces。当前 `collector/state.py:256-331` 传入 `visible_stables`、`_recently_gone`/`RECENT_GONE_WINDOW` 让刚 gone 的设备也能被接管。
- 仍未覆盖的角落（建议补回归）：
  1. **hidden 设备**：`_visible_migration` 要求 `stable in visible`，而 `visible` 只含 `_devices` + 最近 gone，**不含用户隐藏的设备** → 隐藏设备升级 key 时不发 replaces、也不迁移 hidden 设置，会以新 key 重新出现（我当前复现：`repro6_prefs_lost.py`：`events: [('logitech:046d:c547:unit-...', 'G502 X PLUS', None)]`，旧 hidden/rename 仍在旧 key 上）。
  2. **hw-key→hw-key**（旧 stable 不是 ordinal）：`_visible_migration` 只遍历 `_ORDINAL_RE` 的 stable → PlayStation USB instance 变化仍无 replaces（P2-5，`repro5` 仍复现）。
  3. 两个同型号都在 visible 时按设计不发 replaces（歧义，可接受），但应至少有日志/测试覆盖。
- 期望行为/建议：在 `_apply` 检测到 `resolved.replaces` 时迁移 `settings.names/hidden`（P2-6）；把 `_visible_migration` 的候选从“旧 stable 是 ordinal”放宽到“同 fingerprint 的非 live alias”（P2-5）；给真实模式（`hints_for=None`）+ hidden + 往返 polling 各加一条回归（`tests/collector/test_keys.py` 的迁移用例全部带 hints，掩盖了基线问题）。

## P1-2 快速重枚举（xinput slot / steam interface）重新分配 ordinal key（P9/M0 抖动）—— **仍未修（00:59 复现）**

- 位置：`collector/keys.py:37`（`STALE_AFTER=30.0`）、`:463-521`（`_resolve_new`）、`:669-682`（`_stale_by_fingerprint`：`now-last_seen <= STALE_AFTER` 直接跳过）、`collector/providers/xinput.py:387`（`xinput:{slot}`）、`collector/providers/steam.py:239`（`steam:{pid}:{iface}`）、`collector/keys.py:229-232`（steam 解析明确“the rest is a slot: not identity”）。
- 复现/证据：`%TEMP%\pb-audit\repro9_xinput_slot_jitter.py`（纯 KeyStore，当前代码）：
  ```
  controller in slot 0 -> xinput:0000:0000:u1
  same controller in slot 1 (10 s later) -> xinput:0000:0000:u2 | replaces = None
  after 200 s, slot 0 again -> xinput:0000:0000:u1 | replaces = None
  aliases: {'xinput:0': '...:u1', 'xinput:1': '...:u2'}   migrations: 0
  ```
  物理设备没变，只是重新分配了 XInput slot（关机再开、适配器顺序、控制器重新配对、Steam 控制器换 interface 都会）；10 s 内旧 alias 的 `last_seen` 未过 30 s，`_stale_by_fingerprint` 拒绝复用 → 新 ordinal。
- 实际行为：新 key 立刻出现，旧 key 的行要等 `max(2×TTL,10s)` 才 gone；无 `replaces`、无 migration 记录（P9 自报 `key_migrations` 漏记）；UI 丢位置；设备回到旧 slot 时又用回旧 key → 同一物理设备在 key 间来回。另：`xinput` 多手柄时 name 被自动加 ` 1`/` 2` 后缀（`xinput.py:385-386`），连接顺序变化会改变 fingerprint，也会触发同类抖动。
- 期望行为：旧 upstream key 已不在本轮 live 集合、且该 fingerprint 只有唯一非 live alias 时，**复用旧 alias 的 ordinal**（不受 30 s 限制）；若必须换 key，走 `_adopt()` 发 replaces + 记 history。
- 建议修法：`_stale_by_fingerprint` 的 30 s 条件只保留给“多候选消歧”；`_resolve_new` 复用唯一 non-live 候选（理由：旧 legacy key 不 live 已足够说明设备不在本轮快照里）；`xinput`/`steam` 的 name 后缀不要进入 fingerprint（或在 fingerprint 里剥离末尾 ` \d+`）。

## P1-3 蓝牙 watcher 失败后的兜底轮询实际约每 2 秒一次（`poll_interval` 完全未使用）—— **仍未修（00:59）**

- 位置：`collector/collector.py:95-183`（`BluetoothMonitor`）：`poll_interval` 在 `:100/:105` 保存但全工程无引用；主循环当前 `:120-131` 在 `watcher is None` 时每轮 `_poll_once()`，然后固定 `self.stop_event.wait(2.0)`；类 docstring（`:96`）和 `__main__` 的意图仍是“one-shot polls once a minute”。
- 证据：`grep -rn "poll_interval" collector/` 仅定义/赋值两行；`BluetoothProvider._query` 的 `subprocess.run(timeout=60)`（`providers/bluetooth.py:314-335`）说明单次轮询很贵。
- 实际行为：watcher 起不来（PowerShell/WinRT 工具链问题）或 `BluetoothWatcher.failed`（退出/连续 stall）后，每 ~2 s + 一次 PowerShell（60 s 超时上限）去查 PnP/WinRT；按 `BUILD_PLAN §3.4/§0` 的 P3（含 PowerShell 子进程 <0.8%）几乎必然爆表，日志被 `[Bluetooth]` 刷屏（`providers/bluetooth.py:344-345` 每 poll 逐行 `log.info`）。而正常路径的 watcher 本身已由 tauri-dev 实测占 ~0.52% CPU / ~136 MB（见并行报告 F6），兜底路径更糟。
- 期望行为：兜底真正每 `poll_interval`（默认 60 s）一次；watcher 健康时零轮询。
- 建议修法：主循环区分监督（2 s wait）与兜底轮询（`next_poll` 时间戳，`poll_interval` 到了才 `_poll_once`）；把兜底次数/时延写进 `stat`，便于 P3 观测。

---

# 二、P2（建议修）

## P2-1 基线：退出不 join provider、不杀子进程；**当前 F3 已改 daemon 池 + watcher 子进程 kill，残余风险在 `subprocess.run` one-shot 子进程**

- 基线证据：`repro3_exit_waits.py` → `stop()` 0.02 s 返回、进程 6.139 s 才退出（ThreadPoolExecutor 非 daemon 被 atexit join）；`repro3b_driver.py` → 父进程被 TerminateProcess 后 PowerShell 仍写 marker（孤儿）。
- 当前代码（00:59 复核）：`collector/collector.py:28-71` 新增 `DaemonPool`（daemon 线程，`close(0.3)` 后放弃阻塞 poll）；`Collector.stop()`（`:243-252`）现在 `_bt_monitor.stop(timeout=1.0)`；`BluetoothMonitor.stop()`（`:154-183`）会 `terminate()`/`wait(0.5)`/`kill()` 兜底。**进程退出阻塞问题基本解决**（daemon 线程随解释器退出）。
- 残余风险：`providers/wgi.py:116-120` 与 `providers/bluetooth.py:318-322` 仍用 `subprocess.run`，collector 没有句柄可 kill；父进程被强杀时这些 PowerShell 孙进程仍在跑（Rust `Job Object` 能兜底，但 `sidecar.rs:325-341` 在 CreateJob/Assign 失败时只 log warning）。建议把这两处改成 `Popen` + `ChildRegistry`，`stop()` 里统一 terminate/kill；`--diag` 的 provider 轮询同理。
- 期望：shutdown/EOF 后 <2 s 退出且无子进程残留（P10）。

## P2-2 基线：`refresh`/`refresh(provider)` 在 round 进行中被静默丢弃 —— **当前 F4 已改（静态复核通过），待回归测试**

- 基线证据：`repro2_refresh_lost.py` → round 中 refresh：`fast_polls=1`（期望 2）；round 结束后仍为 1（请求永久丢失）。
- 当前代码：`collector/collector.py:294-321` 只在 `not round_active` 时消费 `_wake_all/_woken`（注释明确“refresh during round stays queued”），并新增 `fast_polls` 计数（`:227/:315`）。静态看已修复；`tests/` 截至 00:59 未新增对应回归。
- 残余建议：`handle_command("refresh")` 仍先 `wake()` 再立刻 `on_tick()`，tick 是旧快照；协议要求 refresh “立即轮询全部 provider（并回 tick）”，可考虑 wake 后短等一次新 merge 再 tick，或接受“tick 立刻、数据随后 device”的语义并在 protocol.md 明确。

## P2-3 基线：真实 `--once`/`--probe` 不含事件驱动蓝牙；**当前 F5 已让 `run_once` 等首个蓝牙快照（≤4 s），但真实 `--probe --duration` 仍是一次性**

- 基线证据：`--once` 真实 0.198 s 退出、hello+tick 全空；`--probe --duration 10` 0.187 s 退出、`(no devices)`；而 `--stdio` 15 s 能看到 FlyDigi 94%。
- 当前代码：`collector/collector.py:455-496` 新增 `wait_for_bluetooth(4.0)`，`run_once()` 在 HID ready 后等待并 `wait_until_settled(2.0)`；`state.py:213-230` 新增 `bluetooth_snapshots` 计数。静态看 `--once` 现在会包含蓝牙（需实测确认；若 watcher 启动 >4 s 仍会空）。
- 仍未修：`collector/__main__.py:211-227` 真实 `--probe` 仍直接 `run_once(timeout=90)`，`--duration` 只用于 fake 分支；新加的 `Collector.run_for()`（`collector.py:498-512`）未被接线 → HANDOFF §3.5 的“真实 `--probe` 观察插拔/休眠恢复”仍做不到。建议 `run_probe` 真实分支改用 `run_for(duration)` 并流式打印（`ProbeWriter`）。

## P2-4 单个 provider 挂死 → 任务永久 in-flight、设备冻结且不再轮询；daemon 池只解决退出、不解决冻结

- 位置：`collector/collector.py:294-337`（due 只挑 `not task.in_flight`；`force_merge` 只清 `_pending`/round，**不清 in_flight**）、`:339-370`（`_run_task` 是唯一清 `in_flight` 的路径，无 per-task 超时）、`collector/state.py:163-168`（`set_in_flight`）、`:398-423`（`_expire` 对 in-flight provider 设备 `continue`，永不 gone）、`:256-331`（每次 merge 刷新快照内所有设备 `last_seen`）。
- 实际行为：任一 provider 的 `poll()` 卡住（HID open/read 无超时、驱动/`hidapi` 卡死、PowerShell 卡住）→ 该任务永远 `in_flight`，调度器永远跳过它；`_expire` 永远保护其设备；每次 merge 又刷新 last_seen → 设备“在线冻结”，不再更新、永不 gone；worker 被永久占用（4 个卡住 = 线程池耗尽，后续任务只排队）。`force_merge` 每 90 s 只清 pending/round，不取消任务，也不清 in-flight。
- 期望：per-task watchdog（超时清 in_flight + 标记 provider 失败 + 让设备走 TTL/gone 或发 `offline`）+ `force_merge` 清理 stale in-flight；线程池至少能感知“4 个槽都超时”。
- 建议：`ProviderTask.started_at` + `_scheduler_wake` 检查超时；`state.mark_provider_failed(name)` 从 in-flight 移除；`stat` 自报 in-flight 时长。

## P2-5 hw-key→hw-key 变化（PlayStation USB instance 等）无 `replaces`/migration —— **仍未修（00:59 复现）**

- 位置：`collector/keys.py:463-521`（`_resolve_new`）、`:559-600`（`_visible_migration` 只接受 `_ORDINAL_RE` 的旧 stable）、`:669-682`（`_stale_by_fingerprint` 同上）、`collector/providers/playstation.py:238-243`（单只 USB 无 serial 用 `usb-`+`_instance(path)`；两只同型号 USB 时 ident=USB instance）。
- 复现/证据：`repro5_hwkey_change.py`（当前代码）：
  ```
  after port change: stable = playstation:054c:0ce6:usb7&33cc&0&0000 | replaces = None
  migrations: 0
  ```
- 实际：两只同型号 PS 手柄在 USB 上时 key 含 USB 拓扑 instance（不是 serial/mac/unit_id）；换口/重新枚举后新 key 立刻注册，旧行等 TTL 后 gone；无 replaces、P9 漏记；换回原口旧 key 又回来。
- 期望：同一物理设备（同 provider/vid/pid/name）hw-key 变化也命中 replaces + migration history。
- 建议：`_visible_migration`/`_stale_by_fingerprint` 的候选放宽到任意 stable（不止 ordinal）；对 PlayStation 明确 USB instance 只是消歧标识，若 hidapi serial_number 为空则视为“无硬件 id”走 ordinal+replaces。

## P2-6 key 迁移不迁移用户 names/hidden：隐藏设备迁移后重新出现、改名恢复硬件名 —— **仍未修（00:59 复现）**

- 位置：`collector/settings.py:281-320`（names/hidden 以 stable key 为 map key）、`collector/keys.py:616-635`（`_adopt` 只改 aliases/history）、`collector/state.py:303-321`（`_apply` 的 replaces 只清旧 `_devices`/告警/最近 gone，**不迁移 settings**）。
- 复现/证据（当前代码）：`repro6_prefs_lost.py`：
  ```
  visible after hide: []
  after migration, events: [('logitech:046d:c547:unit-...', 'G502 X PLUS', None)]
  visible: ['logitech:046d:c547:unit-...']
  hidden setting still says: {'logitech:046d:c547:u1'}
  ```
  （注：hidden 设备不在 `visible` 里，连 replaces 都不会发——与 P1-1 修复的 visible 条件叠加。）
- 期望：replaces 时把 `names[old]→names[new]`、hidden 列表迁移并持久化；否则用户隐藏的设备会重新出现（隐私/体验问题），改名丢失。
- 建议：`state._apply` 检测 replaces 时调用 `settings.migrate_key(old,new)`（新增）；或让 `KeyStore` 只产出迁移事件、由 state 统一处理。

## P2-7 真实模式 `transport` 恒 `unknown`（xinput/steam 恒 `usb`），fake hints 掩盖

- 位置：`collector/state.py:59-71`（`infer_transport` 只按 source/key/name 猜；`key.startswith("steam:/xinput:") → "usb"`）、`collector/providers/base.py:11-28`（`DeviceStatus` 无 transport 字段）、`collector/fake.py:47-52`（fake hint 提供 transport）。
- 证据：`repro8_kind_transport.py`（真实上游 key/name，无 hints）：logitech/razer/keychron 全部 `transport=unknown`；Xbox/Steam 为 `usb`；只有蓝牙 source 正确。
- 实际/期望：`protocol.md §1` 的 transport 枚举要求 `2.4g|usb|bluetooth|unknown`；真实模式下除 BT 外几乎全 unknown，Xbox 无线适配器/Steam puck 被误标 usb。M2 的 transport 映射没有数据来源。
- 建议：`DeviceStatus` 增加 provider 侧 `transport`/`via`（WGI 已有 `IsWireless`；logitech 按 receiver/unit 路径；Steam 按 puck；mchose/razer 按打开的 collection），`infer_transport` 仅兜底；不要只在 fake hints 里给。

## P2-8（计划偏差确认）WGI 非法常驻 helper：xinput 仍每 ~8 s spawn PowerShell

- 位置：`collector/providers/wgi.py:111-127`（`subprocess.run(["powershell", ...], timeout=20)`）、`collector/providers/xinput.py:38-39`（`WGI_REFRESH=8`）、`:214-227`（`_wgi` 8 s 缓存）。
- 实际：`BUILD_PLAN §3.4` 要求“改一个常驻 helper，8 s 一次 stdin 命令”；当前有手柄连接时每 ~8 s 新起 PowerShell（60–100 MB 峰值），直接计入 P3/P4。建议实现 `WgiHelper`（Popen 常驻 + stdin 命令/JSON 行）并复用 `BluetoothWatcher` 的 supervisor 模式；短期在 `stat` 自报 WGI 子进程次数/耗时。

## P2-9（计划偏差确认）CfgMgr32 直读 / suspend-resume / offline 告警未实现

- 蓝牙：`collector/providers/bluetooth.py:34-94`（PowerShell `Get-PnpDevice`/`Get-PnpDeviceProperty`）、`:107-131`（每 60 s/连接变化跑全量快照）——`BUILD_PLAN §3.4` 要求的 `ctypes`+`CfgMgr32` 直读未做；P3 成本仍在子进程。
- suspend/resume：`collector/protocol.py` 的 `new_session()` 全工程未调用，没有 `WM_POWERBROADCAST`/恢复后全量重发；`BUILD_PLAN §8/§1.3` 未落地（HID 每 poll 重开可缓解数据问题，但 UI 的 session 语义/全量对齐缺失；并行报告 F12 已列为 M0 外）。
- `offline`：`protocol.py:23` 的 `ALERT_KINDS` 含 `offline`，`protocol.md §1` 也列了，但 `state._check_alerts`（当前 `:350-377`）只发 low/full，全工程无 `alert("offline",...)`。
- 建议：WGI helper/蓝牙直读排 M1 前；suspend/resume 至少“App 恢复后 refresh + collector 检测 wall/monotonic 大跳变时 `new_session()` + 全量 tick”；offline 告警接 P2-4 的 provider 失败事件。

---

# 三、P3（边界/加固/文档/测试缺口）

> 这些行号大多对应未改动文件（`transport.py`/`settings.py`/`history.py`/`__main__.py`/providers）；`state.py` 行号按当前文件。

## P3-1 `read_commands` 遇到超长“多字节 UTF-8”行会终止整个 stdin 读取（违反 protocol §0/§1.1(5) 的“丢行并重新同步”）

- 位置：`collector/transport.py:283-331`，关键分支 `:305-318`（按字节 `readline(max_line+1)`，却用解码后的字符数 `len(text)` 判断超长；不超长则 `return` 当 EOF 残片）。
- 复现：`repro4_read_commands_utf8.py`（`max_line=64`）：ASCII 超长行 → `['shutdown']`（正确重新同步）；CJK 超长行 → `[]`（整个流被当 EOF，后续 shutdown 丢失）。触发需 >1 MiB 且含较多 3/4 字节字符；触发后 `serve()` 循环结束 → 进程退出。附带 off-by-one：`max_line+1` 字节且以 `\n` 结尾的行会被接受。
- 期望/修法：一律按字节长度判断；含 `\n` 且超 cap → 丢行；不含 `\n` 且超 cap → drain 到换行；解码用 incremental decoder。

## P3-2 P8 自报 `timers_per_s` 只算 scheduler；蓝牙监视线程另有 0.5 次/s（兜底更高）

- 位置：`collector/collector.py:272-292`（`_scheduler_loop`/`_record_wake` 只统计 scheduler）、`:379-389`（`timers_per_second` 附近）、`:95-137`（`BluetoothMonitor` 每 2 s `wait`）。`BUILD_PLAN §0-P8` 要求 ≤1 次/s（自报 + ETW 校验）；scheduler 1/s + monitor 0.5/s 会超，而自报仍 ~1.0。修法：把 monitor wait 计入 `_wake_times`/`stat`，或让 scheduler 统一驱动蓝牙监督。

## P3-3 TTL/poll 配置不一致；`poll.bluetooth` 无效；fake 注释与实现不符

- 位置：`collector/settings.py:32-39`（`poll.bluetooth=10`）、`:255-266`（`ttl_for("bluetooth")` 硬编码 15）、`collector/collector.py` 的 `ttl_for`（真实模式 watcher 存活 60/兜底 90，**完全绕过配置**）、`collector/fake.py:214-217`（demo 注释写 “Bluetooth TTL 10 s → gone ~20 s after t=8”；实际 fake TTL 15 → 阈值 30 s，实测 gone 在 t≈35.7）。修法：蓝牙 TTL 走 `poll["bluetooth"]` 或把真实 60/90 写进配置并允许覆盖；更新注释。

## P3-4 稳定 key 字符集未清洗：PlayStation USB instance 生成带 `&` 的 key，项目自带 NDJSON 校验器 FAIL

- 位置：`collector/keys.py:84-88`（`_norm` 去掉 `[\s\-_.:/\\{}\[\]]`，保留 `&`/`#`）、`collector/providers/playstation.py:238-243`、`tools/collector_ndjson_check.py:30-31`（`KEY_RE` 只允许 `[a-z0-9]+|unit-[a-z0-9]+|u[0-9]+`）。
- 证据：喂一条合法事件流（key=`playstation:054c:0ce6:usb7&11aa&0&0000`）→ `FAIL line 2: device key not provider:vid:pid:suffix`。`protocol.md §3` 未定义字符集，但项目自带校验器已收紧；UI 用 Map/dataset 暂不受影响。修法：suffix 输出前把非 `[a-z0-9]` 哈希/替换，或在 protocol.md 明确字符集并同步校验器；与 P2-5 一起改。

## P3-5 `History` 无锁：退出时 `history.save()` 与仍在跑的 provider 线程 `record()` 竞态

- 位置：`collector/history.py:73-89`（save 遍历 `self.devices`，无锁，只 catch OSError）、`:92-120`（record 无锁）、`collector/__main__.py:256-259`（serve 返回后直接 save）。基线退出要等 6 s（P2-1），当前 daemon 池让进程更快退出反而降低窗口，但 `run_once`/`run_for` 仍可能在 worker 未结束时就 save。修法：`History` 加 RLock，save 在锁内快照；或 stop 先 join/取消 worker。

## P3-6 迁移时 `KeyStore.save(force=True)` 在 StateStore 锁内 fsync，慢盘 stall 所有 provider/命令线程

- 位置：`collector/state.py:268-278`（`_apply` 在锁内 `keys.resolve`）、`collector/keys.py:616-635/637-661`（`_adopt`/`_migrate` 内 `save(force=True)`，mkstemp+fsync+replace）。修法：迁移只置 dirty，锁外异步/合并保存。

## P3-7 全 provider 禁用时 `--once`/`--probe` 等满 120 s 才退出（exit 2）

- 位置：`collector/__main__.py:230-246`、`collector/state.py:177-198`（无 gated provider 时 `_pending` 初始空，`_ready` 只在 `_merge_and_apply` 置 True；`begin_round` 不会被调用）。`settings.providers=[]` 是合法配置（`settings.py:127-129/274-278`）。修法：`_gated` 为空或 `--once` 时直接 ready + 空 tick。

## P3-8 fake provider 的“乱序到达”实际不存在（delay 恒 0），`--fake` 从未压测门控；且每次 `--fake` 留临时目录

- 位置：`collector/fake.py:14-17`（docstring 声称乱序）、`:98`（未传 delay）、`:136-141`（`if self.delay`）、`collector/__main__.py:130-138`（mkdtemp 不清理）。建议 demo 给 provider 0–50 ms 随机 delay，fake 结束 `shutil.rmtree`（或固定 data-dir）。

## P3-9 首次看到 `level=100 + charging=true` 就发 `full` 告警，与 `_check_alerts` 注释语义不符

- 位置：`collector/state.py:367-375`（注释 “only for a device that was seen charging below 100%”，条件 `(charging or key in _charging_seen)`）。复现：`repro7_alerts.py` → `first sighting 100% charging -> [('full',100)]`。修法：只允许 `key in _charging_seen` 发 full（首见 100% 时静默 set `_full_alerted`）。

## P3-10 多 provider 共用同一 legacy key 时没有去重（结构隐患；当前 PID 集合不重叠，未稳定复现）

- 位置：`collector/state.py:238-253`（`_merge` 直接拼接，`_key_provider[st.key] = name` 后写覆盖）、`:268-331`（`_apply` 按 list 顺序逐个 resolve，无 legacy/stable 去重）。当前 `steelseries*`/`hyperx*` 的 PID 集合互不重叠，所以不可稳定复现；但新增/上游同步后会出现同轮两次 device、level/charging 互相覆盖。修法：`_merge` 按上游 key 去重（charging/在线优先），或 `_apply` 按 stable key 归并；补单测。

## P3-11 provider 抛异常时 snapshot 被置空（丢失 last-known），设备 TTL 后消失

- 位置：`collector/collector.py:339-352`（异常 → `devices=[]`）、`:360-364`（仍以空列表 `on_provider_result`）。单次瞬态异常会让该 provider 全部设备从快照消失，阈值后 `gone`（设备其实还在）。建议异常时保留上次 snapshot 或标 `offline` + offline 告警。

## P3-12 打包/构建：`build` 恒 `unknown`，版本握手形同虚设；`--noconsole` 未使用（当前靠 Rust CREATE_NO_WINDOW + 管道）

- 位置：`collector/settings.py:323-328`（只读 `PB_BUILD`）、`collector/__main__.py`、`scripts/build-collector.ps1:61-72`（`--onedir`/`--collect-submodules`，无 `--noconsole`、无写 build id 步骤）。
- 验证：`dist/collector/_internal` 含 `base_library.zip`、`hid.cp312-win_amd64.pyd`；`build/pyinstaller/collector/PYZ-00.toc` 含 35 个 `collector.providers.*`（含 `bluetooth`/`wgi`/`xinput`）；打包 exe fake/real 冒烟均 exit 0 → **PyInstaller 数据/动态导入完整**。但 `hello.build` 永远 `unknown`，`BUILD_PLAN §1.2` 的版本过期提示无法工作。修法：构建时写入 build id（`collector/_build.py` 或 `--add-data`），`build_string()` 读取；如需无控制台再评估 `--noconsole`。

## P3-13 其他较小项

- **死代码/未接线**：`Protocol.new_session()`、`StateStore.reset_alerts()`、`settings.wake_on_input`、`settings.PROVIDER_ORDER`、`history.format_left()`、`merge.merge_bluetooth()`、新增的 `Collector.run_for()`（真实 `--probe --duration` 未接线，见 P2-3）。
- **`ProbeWriter` 多线程打印可能交错**（`collector/__main__.py:36-95`，scheduler/provider 线程同时 print；仅开发输出）。
- **fake 时间语义**：`--probe --fake` 的 `updated_unix` 是“真实时间 + 虚拟秒”（会落在未来）；`stat.timers_per_s` 在 fake 下 = speed（4/6），仅开发模式但易误读 P8/P3；建议 summary 标注虚拟时间。
- **`LineWriter` 发送失败只 `dropped += 1`**（`transport.py:250-273`），stdout 关闭/管道破裂时上层无感知，`Protocol` 仍递增 seq，UI 只看到空洞；建议连续失败时 stderr 报错。
- **`RotatingLog` 队列无上限**（`transport.py:116-126`）：日志风暴 + 慢盘时队列/内存只增不减（P7 风险）；建议 maxsize + drop 计数。
- **`collector.log` 双写**：Python stderr 线程（`transport.py:207-228`）与 Rust `stderr_loop`（`src-tauri/src/sidecar.rs:457-488`）都打开/轮转 `logs\collector.log`（shell-audit 已记）。正常时 Python 重定向了 stderr，Rust 文件几乎为空，但两者轮转计数会互相干扰；建议只让一方拥有该文件。
- **`BluetoothWatcher._run` 的 `self._proc.wait()` 无超时**（`providers/bluetooth.py:507`）：PowerShell 忽略 terminate 时监视线程永久卡住、`failed` 不置位；当前 `BluetoothMonitor.stop()` 已加 kill 兜底，但 `_run` 自身仍建议 `wait(timeout)+kill`。
- **`config.json` 与 Rust 的整份读改写竞态**（shell-audit P2-4 已记；collector 侧 `Settings.save` 同样整份写）：`set` 命令与 Rust `set_pinned` 并发会互相覆盖；建议 collector 写前 reload-merge，或拆分文件。
- **xinput/蓝牙 stable key 的 vid/pid 恒 `0000`**（`PROVIDER_VID["xinput"]="0000"`、BT 无 vid）：与 `protocol.md §3`/`BUILD_PLAN §4.1` 的 `provider:vid:pid` 语义相比信息缺失；WGI 已有真实 vid/pid，可注入 provider hints 改善（与 P2-7/P1-2 一起）。

---

# 四、专项结论（对应审计任务的六个重点）

## 1. 并发/门控

- **未发现死锁**：锁序 `StateStore._lock → Settings/KeyStore/Protocol(→LineWriter)`；命令线程、provider 线程、scheduler、蓝牙 watcher 都只在持有 StateStore 锁时调用下游，没有反向获取；`Settings.apply_set` 先释放 settings 锁再取 StateStore 锁。`_apply/_merge/_expire/_emit_tick` 都在同一 RLock 内。
- **未发现 provider 结果丢失**：`on_provider_result` 对 gated provider 先存 snapshot 再判 pending；`on_bluetooth_snapshot` 在轮次打开时先存快照、延后合并的语义正确（快照会在该轮闭合的 merge 里被包含，我核对过“最后一条结果触发 merge 时 BT 已在 snapshot 中”的顺序）。
- **已确认的竞态/丢失**：refresh 在 round 中丢失（P2-2，F4 已修）；`History.save` 与 `record` 无锁（P3-5）；退出时 in-flight worker（P2-1，F3 已缓解）。
- **gone 误报**：当前路径未发现稳定误报。`_expire` 在 `_pending` 非空时整体跳过；`_apply` 每次 merge 刷新快照内所有设备的 `last_seen`（当前 `state.py:276`），所以被慢 provider 拖住的 round 不会误判；“provider in-flight 不 gone”也正确，但这正是 P2-4 冻结的成因。残余风险：蓝牙 watcher 停顿 > `max(2×60,10)=120 s` 时仍连着的 BT 设备会先 gone 再出现（P3 级；`STALL_TIMEOUT=180` 才重启 watcher）。
- **重复 device**：P1-1（已修，hidden/hw-key 两角落仍在）、P1-2（xinput/steam 快速重枚举）、P2-5（hw-key→hw-key）会造成同一物理设备短暂两行；P3-10 是结构性跨 provider 重复（当前未触发）。
- **`force_merge` 与 in-flight 的交互（重点）**：`force_merge` 只在 round 超时（90 s）时被 scheduler 调用，清 `_pending` 并用上一份完整快照 `_merge_and_apply`；它**不清理 `task.in_flight`、不取消 worker、不更新 StateStore 的 `_in_flight`**。于是卡住的 provider：调度器永远跳过（`collector.py:308-313`）、StateStore `_expire` 永远跳过其设备（`state.py:405-406`）、每次 force_merge 的 `_apply` 又刷新全部设备 `last_seen`（`state.py:268-278`）→ **轮次能闭合、日志有警告、但设备冻结且永不 gone**；若连续 force_merge，每次都会给 TTL 续命。

## 2. 稳定 key

- **格式/优先级**：`provider:vid:pid:(serial|mac|unit-<id>|uN)` 基本成立；serial > mac > unit > ordinal 在 `Identity.suffix()`（`keys.py:126-140`）正确；ordinal 只来自 `aliases.json` 的 `next_ordinal`，删除不回收。
- **aliases 持久化**：`load/save`（`keys.py:308-375` 附近）能读写 `version/next_ordinal/aliases/history/migrations`；`save` 用 mkstemp+fsync+replace 原子写；history 上限 200；损坏文件静默回落（会重新分配 ordinal，属可接受灾难恢复）。
- **replaces**：只在 `Resolved.replaces` 非空且不同于新 key 时发（`protocol.py:126-131`），并删旧 `_devices`；基线只覆盖 ordinal→hw-key，**当前 F2 扩展到 same-round-key-change（含刚 gone 的设备），但 hidden 与 hw-key→hw-key 未覆盖**。
- **双设备同型号**：无硬件 id 时靠 fingerprint（provider,vid,pid,name）+ 非 live stale alias 复用；同一快照内第二台分配 u2（正确）。风险：`xinput` 自动加 ` 1`/` 2` 后缀（`xinput.py:385-386`）改变 fingerprint；快速重连 30 s 内换 slot 会新分配（P1-2）。
- **重启后稳定性**：有硬件 id 的由确定性 hw_key 保证；ordinal 的由 legacy→stable 别名表保证（前提 legacy key 稳定：pid/unit 稳定，slot 类不稳定）。`aliases.json` 丢失/损坏会整体重排。
- **fake hints vs 真实无 hints**：fake 的 `hint()` 补了 vid/pid/unit/name/kind/transport，掩盖了 `parse_legacy` 对 logitech unit key 丢 pid（P1-1 基线）、真实无 transport（P2-7）；`tests/collector/test_keys.py` 的迁移用例全部显式传 hints。**建议所有 key 迁移用例都跑一遍 `hints_for=None`。**

## 3. 协议核对表

| 契约 | 实现 | 结论 |
|---|---|---|
| 单行 ≤1 MiB / UTF-8 / `\n` / flush | `transport.py:240-273/32-48` | ✅（超长 stdout 行丢弃计数；UTF-8+flush；stdout=None 有兜底，但打包未实测） |
| stdin 超行丢行重同步；EOF 丢残片 | `transport.py:283-331` | ⚠️ P3-1（CJK 超长行终止整个读取）；EOF 残片正确 |
| stderr 独立线程 + 轮转 | `transport.py:102-228` | ✅；P3 有队列无上限/双写 |
| hello 立即发、protocol/session/build/capabilities/devices/settings、seq=0 | `protocol.py:112-124`；`collector.py:514-517` | ✅（build 恒 unknown，P3-12） |
| 事件 `t/session/seq` 单调 | `protocol.py:91-97` | ✅（hello 0，后续从 1 起；锁内自增） |
| device 只在语义变化发 | `protocol.py:65-72`；`state.py:322-331` | ✅（`left_s` 变化也算语义变化，符合 §1） |
| gone 只由全局 tick 依据 last_seen | `state.py:379-385/398-423` | ✅ |
| tick 30 s / refresh 后回 | `settings.py:93`；`state.py:425-431`；`collector.py:433-441` | ✅（refresh 的“轮询”见 P2-2，F4 已修） |
| alert kind/key/level/text | `protocol.py:140-146`；`state.py:350-377` | ✅ low/full；⚠️ offline 从未发（P2-9） |
| stat 每 60 s | `collector.py` `_maybe_emit_stat` | ✅；⚠️ 不含蓝牙 watcher/子进程，P8 口径见 P3-2 |
| Device 11 字段/类型、level int\|null、approx、left_s、transport 枚举 | `protocol.py:37-60` | ✅；transport 真实质量 P2-7；`_normalize_level` 正确处理 bool/越界 |
| 命令 shutdown/refresh/set/get | `collector.py:414-442` | ✅；refresh 见 P2-2（已修待回归）；set 立即 save+tick |
| session 变化 = 全量重置 | `Protocol.new_session` 未使用 | ⚠️ suspend/resume/版本握手未落地（P2-9/P3-12） |
| `protocol=1`、加字段不升版 | `protocol.py:16` | ✅ |

## 4. 调度/TTL

- **TTL 来源**：`settings.ttl_for_devices`（`settings.py:255-272`）按 provider 类别给 TTL；StateStore 每设备存 TTL，`_expire` 用 `max(2×ttl,10)`。单 provider 维度基本符合 §3.3；但 **round 门控会被最慢 provider 串行化**：首轮 29 个 provider 一起 submit 到 4 个 worker，steam 最多 20 s、WGI 20 s、corsair 7 s/dongle，首轮可能持续数十秒；round 进行中 scheduler 不启动新轮（`collector.py:300-321`），快 provider 的轮询被慢 provider 阻塞。`_apply` 刷新 last_seen 避免了误报 gone，但 UI 数据新鲜度下降，且 refresh 会丢（P2-2，已修）。
- **阻塞窗口**：steam `WINDOW=0.4/BATTERY_WINDOW=5.0`（`steam.py:76-91`，4 slot 最多 20 s）；WGI `timeout=20`（`wgi.py:119`）；corsair 每 dongle 7 s（`corsair.py:146/520`）；bluetooth one-shot 60 s（monitor 线程，不在 round）；其余多 0.4–2 s。**已知 provider 不会超过 round_timeout=90 s**；但 HID `open_path`/驱动卡死不在这些上限内 → P2-4。
- **fake 加速时钟**：`_record_wake`/`timers_per_second` 用真实 monotonic，调度/round/TTL 用 fake（虚拟秒）；fake 下 `timers_per_s=speed`、`_round_timeout=90` 是 90 虚拟秒、`KeyStore._maybe_save` 混用真实/fake 时间、`updated_unix` 未来时间。仅开发路径，但会误导“用 fake 验证 P8”。建议 summary 标注虚拟时间（P3-8/P3-13）。

## 5. 资源/退出/PyInstaller

- **线程**：scheduler（daemon，join 2 s）、`DaemonPool` worker（当前 daemon，close 0.3 s 后放弃）、`collector-bluetooth`（daemon，当前 stop 会 join 1 s + kill 子进程）、`collector-stderr`（daemon，join 2 s）、watcher stdout pump（daemon）。基线退出阻塞 6.1 s 的问题已被 F3 的 daemon 池解决（静态复核）。
- **子进程**：`BluetoothWatcher`（长驻 Popen）当前 stop 有 terminate→wait→kill；但 `wgi.query`/bluetooth one-shot 仍是 `subprocess.run`，无句柄登记、退出不 kill；Rust `Job Object` 兜底（`sidecar.rs:325-341/726-735`），Job 创建/assign 失败只 warning → 残余 P10 风险。
- **文件句柄**：providers 的 `hid.device()` 大多 `finally close()`（upstream 代码）；`hidlist` 不缓存 handle；`RotatingLog` close 时 flush/close；未发现明显 HID/文件句柄泄漏（长稳仍需 ETW/句柄计数验证）。
- **PyInstaller onedir**：验证完整（`PYZ-00.toc` 35 个 providers、`hid.cp312-win_amd64.pyd` 存在；打包 exe fake/real 均正常）。缺 `--noconsole`/build id（P3-12）。

## 6. 测试盲区（58 项没覆盖、容易坏的路径）

- **没有 `tests/collector/test_collector.py`**：`Collector`/`DaemonPool`/`ProviderTask`/`BluetoothMonitor`/refresh/force_merge/in-flight/stop 无单测；现有 `test_stdio.py` 只通过 `--stdio --fake` 间接覆盖，且 fake provider delay=0，几乎不触发乱序/阻塞。
- 无测试：真实模式 `hints_for=None` 的 key 路径（P1-1 基线）、hidden 设备迁移（P2-6）、hw-key→hw-key（P2-5）、快速重枚举（P1-2）。
- 无测试：`read_commands` 多字节超长行（P3-1）、`LineWriter` 发送失败、`RotatingLog` 上限/双写。
- 无测试：provider 抛异常保留 last-known（P3-11）、多 provider 同 legacy key（P3-10）、`force_merge`+in-flight（P2-4）、蓝牙兜底轮询频率（P1-3）。
- 无测试：`--once`/`--probe` 真实路径与事件驱动蓝牙（P2-3/F5 修复）、全 provider 禁用（P3-7）、`offline` alert（P2-9）、P8 计数口径（P3-2）、key 字符集/NDJSON 校验器（P3-4）。
- 无测试：`aliases.json` 损坏/半写、`next_ordinal` 缺失但已有 ordinal 的冲突。
- 建议优先级：先补 `test_collector.py`（可控 fake provider：阻塞/异常/乱序/超时）+ 真实 hints 缺失/隐藏迁移回归（P1-1 修复的边界）+ `repro9` 的快速重枚举用例，再补 stdio/打包边界。

---

# 五、三档清单（截至 00:59 的当前工作区）

## 必须修

1. **P1-2** 快速重枚举（xinput slot / steam interface）重新分配 ordinal key → P9/M0 抖动（`keys.py:37/463-521/669-682`；复现 `repro9` 仍 FAIL）。
2. **P1-3** 蓝牙 watcher 失败后的兜底轮询实际 ~2 s/次（`collector.py:95-137`；`poll_interval` 未用）。
3. **P2-6**（随 P1-1 修复暴露）hidden/rename 在 key 迁移后丢失：隐藏设备重新出现（`repro6` 仍复现；`_visible_migration` 不含 hidden）。

> P1-1（logitech 真实模式 replaces）已由并行 F2 修复并通过我的复核；F3/F4/F5 静态看已修，需要回归测试与实测确认（见“建议修”）。

## 建议修

4. **P2-4** 挂死 provider 永久 in-flight → 设备冻结且不再轮询；force_merge 不清理 in-flight；4 个卡住耗尽池。
5. **P2-5** hw-key→hw-key 变化无 replaces/migration（`repro5` 仍复现）。
6. **P2-7** 真实模式 transport 恒 unknown / xinput·steam 恒 usb（M2 依赖）。
7. **P2-8（已知缺口）** WGI 仍每 ~8 s spawn PowerShell，应改常驻 helper。
8. **P2-9（已知缺口）** 蓝牙 CfgMgr32 直读、suspend/resume 新 session、offline 告警未实现。
9. **P2-1 残余** `subprocess.run` 子进程（WGI/蓝牙 one-shot）无句柄、退出不 kill；依赖 Rust Job Object。
10. **P2-2/P2-3 的回归/收尾** refresh 修复的回归测试；真实 `--probe --duration` 接 `run_for()`（当前是死代码）。
11. **P1-1 修复的两个角落** hidden 迁移（同 3）、hw-key→hw-key（同 5）、两同型号歧义日志/测试。

## 可接受

12. P3-1 stdin 多字节超长行终止读取（触发需 >1 MiB）。
13. P3-2 P8 自报口径（scheduler 1/s + monitor 0.5/s）。
14. P3-3 `poll.bluetooth`/fake 注释不一致；P3-4 key 字符集/校验器；P3-5 History 无锁；P3-6 锁内 fsync。
15. P3-7 全 provider 禁用时 `--once` 挂 120 s；P3-8 fake delay/临时目录；P3-9 首见满电 full 告警。
16. P3-10 跨 provider 同 legacy key 结构隐患；P3-11 provider 异常置空快照。
17. P3-12 build id/`--noconsole`；P3-13 死代码/ProbeWriter/日志双写/`wait()` 超时/xinput·BT 的 vid·pid=0000。

---

# 六、复现脚本与当前代码复核结果（脚本均在 `%TEMP%\pb-audit\`，未放入项目）

| 脚本 | 对应发现 | 基线（00:42–00:56） | 当前（00:59 复核） |
|---|---|---|---|
| `repro1_logitech_nohints.py` | P1-1 | `logitech:046d:0000:unit-...`、replaces None、migrations 0、两行 | **已修**：`logitech:046d:c547:unit-...` + replaces + migrations 1 ✔ |
| `repro2_refresh_lost.py` / `repro2b.py` | P2-2 | round 中 refresh：`fast_polls=1`（期望 2） | 静态已修（`collector.py:300-321` 只在非 round 消费 flags）；未重跑（避免测量期启动 collector） |
| `repro3_exit_waits.py` | P2-1 | `stop()` 0.02 s，进程 6.139 s 退出 | 静态已改（DaemonPool daemon 线程）；未重跑 |
| `repro3b_driver.py`+`child.py` | P2-1 | 父进程被杀后 PowerShell 仍写 marker | watcher 子进程 kill 已加；`subprocess.run` one-shot 仍未跟踪 |
| `repro4_read_commands_utf8.py` | P3-1 | ASCII 重同步✔；CJK → `[]` | 未修（`transport.py` 未改） |
| `repro5_hwkey_change.py` | P2-5 | 新 key、replaces None、migrations 0 | **仍复现** |
| `repro6_prefs_lost.py` | P2-6 | hidden/rename 迁移后丢失 | **仍复现**（且现在连 replaces 都不发） |
| `repro7_alerts.py` | P3-9 | 首见 100% charging 发 full | 未修 |
| `repro8_kind_transport.py` | P2-7 | 真实 key/name 下 transport 全 unknown | 未修 |
| `repro9_xinput_slot_jitter.py` | P1-2 | 10 s 换 slot → u2、replaces None、migrations 0 | **仍复现** |

其他证据：`pytest tests/collector -q` 基线 58 passed（4.47 s；`tests/` 截至 00:59 未新增）；`dist\collector\collector.exe --probe --fake --scenario demo --speed 8 --duration 6` exit 0 且 REPLACES/migration 正常；`dist\collector\collector.exe --once --data-dir %TEMP%\pb-real4` exit 0；真实 `--stdio` 15 s 收到 FlyDigi 94%；真实 `--once` 0.198 s 空、真实 `--probe --duration 10` 0.187 s 空（F5 修复前的基线证据）。

---

# 七、明确“未发现问题”的项

- **死锁**：未发现（锁序见 §四.1）。
- **provider 结果丢失**：未发现（gate/snapshot 写入顺序正确；蓝牙延后合并语义正确）。
- **gone 误报**：当前代码路径与 58 项测试下未发现；慢轮次/ in-flight 保护偏保守（副作用是 P2-4 的冻结）。
- **协议字段缺失/类型错误**：Device 11 字段、6 种事件 `t/session/seq`、`replaces`、`level=null`、枚举校验均符合 `protocol.md §1`；`tools/collector_ndjson_check.py` 对 fake 输出可 PASS（除 P3-4 的 `&` key 场景）。
- **打包遗漏**：未发现；PyInstaller onedir 的 providers/hid 完整（`PYZ-00.toc` 35 个 providers + `hid.cp312-win_amd64.pyd`），fake/real 均能启动。
