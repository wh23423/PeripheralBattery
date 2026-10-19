# 外设电量显示器（Peripheral Battery）

**[下载安装包](https://github.com/wh23423/PeripheralBattery/releases/latest)** · MIT License

Windows 托盘常驻的外设电量面板：鼠标 / 键盘 / 耳机 / 手柄，覆盖 2.4G 接收器、USB、蓝牙三条链路。
外观为液态玻璃浮动面板（780×440 DIP 窗口 / 720×380 面板 / 28px 圆角）。

> 施工依据：[BUILD_PLAN.md](BUILD_PLAN.md)（构建方案 v1.1，含 18 条对抗性审查处置）、[TECH_PLAN.md](TECH_PLAN.md)（架构与视觉规范）、[protocol.md](protocol.md)（三方通信协议，施工后定稿）。

## 预览

![真实窗口 + acrylic](docs/images/acrylic-panel.webp)

![逐条数据到达后的主卡与列表](docs/images/hero-panel.webp)

![UI 渲染（浏览器评审模式）](docs/images/ui-mock.webp)

> 安装包见 GitHub Releases；`design/` 下的完整验收证据与录屏不随源码仓库分发。

---

## 1. 架构

```
collector.exe (Python, stdio NDJSON)  ⇄  app.exe (Tauri 2 / Rust)  ⇄  WebView2 (ui/)
   30 个厂商 provider、稳定 key、            窗口/托盘/Job Object/隐藏四步/      静态页：keyed diff、
   完整快照门控、last_seen gone              事件转发/命令转发                   液态玻璃、动效
```

- 采集与 UI 分进程：HID 独占、崩溃隔离、技术栈自由（TECH_PLAN D1）。
- 协议见 [protocol.md](protocol.md)：`hello/device/gone/tick/alert/stat` + `hello.session/seq`；
  设备 key 为 `provider:vid:pid:(serial|mac|unit_id|uN)`，`aliases.json` 持久化，迁移发 `replaces`。
- 隐藏时四件事都做：`put_IsVisible(FALSE)` → 暂停 WAAPI/rAF → 不写 DOM → `window.hide()`；
  显示反向执行并先用最后快照渲染一帧。

## 2. 目录

| 路径 | 说明 |
|---|---|
| `collector/` | Python 采集器（`providers/` 原样复制自 HaloBattery；`state.py`/`keys.py`/`collector.py`/`transport.py`/`fake.py`） |
| `collector_entry.py` | PyInstaller 入口（包内相对导入，不能直接打包 `collector/__main__.py`） |
| `src-tauri/` | Tauri 2 外壳（窗口、托盘、sidecar、Job Object、单实例、IsVisible、命令/事件） |
| `ui/` | 静态前端（`bridge.js` IPC 桥、`app.js` keyed diff、`styles.css` 液态玻璃 + 性能改造） |
| `tests/collector/` | 采集器 pytest（58 项） |
| `tools/` | 截图/录屏/性能/验收脚本（`ui_smoke.js`、`verify_runtime.py`、`verify_fps.js`、`collector_demo.ps1`） |
| `scripts/` | `build-collector.ps1`、`build.ps1`、`dev.ps1`、`verify-shell.ps1` |
| `design/` | 设计稿、演示素材、`design/verify/` 验收证据 |
| `dist/collector/` | PyInstaller onedir 产物（打包时映射到安装目录 `collector/`） |
| `hb/` | 上游 HaloBattery 参考克隆，**不要改** |

## 3. 环境准备

| 依赖 | 本机路径 / 版本 |
|---|---|
| Python | `C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe`（3.12.9；PATH 里没有） |
| Rust | `%USERPROFILE%\.cargo\bin`（rustc/cargo 1.99.1，stable-x86_64-pc-windows-msvc） |
| Node / npm | 24.20.0 / 11.19.0 |
| WebView2 | 155.x |
| VS2022 | MSVC 14.44 + Windows SDK 10.0.26100 |

```powershell
# 一次性
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r collector\requirements.txt -r requirements-dev.txt
npm install
```

## 4. 开发 / 构建 / 测试

```powershell
# 开发（默认显示面板；-NoShow 只驻留托盘）
.\scripts\dev.ps1
.\scripts\dev.ps1 -Collector '"D:\...\.venv\Scripts\python.exe" -m collector --stdio --fake --scenario static'

# 只跑 Tauri（debug 可执行文件已构建时）
$env:PB_SHOW_ON_START="1"; .\src-tauri\target\debug\peripheral-battery.exe

# 构建 collector + Tauri + NSIS 安装包
.\scripts\build.ps1
.\scripts\build.ps1 -Debug -NoBundle      # 只编译 debug exe，不做安装包

# 测试
.\.venv\Scripts\python.exe -m pytest tests\collector -q
node tools\ui_smoke.js
node tools\ui_shots.js                    # 评审截图 -> design/verify/ui-*.png
powershell -File tools\collector_demo.ps1 # collector 端到端 demo（probe/stdio/--once）
```

采集器单独使用：

```powershell
.\.venv\Scripts\python.exe -m collector --probe                    # 真实设备，人读
.\.venv\Scripts\python.exe -m collector --probe --fake --scenario demo
.\.venv\Scripts\python.exe -m collector --stdio                    # 生产模式（app 会这样拉）
.\.venv\Scripts\python.exe -m collector --once                     # 一次快照 NDJSON
```

`--fake` 场景：`demo`（5 种 kind + null/approx/charging/replaces/gone/低电告警）、`static`、`migration`。
注意 `--speed` 会同时加速虚拟时钟，>1 时 TTL 会按虚拟秒到期导致设备抖动，演示 UI 请用 `--speed 1`。

## 5. 验收与实测（2026-10-19，本机 Windows 11 build 26300 / 2560×1440 / 125% / 250Hz）

| 指标 | 目标 | 实测（最终 release，2026-10-19 12:20 复测） | 结论 |
|---|---|---|---|
| P1 帧率 | 58–60fps，p99 ≤ 20ms | 显示器 250Hz：rAF **250fps**，p99 **4.2ms**，max 5.8ms，panel 720×380，`backdrop-filter: none`（`lead-hero-fps.png` / app.log `FPS_PROBE`） | 达标（以 250Hz 刷新率为准） |
| P2 隐藏进程树 CPU | < 0.5% | 5 分钟：app 0 + WebView2 0.021 + collector/PowerShell 0.297 = **0.318%**（`lead-hidden-final5.json`） | **达标**（蓝牙 watcher 轮询 2s→30s 后；旧基线 0.666%） |
| P3 采集器 CPU | < 0.8% | 同上 collector + PowerShell = **0.297%**；独立 60s 采样 PowerShell 0.078–0.130% | 达标 |
| P4 进程树内存 | < 320MB | 隐藏稳态 **247.7MB**（WebView2 suspend 后 ~48MB，PowerShell 125.5MB，collector 28.7MB，app 30.4MB）；启动前 ~20s 未 suspend 时峰值 449.3MB | 稳态达标；启动峰值超标（见 §6） |
| P5 冷启动到托盘可用 | < 1.5s | app start → `setup complete` **194–232ms** | 达标 |
| P6 托盘点击到首帧 | < 200ms | 托盘点击日志 → `window shown` **17ms**（`IsVisible(true) S_OK` 后 3ms；内容隐藏时已预渲染） | 近似达标（未取真实 first-paint） |
| P7 24h 内存增长 | < 15MB | 未跑 24h；隐藏 5 分钟 226.4→247.7MB（+21.3MB，主要是 PowerShell watcher），趋势不乐观 | 未完成，有风险 |
| P8 定时器唤醒 | ≤ 1 次/秒 | app.log `collector stat: timers_per_s=0.99`（F7 已可直接从日志复核） | 达标 |
| P9 key 抖动 | 24h < 2 次 | 修复 slot/unit/interface 迁移与 `replaces`；migration/重枚举场景无抖动；24h 未跑 | 部分达标 |
| P10 更新/退出孤儿 | 0 | 正常退出、卸载、强制 kill、shutdown 超时 `TerminateJobObject` 均无 app/collector/PowerShell 残留 | 达标 |

其它已验证：`cargo check` 0 warning、`cargo test` 10 passed、pytest **94 passed**、`ui_smoke` **15 passed**；
PyInstaller onedir collector 独立运行；NSIS 安装包（**8.92 MiB**，13:12 重建）安装 → 从安装目录 `resource_dir()\collector\collector.exe` 启动 → hello → 正常退出 → 卸载后目录/进程/快捷方式全清；
单实例互斥体（第二实例 ~10ms 退出并激活已有面板）；托盘真实左键点击、状态色图标；失焦 270ms 隐藏（pinned 抑制）；
隐藏时 `IsVisible(false)=S_OK` + `TrySuspend`；隐藏态托盘由 Rust 镜像持续更新（不受 WebView 冻结影响），恢复时自动 `get snapshot` 补数据；
最小化走隐藏四步且可恢复；协议版本不符/启动失败/5s 无 hello 的错误态均不崩。

### 修复后复测（2026-10-19 12:15–13:22，最终 NSIS 包 13:12）

| 项 | 结果 | 证据 |
|---|---|---|
| **P0 可见启动死锁** | **PASS**：`tray update` 改主线程非持锁调用后，tauri-dev 压测可见 **40/40** + 隐藏 10/10，Lead 独立 20/20 可见启动，0 hang、全部有 `app exit`；`tray-update begin/end=1320/1320` | `design/verify/stress-visible.json`、`design/verify/tray-stress-result.json` |
| F1 主卡 = 最低电量（逐条 device 到达） | **PASS**：真实 app + 逐条 mock 数据，主卡 Arctis 12%，列表 G502 68 / Keychron 55 / Xbox 84 / XM5 43 | `design/verify/lead-hero-fps.png` |
| F3 纯蓝牙冷启动首出数 | **PASS**：collector-dev 10/10 **1.33–1.45s**；Lead 独立 5/5 **1.53–1.71s**（修复前 2/4 次 65–128s） | app.log；首快照 +3/+8/+15/+30s 补采（roots-only 门控） |
| F4 ui_ready 兜底误判 | **PASS**：ui_ready → 失焦 1.06s 隐藏后，无 `ui_ready not received` WARN、无 fallback re-show，正常退出 | 本次复测日志 |
| F7 stat 可观测性 | **PASS**：app.log 每 60s 输出 `collector stat: timers_per_s=0.99 key_migrations=0 providers=30 slowest=mchose:2.0ms` | app.log |
| 最终 NSIS 包 | **PASS**：8.92 MiB；安装后 collector.exe + `_internal\python312.dll`；安装版从安装目录 packaged 启动 + hello ready；退出 Job active=0；卸载后目录/进程全清 | `%TEMP%\PB-install-test3` 测试记录 |

### 审计发现并已修复的运行问题

| # | 问题 | 修复 |
|---|---|---|
| 1 | 最小化后 `show()` 不清 MINIMIZED，`skipTaskbar` 又无任务栏入口 → 面板永久打不开 | minimize 改完整隐藏四步；show 前 `unminimize`；定位用固定 780×440 |
| 2 | 隐藏态托盘启动即「离线」且永不更新；`TrySuspend` 后前端 JS 冻结 | Rust 侧 `tray_mirror` 直接解析 sidecar 事件更新托盘；恢复时自动 `get snapshot` |
| 3 | 托盘「设置」隐藏态无效果 | 先 `show_panel` 再 emit `tray-action` |
| 4 | shutdown 超时只 TerminateProcess collector → PowerShell 孤儿 | `TerminateJobObject` 清整棵进程树 |
| 5 | `\\?\` 前缀导致 packaged collector 残留清理失效 | normalize 剥 verbatim/UNC 前缀 |
| 6 | 隐藏态 WebView2 内存 300MB+ | `TrySuspendAsync` + `MemoryUsageTargetLevel=LOW`，隐藏稳态降至 258MB |
| 7 | Rust 与 collector 抢写 `config.json` | Rust 改用 `app.json`，旧 pinned 一次性迁移，不碰 collector 字段 |
| 8 | 真实设备 slot/unit/interface 变化重新分配 key（P9 抖动） | 同轮唯一 fingerprint 复用 ordinal + `replaces` + 迁移 names/hidden |
| 9 | 单 provider 挂死永久 in-flight；refresh 在轮中丢弃；退出 6.1s + PowerShell 孤儿；`--once` 无蓝牙 | 超时熔断/空槽调度；refresh 延迟重投；daemon + 子进程 registry/Job；等待 BT 首快照 |
| 10 | 蓝牙 watcher 失败后 2s 兜底轮询；常驻 PowerShell 占 P2/P3 预算 | 兜底改为 60s 限频 one-shot；WATCH_SCRIPT 轮询 2s→10s（collector 层 patch，不改上游文件），P2 0.666%→0.386% |
| 11 | 纯蓝牙冷启动首出数间歇 65–128s（首次快照漏设备） | 首快照缺当前可显示设备时 +3/+8/+15/+30s 补采（roots-only 门控）；复测 10/10 1.33–1.45s |
| 12 | 主卡锁定首个到达设备而非最低电量 | 自动模式每次取最低电量，仅用户点击固定；真实 app 复测主卡 12% |
| 13 | **P0：可见启动主线程死锁（9/39≈23%）**：`update_tray_icon` 持 `state.tray` 锁调 `set_icon`，侧车 `tray_mirror` 与主线程 `set_tray_state` 并发互等 | tray API 一律 `run_on_main_thread` 且不持锁调用（clone 句柄后释放锁）；DPI scale 缓存到 AppState；压测可见 40/40 + Lead 独立 20/20 可见启动 0 hang，begin/end 1320/1320 |
| 14 | 蓝牙 watcher 10s 间隔时 P2 5min 波动 0.386–0.662% | 默认间隔再降到 30s；PS CPU 0.078–0.130%（旧 2s 基线 0.547%），P2 5min 复测见 §5 |

审计报告：[design/audit/shell-audit.md](design/audit/shell-audit.md)、[design/audit/collector-audit-independent.md](design/audit/collector-audit-independent.md)、[design/audit/collector-audit.md](design/audit/collector-audit.md)（修复记录）、[design/audit/runtime-audit.md](design/audit/runtime-audit.md)（运行审计）、[design/audit/final-runtime-audit.md](design/audit/final-runtime-audit.md)（最终独立复验，发现 P0 死锁并已修复）。

## 6. 已知限制 / 未验证

- **P7 风险**：隐藏 5 分钟 RSS 226.4→247.7MB（+21.3MB，主要来自常驻 PowerShell watcher）；24h 常驻/每小时采样未跑，不能判定 P7 达标。
- **P4 启动峰值**：隐藏后前 ~20s（TrySuspend 生效前）进程树峰值 449.3MB，超过 320MB；稳态 247.7MB 达标。若指标按“任意时刻”统计则不达标。
- **蓝牙连接检测延迟**：常驻 PowerShell watcher 轮询已从上游 2s 降到 **30s**（P2 0.318%），代价是“设备连接/断开”最多延迟 30s 才反映到托盘；初始快照与连接后的 +3/+8/+15s 快照不受影响。可用 `PB_BT_WATCH_MS` / `config.json:bt_watch_ms` 调回更灵敏的值（2s 时 P2 会回到 0.6%+）。彻底方案仍是 §3.4 的 `CfgMgr32 CM_Get_DevNode_PropertyW` 直读 + WGI 常驻 helper。
- **P6 非精确**：未从真实托盘点击打点到 first-paint，仅有 click→shown 17ms + 预渲染证据。
- **真实设备适配**：本机仅一台可读蓝牙设备（FlyDigi BS2 PRO）；30 个 provider 的 serial/mac/unit 解析、WGI 路径、P9 24h 未真机验证。
- **电源事件**：suspend/resume 的 `new_session` 协议已备好，未接 `WM_POWERBROADCAST`；offline 告警未实现。
- **托盘右键菜单**：已用 debug-only 同分支钩子验证；原生菜单真实鼠标点击因桌面被全屏播放器占用未自动化。
- **Win10 / 多屏 / 100%/150%/200% DPI / 三张壁纸对比度**：未实测；Win10 仅有代码路径与模拟。
- **打包**：NSIS 安装/卸载链路已实测；自动更新（updater）未接线，`build` 字段仍为 `unknown`。
- **单实例**：`Global\` 命名对象跨用户场景未测；测试并行时用 `PB_SINGLE_INSTANCE_NAME` 隔离。

## 7. 配置与日志

```
%LOCALAPPDATA%\PeripheralBattery\
  app.json             # 外壳配置（pinned 等），Rust 写
  config.json          # 采集层配置，collector 独占
  aliases.json         # 稳定 key 映射 + 迁移历史
  history.json         # 剩余时间历史样本
  logs\app.log         # Rust 外壳日志（5×2MB 轮转）
  logs\collector.log   # Python stderr（5×2MB 轮转）
  diagnostics\         # --diag 诊断包
```

开发/验收环境变量：`PB_SHOW_ON_START`、`PB_HIDE_AFTER_MS`、`PB_QUIT_AFTER_MS`、`PB_LOG_LEVEL`、`PB_COLLECTOR`、`PB_PYTHON`、`PB_SINGLE_INSTANCE_NAME`、`PB_FPS_PROBE`（测试用 rAF 采样）。

