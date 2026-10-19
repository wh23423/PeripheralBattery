# 采集器与设备兼容性审查（collector / task-2）

- 审查员：collector-reviewer（独立审查，未修改任何源码/配置/构建产物）
- 仓库：D:\desktop\电量显示插件，HEAD `a24f2fc`；工作树中只有各审查员的 `review/` 新增
- 环境：Windows 11 build 26300（README 记录）、16 逻辑核；Python 3.12.9（`.venv`）；Windows PowerShell 5.1.26100.9711，LanguageMode=FullLanguage
- 我创建/写入的文件：本报告 `review/collector-findings.md`；测试脚本只写在 `%TEMP%\pb-review\`（不在仓库内）
- 基线：`python -m pytest tests\collector -q` → **94 passed in 7.97s**（其余运行证据见 §1）
- 说明：为验证「数据目录不可写」路径，曾以 `PB_DATA_DIR=C:\Windows\System32\nope-pb-review` 跑过一次（该环境意外可写），测试后已 `rm -rf` 并确认目录不存在；未改动仓库内任何文件。

---

## 0. 结论摘要

- **P0：未发现**（未复现崩溃 / 数据错乱 / 完全不可用级缺陷；最接近的一条是 `make_device` 对 NaN/inf 抛异常，见 P2-2）。
- **P1：2 条**
  - **P1-1 休眠/唤醒后所有设备被误判 gone**（Modern Standby 每台 Win11 笔记本都会遇到；实测用单调时钟跳变复现）。
  - **P1-2 蓝牙 watcher 的 30 s 轮询补丁把「连接后 +3/+8/+15 s 补采」退化成 +30 s**（README/代码注释声称不受影响，实测不成立）。
- **P2：18 条**（数据边界、aliases 持久化、冷启动首快照 8 s、WGI PowerShell 成本、stdout 背压、同型号 key 合并等）。
- 旧审计声明的 stdio 五条硬化、完整快照门控、last_seen gone、key 迁移 replaces、Job Object 清树、打包运行、P8 timers 均**复验成立**（§2）。

---

## 1. 实际运行过的命令与结果

所有命令在仓库根目录执行；`PB_DATA_DIR` 均指向新建临时目录，未触碰真实 `%LOCALAPPDATA%\PeripheralBattery`。

| # | 命令 | 结果 |
|---|---|---|
| 1 | `.venv\Scripts\python.exe -m pytest tests\collector -q` | **94 passed in 7.97s**，无 skip/xfail |
| 2 | `python -m collector --probe --fake --scenario demo` | 退出 0；9 device / 2 tick / 2 gone / 1 migration；t=5.44 s 发 `device ... REPLACES logitech:046d:c547:u1`；gone `xinput` t=26.72 s、gone bt t=43.03 s；总耗时 7.79 s |
| 3 | `--probe --fake --scenario static` / `--once --fake --scenario static` | 退出 0；`--once` 输出 hello(5 devices)+tick；`tools/collector_ndjson_check.py` → `OK: NDJSON matches protocol.md` |
| 4 | `--probe --fake --scenario migration` | 退出 0，但 **provider polls: logitech=1、device events=1、key migrations=0**，从未出现 `replaces`（见 P2-6） |
| 5 | `--probe --fake --scenario slow` | 退出 0，1.54 s；`provider polls: 全部=0`（阻塞 5 s 的 daemon 线程被放弃，P10 符合预期） |
| 6 | 真实 `--probe --duration 25` | 退出 0；t=1.28 s 出 `bluetooth:0000:0000:c817f5498e4e "FlyDigi BS2PRO" level=94%`；29 个 provider 全部 poll 过；diag：`[MCHOSE] vid=3837 pid=3026 'Ace 68 Turbo 16K': no vendor collection`、`[XInput] no controllers connected` |
| 7 | 真实 `--once` | 退出 0，**8.23 s**；hello 内含 1 台蓝牙设备；ndjson checker OK（见 P2-7） |
| 8 | `dist\collector\collector.exe --probe/--once --fake`（PyInstaller onedir） | 退出 0，0.45 s；ndjson checker OK，中文设备名正常 |
| 9 | stdio 健壮性（独立脚本）：1 MiB+5 KB 超长行 + 正常命令 | 退出 0，读端重新同步成功（stdout 7 行） |
| 10 | stdio 健壮性：非法 UTF-8 行、非 JSON 行、未知 `{"t":"bogus"}` | 全部忽略并继续；退出 0 |
| 11 | stdin EOF（P10） | **0.01 s 内退出 0**，无滞留 |
| 12 | stdout 被关闭（Rust 端断管） | 采集器不崩溃、继续运行；再发 shutdown 仍正常退出 0 |
| 13 | `sys.stdout=None; sys.stderr=None` 后跑 `--probe --fake`（pythonw 模拟） | 退出 0，1779 字节输出到 fd1，无异常 |
| 14 | `--stdio --fake` 命令实测：get/refresh/refresh(provider)/set hidden/set name/bogus/非 JSON | 隐藏后 tick 只剩 3 台（4→3），改名后 tick 含新名字，未知命令只记日志 |
| 15 | `--stdio` 真实 70 s：psutil 采样 + stat | timers_per_s=**0.991**；ticks=3（30 s 间隔）；bt watcher=True snapshots=**2** watch_ms=30000；最慢 provider avg 2 ms；RSS 24.7→24.8 MB，handles 236→237，CPU 0.16 s/70 s（采集器本体） |
| 16 | 真实 `--stdio` 75 s，解析 collector.log 的 Bluetooth 快照组 | 只有两组：`16:59:32.618`（2 行）和 `17:00:03.810`（2 行）→ **间隔 31.19 s，+3/+8/+15 s 补采全部丢失**（P1-2） |
| 17 | Job Object 验证（ctypes `IsProcessInJob`） | collector 与 powershell/conhost 均 **在 Job 内=True**；**硬杀真实 collector 进程**后 3 s 内 PowerShell 子进程消失；硬杀 shim 场景同样无孤儿 |
| 18 | HID 枚举成本（真实机器） | `interface_paths()` 43 个 HID 接口，0.20 ms/次；`hid.enumerate(0)` 首次 7.44 ms、命中缓存 0.28 ms；仅 3 个 VID 存在（373e/37d7/3837） |
| 19 | 蓝牙/LE HID 路径解析回归 | 用三种真实格式（USB `vid_...&mi`、classic `{00001124-...}_vid&0002045e_pid&0b13`、LE `{00001812-...}_dev_vid&02045e_pid&0b13`）验证 `_path_vids/bluetooth_ids/bluetooth_only_vids` 全部正确 |
| 20 | PowerShell 成本（子进程自报 CPU） | 空脚本 0.10 s wall / 0.12 s CPU；**蓝牙全量快照 1.29–1.42 s wall / 0.47–0.48 s CPU**；**WGI 查询 3.79 s wall / 0.22 s CPU**（无手柄时 3 s 等待循环） |
| 21 | PowerShell 不可用（monkeypatch 抛 FileNotFoundError） | BT `poll()` → [] + diag，不崩；watcher `failed=True`；WGI→None；均为静默降级（无用户可见提示） |
| 22 | 单元级探针（level / BT 分组 / aliases / settings） | NaN→**ValueError**、inf→**OverflowError**；255 经 provider 原样输出、经 `make_device` 变 **100**；损坏 aliases.json 静默清空（0 告警）；删 aliases 后新设备复用同一 `u1`；`set(poll={"mouse":100000})` 被接受 |
| 23 | 休眠/唤醒模拟（可注入时钟的 StateStore） | 正常 2 分钟后 gone=0；单调时钟 +3600 s 后第一次 tick → **2 台全部 gone**；重新轮询后 2 台重新 `device`（累计 4 个 device 事件）→ P1-1 |
| 24 | 数据目录不可用 | `PB_DATA_DIR` 指向一个普通文件 → `FileExistsError` 裸 traceback，退出码 1（P2-14） |
| 25 | `Get-PnpDevice` 实例 ID 取样（验证蓝牙分组依据） | 耳机 AC800AC6B3D9 的 service 节点实例 ID 里确实带 MAC 与 `0000110b`（A2DP），`mac_of()/_HEX12` 回退正确；FlyDigi 只有 `BTHLE\DEV_C817F5498E4E` 一个节点 |
| 26 | WinRT `ClassOfDevice` 直查 FlyDigi | `codRaw=0`、`status=Disconnected`、name=`Bluetooth c8:...` → `kind_from_class(0)` 返回 ""，最终 kind=bluetooth（P2-17） |
| 27 | 未运行 | `tools\verify_runtime.py`（需要已构建的 app + WebView2 自动化，且和其它审查员并行时启动 app 会互相干扰，属平台/外壳范围）；`scripts\stress-tray-startup.ps1`（外壳范围，见 task-1） |

---

## 2. 旧修复复验（只列结论）

| 旧修复/声明 | 复验结果 |
|---|---|
| stdio 五条硬化（stdout=None、UTF-8、flush、stderr 排空、1 MiB 行上限/半行/EOF） | **成立**（#9–#13） |
| 完整快照门控 + last_seen gone（只由 tick 判 gone，阈值 max(2*TTL,10 s)） | **成立**（demo：设备 t=5.4 消失、t=26.72 才 gone） |
| 稳定 key + 迁移 `replaces` + names/hidden 迁移 + aliases 持久化 | **成立**（demo migration 路径在真实轮次中可见；`replaces` 与 history 正确；但见 P2-3/P2-5） |
| 子进程 registry + Job Object + 硬杀清树 | **成立**（#17：硬杀采集器 3 s 内 PowerShell 消失，无孤儿） |
| 蓝牙 watcher 常驻 + 失败回退 60 s one-shot + 卡死重启 | **成立**（代码路径 + #20/#21） |
| PyInstaller onedir 独立运行 / hidden imports / hidapi DLL | **成立**（#8；`--probe/--once --fake` 与 ndjson checker 均通过） |
| P8 定时器唤醒 ≤1/s | **成立**（0.991，真实 70 s） |
| refresh 在轮中不丢、refresh 立即回 tick | **成立**（count 实测；100 次 refresh 洪泛 5 s 内处理完，无卡死） |
| 蓝牙 watcher 连接轮询降到 30 s、连接后 +3/+8/+15 s 补采不受影响 | **不成立**：补采被 30 s sleep 吞掉（P1-2） |
| `--fake --scenario migration` 用于演示 key 迁移 | **不成立**（P2-6） |
| 24h 内存增长（P7）/ 24h key 抖动（P9）/ 真机 HID | 仍**未验证**（本次只跑了 70 s/60 s 短时采样；无法代表 24 h） |

---

## 3. 发现清单

### P0

无。说明：唯一能走到「采集器整体停止出数」的路径需要外部输入（NaN 电量，见 P2-2）或 Rust 读端停顿（P2-9），当前 provider 不会产生 NaN，因此不判 P0。

---

### P1-1 休眠/唤醒后所有设备被误判 gone，接着整批重新出现（Modern Standby 高频场景）

- 位置：`collector/state.py:449-474`（`_expire` 直接用 `self._clock()` 单调时钟做 last_seen 比较）、`collector/state.py:423-436`（`on_tick`）；采集器没有任何电源/恢复处理（`collector/collector.py:612-639` 的 `handle_command` 只认 shutdown/refresh/get/set；`collector/protocol.py:99-104` 的 `new_session()` 没有任何调用方）。
- 触发场景：Win11 笔记本合盖/Modern Standby、或任何 S3 睡眠后唤醒。CPython 3.12 在 Windows 上 `time.monotonic()` 的实现是 `GetTickCount64()`（`.venv\Scripts\python.exe -c "import time; print(time.get_clock_info('monotonic'))"` → `implementation='GetTickCount64()'`），微软文档明确指出需要 `QueryUnbiasedInterruptTime` 才能拿到「working state」时间（即 tick count 含睡眠时间，参见 https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64 ）。睡眠 1 小时 → 单调时钟前进 1 小时 → 第一次 1 Hz tick 时所有 key 都满足 `now-last_seen > max(2*TTL,10)`。
- 证据（模拟可注入时钟，等价于恢复后第一次 tick）：
  ```
  after 2 min normal: gone= []
  after simulated 1h sleep + first tick: gone= ['logitech:046d:c547:u1', 'razer:1532:0554:1a2b3c4d'] visible= 0
  after re-poll: device events total = 4 gone total = 2
  ```
  即 2 台设备被 gone 又重加：UI 面板/托盘闪一下「离线」，低电/充满告警状态被清空，Rust tray_mirror 在隐藏态也会先显示离线；若用户在唤醒瞬间打开面板会看到空列表。
- 影响：每次唤醒都复现（不影响最终数据，但 P1「明显体验问题」；同时污染 P9 统计口径——gone+device 各 2 次）。
- 修复方向：Rust 接 `WM_POWERBROADCAST`（PBT_APMRESUMEAUTOMATIC）后给采集器发 `{"t":"new_session"}`（协议已留 `new_session`），采集器据此清空 last_seen/gone 判断并重置 session；更简单的兜底：`on_tick` 里检测单调时钟跳变（本次 tick 与上次 tick 差 > 5 s 或 > 3×tick_period）时跳过这一轮 `_expire` 并把所有 last_seen 拨到 now。另加一个可注入时钟的单测（本次模拟脚本即测试雏形）。

---

### P1-2 蓝牙 watcher 的 30 s 补丁把「连接后 +3/+8/+15 s 补采」退化成 +30 s，冷启动补采同样被吞

- 位置：`collector/btconfig.py:39-40`（只匹配 `Start-Sleep -Milliseconds 2000`）、`collector/btconfig.py:102-106`（只替换 sleep 数值）、`collector/btconfig.py:51-60,110-112`（F3 冷启动补采 patch 往 `$queue` 里塞 +3/+8/+15/+30 的 due 时间）、`collector/providers/bluetooth.py:107-131`（WATCH_SCRIPT：`Start-Sleep -Milliseconds <watch_ms>` 在循环开头，`$queue` 只在睡醒后用 `Get-Date` 判定一次 due）、`collector/collector.py:286-300`（启动时 configure）。
- 机制：脚本改为每 30 s 醒一次；醒来时 `$now` 已晚于 +3/+8/+15 三个 due 点，于是 `while ($queue[0] -le $now)` 一次性全部弹出，只做 **一次** 快照。也就是说：
  - 连接/断开事件被检测到的那一刻有立即快照（正常）；
  - 之后本应在 +3/+8/+15 s 各补一枪，实际只在 +30 s 补一枪；
  - F3 冷启动补采（+3/+8/+15/+30）同样塌缩为 +30 s 一枪。
- 证据（真实 `--stdio` 75 s，解析 collector.log 中每次快照的 Bluetooth diag 组）：
  ```
  16:59:32,618 [Bluetooth] WH-1000XM4 ... ; [Bluetooth] FlyDigi BS2PRO ...
  17:00:03,810 [Bluetooth] WH-1000XM4 ... ; [Bluetooth] FlyDigi BS2PRO ...
  ```
  两次快照间隔 **31.19 s**，中间没有任何 +3/+8/+15 档；70 s 的真实 stat 也显示 `bluetooth: snapshots=2, watch_ms=30000`。
- 与文档冲突：`collector/btconfig.py:15-18` 与 README §6 都写「battery snapshots on connect (+3/+8/+15 s) ... 不受影响」「初始快照与连接后的 +3/+8/+15s 快照不受影响」。实测不成立。
- 影响：蓝牙设备连上后，若 Windows 晚几秒才报告电量（最常见的时序），面板最多要等 **~30 s** 才出现电量（文档承诺 ~3–15 s）；冷启动 F3 修复在「首快照漏设备」情形下也退化为 30 s；配合 P2-12 的 120 s gone 阈值，丢一次快照就会 gone 抖动。
- 修复方向：不要只替换 sleep 数值，而是让循环按「最早 due 时间」睡：`$wait = min(nextConnCheck, oldestQueueDue, nextFull) - now`，或保留 2 s 的细粒度 sleep 但用时间戳门控 `Get-BtConn`（每 watch_ms 才真正查询一次）。加一个不依赖真机的验证：mock PowerShell（`PS_SCRIPT/WATCH_SCRIPT` 可替换）断言快照时间戳包含 +3/+8/+15。

---

### P2-1 电量边界未校验：255/-1 会被当成 100%/0% 显示（协议 side 直接 clamp）

- 位置：`collector/providers/bluetooth.py:240-244`（`int(p.get("data"))` 原样接受，无 0..100 校验；`group_devices` 也一样）、`collector/protocol.py:28-34`（`_normalize_level` 用 `max(0,min(100,...))` 强夹）。
- 证据：`group_devices` 喂 255 → DeviceStatus.level=255（diag 打印 `255%`），`make_device(level=255)["level"] == 100`；-1 → 0；101→100。-1 的负电平还会让 provider 在 `-1%` 字符串里进日志。
- 场景：部分蓝牙驱动/固件用 255 表示「未知电量」，用户会看到满电 100%。
- 修复方向：`group_devices` 只在 0..100 内接受（否则按 None/缓存处理）；`_normalize_level` 对超出 0..100 的整数返回 None 而不是夹取，并单独处理 NaN/inf（见 P2-2）。

### P2-2 `_normalize_level` 对 NaN/inf 直接抛异常，能中断一轮 merge

- 位置：`collector/protocol.py:28-34`（`int(round(level))`）；调用点 `collector/state.py:360-374`（`_apply` 在持有 StateStore 锁时构造 Device）。
- 证据：`make_device("k","n",float("nan"))` → `ValueError: cannot convert float NaN to integer`；inf → `OverflowError`。
- 影响：异常从 `state.on_provider_result` 抛出 → `collector/collector.py:505-512` 只 log.exception 且 `merged=False`，`_pending` 已被清空、`_round_active` 保持 True；该设备存在期间每一轮 complete-snapshot merge 都会失败，直到设备消失（期间不再有 device 事件；90 s 后 force_merge 也只能空转）。
- 修复方向：`_normalize_level` 对 NaN/inf（`math.isfinite`）返回 None；`StateStore._apply` 对单个设备的 key/level 异常按设备隔离（try/except continue），不要让一个坏设备毁掉整轮。

### P2-3 aliases.json 损坏/丢失静默清零：用户 hidden/rename 会错配到别的设备，且无任何告警

- 位置：`collector/keys.py:354-362`（`load` 捕获 OSError/ValueError 后直接 return，无日志、无备份、无 .bad 改名）、`collector/keys.py:744-748`（序号分配）、`collector/state.py:291-298/339-347`（hidden 判断用 stable key）、`collector/settings.py:289-350`（names/hidden 按 stable key 存）。
- 证据（单元探针）：
  ```
  payload '{not json'          -> migration_count=0 aliases=0
  payload ''                   -> migration_count=0 aliases=0
  first device  -> xinput:0000:0000:u1
  after loss    -> xinput:0000:0000:u1 (a *different* controller gets the same stable key)
  ```
  即 aliases 丢失后序号从 u1 重新分配，config.json 里 `hidden:["...:u1"]` / `names` 会被套到新出现的另一台设备上；同时 P9 的迁移历史与 migration_count 归零。
- 修复方向：load 失败时 `log.warning` + 把坏文件改名为 `aliases.json.bad-<ts>`（或写 `.bak` 双份）；序号 scope 里加入更稳定的信息（如首次出现的 name/fingerprint 已经做了）并在迁移时保留；文档说明「删 aliases = 重置用户偏好绑定」。

### P2-4 `KeyStore.save()` 先清 dirty 再写盘：写失败会静默丢持久化

- 位置：`collector/keys.py:394-408`（`self._dirty = False` 在 `os.replace` 之前）、`collector/keys.py:409-421`（OSError → return False）。
- 场景：%LOCALAPPDATA% 被杀软/同步盘/磁盘满临时锁住 → `os.fdopen/os.replace` 抛 OSError；此时 dirty 已清，下一次 `_maybe_save` 不会重试，直到进程退出时的 `save(force=True)`（若被硬杀则彻底丢失本次新增 alias/序号）。
- 修复方向：只有写成功后才清 dirty；失败时把 dirty 置回并递增重试计数。

### P2-5 同型号/同品牌设备共用 key 家族，最多只能显示一个图标

- 位置：`collector/providers/mchose.py:233-241`（`device_key(vid,pid)`：0x5253 家族一律返回字符串 `"mchose"`，与 pid 无关；同一轮里 `found` 以该 key 聚合，`poll` 中 `found.setdefault(key,...)` 会合并多个 pid 的读数）、`collector/providers/wlmouse.py:183`（`key = "wlmouse"`）、`collector/providers/lamzu.py:128`（`"lamzu"`）、`collector/providers/gwolves.py:303`（`"gwolves"`；部分分支 `gwolves:{model}`）。
- 场景：同一台机器插两只 MCHOSE 0x5253 鼠标 / 两只 Lamzu，UI 只有一行；读数被按 charging 排序后取第一份，另一只永久不可见。
- 另外同型号 XInput 手柄：legacy key 是 `xinput:{slot}`，`KeyStore.resolve` 用 alias 把 legacy→stable 固定下来（`collector/keys.py:472-493`），重启后若 Windows 把两只同型号手柄分到不同 slot，stable key 会跟随 slot 而不是物理设备 → hidden/rename 换到另一只上（两只同型号无法凭 API 区分，属协议层限制）。
- 修复方向：至少给 mchose/wlmouse/lamzu/gwolves 的 key 带上 pid（MCHOSE 已对非 0x5253 这样做）或 HID path 中的物理端口/序列号后缀；同型号 XInput 在文档中明确「无法区分」。

### P2-6 `--probe/--once --fake --scenario migration` 无法演示 key 迁移

- 位置：`collector/fake.py:235-241`（migration 时间线 0 s→2 s，duration=3 虚拟秒）、`collector/collector.py:328-340` + `settings.py:258-261`（logitech 属于 mouse 类，轮询间隔 5 s 虚拟秒）、`collector/__main__.py:219-221`（`--probe` 到 `world.duration` 即停）。
- 证据：#4：`--probe --fake --scenario migration` 只有 1 次 logitech poll、key migrations=0、只输出 `...:u1`；`--once --fake --scenario demo` 也永远只看到 t≈0 的初始快照。README 说 migration 场景用于演示 replaces，实际演示不出来（demo 场景靠 45 s 长时长才碰巧演示到）。
- 修复方向：把 migration 时间线改为 `(0 s 初始) … (6 s 或 8 s 切 unit id)` 并提高 duration；或让 scenario 提供「每次 poll 都 +1 状态」的 timeline；补充一个 pytest 用例直接断言 `--probe --fake --scenario migration` 正常退出且产生 replaces。

### P2-7 冷启动/--once 的首个完整快照要 ~8 s（30 provider / 4 worker / 1 Hz 分批）

- 位置：`collector/collector.py:364-397`（每次唤醒最多取 `free_slots=4` 个 due provider 组成一轮，其余等下一秒）、`collector/collector.py:242,301-303`（pool=4、scheduler 1 Hz）、`collector/__main__.py:234-250`（`--once` 等 `coverage_complete`）。
- 证据：#7 真实 `--once` **8.23 s**；代码推算 30/4≈8 轮 ×1 s。
- 影响：`--once`/诊断变慢；冷启动时设备按 provider 顺序分 8 批出现，冷门 provider（steam/nzxt 等）里的设备最晚 ~8 s 才显示；README 的 F3「首出数 1.3–1.45 s」只覆盖了蓝牙（event provider，不参与门控），纯 HID 用户没有等价验收。
- 修复方向：第一轮忽略 free_slots 全部提交（pool 临时扩到 provider 数，或用 2-3 个并发 wave），或让 `--once` 在拿到 round 1 后即返回；补充「仅 HID、无蓝牙」冷启动耗时测试。

### P2-8 WGI 查询成本与调用条件：有手柄在线时每 8 s 起一个 PowerShell（0.22–0.48 s CPU）

- 位置：`collector/providers/xinput.py:214-227`（`_wgi` 在 slots 变化或 `now-_wgi_at>=WGI_REFRESH(8)` 时同步执行）、`collector/providers/wgi.py:111-126`（每次 `subprocess.run` 起 PowerShell）、`xinput.py:229-263`（无 XInput slot 时直接 return，WGI 根本不会被调用）。
- 证据（#20）：WGI 脚本 3.79 s wall / 0.22 s CPU（无手柄时 3 s 等待循环），蓝牙全量快照 1.29–1.42 s wall / 0.47–0.48 s CPU，空 PowerShell 0.12 s CPU；16 逻辑核。手柄在线时 xinput 每 10 s（pending 时 3 s）poll 一次，其中每 8 s 一次 WGI PowerShell；P2/P3 的验收数据是在**没有手柄**的机器上测的，未覆盖此路径。
- 另：如果某手柄能被 WGI 看到但不在 `RawGameControllers` 里、或 xinput1_4.dll 不可用（`xinput.py:232-235`），WGI 主源路径不会兜底。
- 修复方向：提高 WGI_REFRESH（如 30–60 s）并缓存；把 WGI 改成常驻 helper（README §6 已列长期方案）；测量「手柄在线」场景的 P3。

### P2-9 stdout 背压无超时/守护：Rust 读端停顿时采集器会在持锁状态下阻塞

- 位置：`collector/protocol.py:91-97`（`_emit` 持 `Protocol._lock` 调用 `writer.send`）、`collector/transport.py:250-273`（`LineWriter.send` 直接 `write+flush`，只捕获 OSError）、`collector/state.py:360-374`（在 StateStore 锁内调用 `sink.device`）。
- 场景：Rust 的 stdout 读线程若因 WebView 冻结/IPC 背压暂停读取，管道 64 KiB 写满后 `write` 阻塞；此时采集器的 scheduler/worker 线程持有协议锁+状态锁被冻结，主线程的 `shutdown/set/get` 也无法获得状态锁 → Rust 只能等 2 s 后 TerminateJobObject（已实测硬杀会清树，但会丢最后一次 keys/history 落盘）。隐藏态每小时输出约 1.5–2 KB stat，长期隐藏（数小时）后风险显著上升。
- 说明：这是依赖 Rust 行为的风险项（归 task-1 复核），采集器侧可改进。
- 修复方向：独立写线程 + 有界队列（满时丢弃最旧并计数）；写超时/非阻塞检测；或对 shutdown 命令走无锁快速路径。

### P2-10 DaemonPool 的 worker 永不回收：provider 永久卡死会逐步吃掉全部 4 个槽

- 位置：`collector/collector.py:57-90`（`_busy` 只在 `finally` 递减）、`collector/collector.py:422-445`（reap 只把 task 标 not in-flight，worker 线程仍在阻塞的 C 调用里）。
- 场景：某 HID 驱动 bug 使 `hid.read`/PowerShell 子进程永久阻塞；每挂一个 provider 少一个 worker，4 个后 `submit` 永远 False → 采集器只发 stat、不再轮询（设备全靠 TTL gone）。`ProviderTask.timeout` 只影响 in-flight 记账，不释放线程。
- 修复方向：把有超时的子进程路径收紧（所有 `subprocess.run` 已有 timeout，但 HID read 取决于驱动）；或每 provider 一个独立线程 + subprocess/WGI 用可 kill 的子进程隔离；至少给 `free_slots` 长期为 0 打 WARN。

### P2-11 协议声明了 `offline` 告警但采集器从未发出

- 位置：`collector/protocol.py:23`（`ALERT_KINDS=("low","full","offline")`）vs `collector/state.py:392-420`（`_check_alerts` 只发 low/full）。
- 影响：设备离线/断连没有任何 alert（README §6 已知限制），但协议/CAPABILITIES 面没有说明；UI 侧若按协议做 offline 提示永远收不到。
- 修复方向：要么在 `_expire` 判 gone 前发 offline alert（可配置），要么从协议文档/常量里标记为未实现。

### P2-12 蓝牙 TTL 余量只有 1 个快照：丢一次快照即 gone 抖动

- 位置：`collector/collector.py:515-527`（watcher 健康时 TTŁ=60 s）、`collector/state.py:461`（阈值 max(2*TTL,10)=120 s）vs 实际 watcher 稳态快照间隔 60 s（#16：1.3 s 后下一发 ~60 s 后）。
- 场景：PowerShell 抖动/系统繁忙导致漏掉一次快照 → 间隔 120 s → tick 正好判 gone，随后下一条快照把设备作为新 device 重新加回（托盘/列表闪动、告警状态清空）。STALL_TIMEOUT=180 s / STALL_LIMIT=2 期间也会持续 no update。
- 修复方向：TTL 提到 90–120 s（阈值 180–240 s），或在 watcher 停滞时抑制 gone（已有 `set_in_flight` 只覆盖 provider 轮询，不覆盖 event provider）。

### P2-13 设备名等硬件字符串原样进日志（换行可伪造日志行）

- 位置：`collector/providers/bluetooth.py:289-291`（`diag.append(f"[Bluetooth] {name} ...")`）、`collector/state.py:399-408`（alert text 含 `name`）、`collector/providers/__init__.py` 相关 diag 打印。
- 场景：蓝牙 FriendlyName 可由设备/厂商任意填写（含 `\n`），经 `log.info` 落入 collector.log 可伪造行；对终端 `--probe` 也是原样 print（终端转义序列可注入）。协议 JSON 侧是安全的（`json.dumps` 转义），XSS 取决于 UI。
- 修复方向：日志/纯文本输出前把控制字符替换为 `\xNN` / `repr()`。

### P2-14 数据目录不可创建时裸 traceback 退出，没有可诊断信息

- 位置：`collector/__main__.py:131-139`（`prepare_settings` 在 `install_stderr_log` 之前）、`collector/settings.py:194-197`（`os.makedirs`）。
- 证据：#24：`PB_DATA_DIR` 指向一个文件 → `FileExistsError [WinError 183]` 栈，退出码 1；Rust 侧只表现为 5 s 无 hello 的 error 态。
- 场景：企业环境重定向/权限受限的 %LOCALAPPDATA%、磁盘满、同步盘锁定。
- 修复方向：捕获 OSError → 写一条明确中文原因到 stderr（Rust 会落 collector.log）并退出 2；或 fallback 到 %TEMP%\PeripheralBattery 并在 hello/diag 中标注。

### P2-15 `--probe`(真实)/`--once` 不安装 stderr 日志，运行期错误只进 provider.diagnostics

- 位置：`collector/__main__.py:212-231`（run_probe：只 `ensure_utf8_stdio`，无 `install_stderr_log`）、`__main__.py:234-250`（run_once 同理）vs `__main__.py:253-266`（run_stdio 装了）。
- 影响：真实 `--probe`/排障时 `log.info/log.warning`（蓝牙 watcher 重启、PowerShell 退出码、watchdog、state 异常）全部丢弃；只有少数 provider 自己收集的 diag 会打印。本次真实 `--probe` 实测 stderr 为空、`logs/collector.log` 为 0 行。
- 修复方向：probe/once/diag 也调用 `install_stderr_log`（或至少 `logging.basicConfig(stream=sys.stderr)`），并在 summary 里提示日志路径。

### P2-16 config.json 的 `poll` 没有上限值校验

- 位置：`collector/settings.py:109-111`（LIMITS 无 `poll`）、`collector/settings.py:138-141`（只要求 `v >= 1`）。
- 证据：#22：`set("poll", {"mouse": 100000}) → True`，随后 `provider_interval("logitech") == 100000.0`。这个值同时是 TTL（`settings.py:263-280`），gone 阈值 (2*TTL) 随之变 55 h → 设备拔掉后永远不会 gone。
- 修复方向：给 poll 每个 kind 加 (1, 3600) 上限并 clamp；get 时再做一次 clamp。

### P2-17 BLE 手柄 kind 识别失败：FlyDigi BS2PRO 显示为通用蓝牙图标

- 位置：`collector/providers/bluetooth.py:157-178`（`kind_from_class` 依赖 ClassOfDevice/Appearance）、`bluetooth.py:268-293`（cls 缺失时 kind=""）。
- 证据：#26：WinRT 直查该设备 `codRaw=0`；真实 `--probe` 输出 `kind=bluetooth`（而非 gamepad）。BTHLE 设备只有 `BTHLE\DEV_<mac>` 一个节点、没有 HID service 节点可反查。
- 影响：手柄显示通用蓝牙符号（不是功能错误，但 UI 信息缺失）；`dedupe_controllers` 对 `via=bluetooth` 的 xinput 去重仍有效。
- 修复方向：kind 缺失时用 HID usage page/usage（蓝牙 HID 手柄 page 0x01 usage 0x05）或 appearance 的 fallback；或允许用户在 UI 覆盖 kind。

### P2-18 `--speed` 与固定 poll 间隔交互，demo 时间线里的事件被跳过

- 位置：`collector/__main__.py:148-163`（fake 的 `tick_period` 随 speed 变，但 provider interval 来自 settings，按**虚拟秒**计）、`collector/fake.py:199-221`（demo 在 1.5/2.0/3.0/4.0 s 的变更）。
- 证据：#2：keychron 的 null→55% 变更在 t≈15.5 s 才到（应 t=2 s），logitech 的 12 s charging 变更也到 t=15.5 s；README 已警告 `--speed` 影响 TTL，但这里影响的是「场景演示是否覆盖」。
- 修复方向：fake 场景用 provider 间隔 ≤ 1 s 的专用 ProviderTask interval，或把时间线锚到「第 N 次 poll」而不是绝对秒。

---

## 4. 未覆盖 / 受限（诚实说明）

- 无真机 HID 外设（除已配对的 FlyDigi BS2PRO 与一只不被支持的 MCHOSE Ace 68 键盘）：30 个 provider 的协议解析、独占打开失败（G HUB/Razer Synapse 占用）、USB 2.4G 接收器、Steam/XInput 手柄实测均只能代码审查。
- 无法自动化睡眠/唤醒（未真正 suspend 机器），P1-1 用注入时钟 + `GetTickCount64` 文档语义证明。
- 24 h 内存增长（P7）/24 h key 抖动（P9）未跑；只做了 60–70 s 采样（RSS 24.7→24.8 MB、handles 236→237、threads 13–16 稳定）。
- 企业策略（PowerShell 被 AppLocker/WDAC/ConstrainedLanguage/GPO 禁用）未实测，只验证了「PowerShell 不存在」的降级路径是静默的。
- PyInstaller 杀软误报、只读安装目录、多用户/RDP 场景属 task-4 范围，未重复。
- `tools\verify_runtime.py` / `scripts\stress-tray-startup.ps1` 未运行（依赖已构建 app + GUI 自动化，与并行审查员/外壳范围重叠）。

---

## 5. TOP 待修复清单（按优先级）

1. **P1-1 休眠/唤醒 gone 风暴**：`state.on_tick` 检测单调时钟跳变并跳过 `_expire`（或 Rust 恢复事件 → `new_session`）；加注入时钟单测。否则每台 Win11 笔记本每次唤醒都闪一遍。
2. **P1-2 蓝牙补采时间表塌缩**：修复 `btconfig.patch_watch_script` 只改 sleep 的问题，按最早 due 时间睡；恢复 +3/+8/+15 s（含 F3 冷启动补采）；加时间戳断言测试。
3. **P2-1 + P2-2 电量校验**：0..100 之外（255/-1）判未知而不是夹取；NaN/inf 返回 None；`_apply` 单设备容错。否则会出现「满电 100%」的假数据。
4. **P2-3 + P2-4 aliases 持久化健壮性**：损坏告警+备份、写成功后才清 dirty、丢失后序号/偏好错配的缓解。
5. **P2-7 首快照 8 s**：首轮不受 pool 槽位限制 / `--once` 拿首轮即返回；补「无蓝牙、纯 HID」冷启动验收。
6. **P2-9 stdout 背压**：独立写线程+有界队列+超时；与 task-1 的 Rust 读端行为一起复核（隐藏 24 h 的真实风险）。
7. **P2-8 WGI 成本**：WGI_REFRESH 提到 30–60 s 或常驻 helper；补「手柄在线」的 P2/P3 实测。
8. **P2-5 同品牌多设备 key 合并**：mchose/wlmouse/lamzu/gwolves key 带 pid/物理后缀。
9. **P2-12 蓝牙 TTL 余量**：TTL 60→90/120 s，避免一次快照丢失就 gone。
10. **P2-6 / P2-14 / P2-15 / P2-16 / P2-17 / P2-18**：演示场景修正、数据目录/日志错误可诊断、poll 上限、BLE kind、demo 时间线。
