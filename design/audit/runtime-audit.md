# Release 0.1.0 运行情况审计（独立验收）

- 审计人：audit-runtime（只读代码 + 运行测试；未改 `ui/`、`src-tauri/`、`collector/`、`scripts/`、`dist/`、`package.json`）
- 审计时间：2026-10-19 01:24–01:38（Asia/Shanghai）
- 机器：Windows 11 build 26300 / 2560×1440（工作区 2560×1380）/ 125% 缩放 / 250Hz
- 被测产物：
  - `src-tauri/target/release/peripheral-battery.exe`（sha256 `adee947dc13c79af4f710e66544bd6e378edf1302934f6c0adfa5fc011fa288f`，10.9 MB）
  - `src-tauri/target/release/bundle/nsis/PeripheralBattery_0.1.0_x64-setup.exe`（sha256 `4b06786eed46901ffafa457e7bb7751cebbfbd7aa127ed02bba535e8c5227895`，9.35 MB）
  - `dist/collector/collector.exe`（sha256 `248d1c522e722e7a5c5c1fdb91493a1928db96bb56ba4f349cf2a4d7e6f700b4`）
- 方法：每个用例独立 `PB_SINGLE_INSTANCE_NAME=Global\PBAuditRuntime-<name>`；从 app.log 字节偏移读取本次日志；窗口枚举用 PS7 + DPI-aware Win32；FPS 用 `PB_FPS_PROBE`（WebView2 CDP 端口被 wry 覆盖，未用 `tools/verify_fps.js`）。全部用例结束后已确认无 `peripheral-battery`/`collector` 残留，`app.json` 为 `{"pinned":false}`。
- 日志：`%LOCALAPPDATA%\PeripheralBattery\logs\app.log`（审计窗口内 app.log WARN 仅两类，见 §1.8；**0 ERROR、0 panic**）。

## 0. 结论摘要（可发布性）

**技术链路可发布，但不宜直接宣称 P1–P10 全绿。** 隐藏启动 / Rust 托盘镜像（TrySuspend 后仍更新）/ 最小化恢复 / 单实例 / 退出与超时 TerminateJobObject / NSIS 安装-运行-卸载 / 可见帧率全部通过，无崩溃、无孤儿、无 ERROR。

必须由 Lead 决策的两项：

1. **F1 主卡（hero）会锁定到“首个到达的设备”**，而不是最低电量设备（实测主卡 68%，列表里躺着 12%）；UI smoke 的期望是“主卡=最低电量 12%”。**建议发布前修复**（改动小），否则主卡信息可能误导用户。
2. **F2 P2 指标按 BUILD_PLAN 全进程树口径未达标**：隐藏 5 分钟均值 0.666%（Lead 数据）/ 90s 均值 0.901%（本次）＞ 0.5% 目标；超标部分全部来自采集器常驻 PowerShell 蓝牙 watcher（P3 已包含，P3 0.645%/0.797% 仍 < 0.8%）。若 Lead 认可把该 watcher 归入 P3、P2 只统计 app+WebView2（0.021%/0.104%），则 P2 达标——需要口径确认。

其余为不阻塞的改进项（F3–F7）与未验证项（F8：P9/P7 24h 未跑）。

## 1. 必测清单结果

| # | 用例 | 结果 | 关键证据 |
|---|---|---|---|
| 1 | 隐藏启动 90s | **PASS** | `design/audit/runtime-hidden.json`、`evidence/runtime/test1-hidden.log` |
| 2 | 隐藏态托盘正确性 + 第二实例恢复 | **PASS**（托盘不再冻结） | `evidence/runtime/test2-hidden-tray.log`、`runtime-screens/test2-tray-resume.png` |
| 3 | 可见帧率 + DPI 截图 | **PASS** | `evidence/runtime/test3-visible-fps.log`、`runtime-screens/test3-visible-fps.png` |
| 4 | 最小化恢复（P1 回归） | **PASS** | `evidence/runtime/test4-minimize-restore.log`、`runtime-screens/test4-minimize-restore.png` |
| 5 | 单实例 | **PASS** | `evidence/runtime/test5-single-instance.log` |
| 6 | 正常退出 / 超时 TerminateJobObject 无孤儿 | **PASS** | `evidence/runtime/test5-single-instance.log`、`evidence/runtime/test6-timeout-kill.log` |
| 7 | 安装包布局 + 安装版运行 + 卸载 | **PASS** | `evidence/runtime/test7-installer.log` |
| 8 | 日志健康（WARN/ERROR/频率） | **PASS（有 1 条误导性 WARN，见 F4）** | `evidence/runtime/test8-log-health.log` |
| 9 | BUILD_PLAN P1–P10 对照 | **见 §3：P2 未达标、P9/P7 未跑，其余达标** | 本文件 §3.9 |

### 1.1 隐藏启动 90s（用例 1）
命令（实测用了 90s，覆盖 >90s 要求）：
```bash
PB_SINGLE_INSTANCE_NAME="Global\\PBAuditRuntime-hidden1" \
  .venv/Scripts/python.exe tools/verify_runtime.py hidden --duration 90 \
  --exe src-tauri/target/release/peripheral-battery.exe \
  --json design/audit/runtime-hidden.json
```
证据（`evidence/runtime/test1-hidden.log`）：
- `01:25:15.448 app start … pid=41728`；`01:25:15.655 tray created id=peripheral-battery-tray`；`01:25:15.661 sidecar spawn path=\\?\D:\…\target\release\collector\collector.exe … kind=packaged`；`01:25:15.664 setup complete`
- `01:25:15.770 hello protocol=1 session=92e52aa1…`；`01:25:15.771 collector-state state=ready`
- `01:25:15.736 window hidden reason=startup-default`；`01:25:16.037 webview TrySuspend completed suspended=true`
- `hidden_window_probe: [[],[],[]]`（EnumWindows 三次均无可见窗口）；`app_exit_code: 0`；`leftovers: []`
- 段内 `[WARN]`/`[ERROR]` = 0/0
- CPU（90s）：`app 0.0 / webview2 0.104 / collector 0.139 / other 0.658`（合计 **0.901%**）；RSS 稳态 **248.9 MB**，峰值 **448.2 MB**（t=0 启动瞬时；t=20s 后 231.4MB）

### 1.2 隐藏态托盘 + 第二实例恢复（用例 2，关键回归）
命令：`design/audit/runtime-tray-test.ps1`（release exe + `scripts/mock-collector.py`，`PB_MOCK_MUTATE_MS=1500`，默认隐藏启动，14s 后启动第二实例）
证据（`test2-hidden-tray.log`）：
- 隐藏期：`01:28:13.570 TrySuspend completed suspended=true` 之后仍有 **10 条** `tray-state source=rust`（13.612→26.257，level 在 68/12/43 间变化）→ **Rust 镜像在 Suspend 后仍更新，托盘冻结回归已修复**
- 第二实例：`single-instance: existing pid=Some(30736) … signaled=true … exit`，进程 **21ms** 退出
- 恢复链：`activation event` → `window show reason=single-instance requested collector snapshot=true` → `tray-state source=frontend`；**snapshot→前端更新 9ms，window shown→前端更新 5ms**（≪200ms）
- `frontend tray-state level=Some(12)` 与 mock 最新值一致；截图数值：G502 68%、Arctis 12%（充电中）、Keychron 约55%、Xbox 44%（充电中）、WH-1000XM5 43%，与 mock 时间线（i=9：headset 12/充电，gamepad 44）逐项一致
- `RESULT hidden_tray=PASS resume=PASS exit=0 leftovers=0`

### 1.3 可见帧率（用例 3）
命令：`design/audit/runtime-fps-test.ps1`
（env：`PB_SHOW_ON_START=1`、`PB_FPS_PROBE=3000`、`PB_COLLECTOR='…python.exe -m collector --stdio --fake --scenario static --speed 1'`）
证据（`test3-visible-fps.log`）：
```json
FPS_PROBE {"duration_ms":3000,"frames":749,"fps":249.66,"avg_ms":4.01,
 "p50_ms":4,"p95_ms":4.1,"p99_ms":4.1,"max_ms":8.1,"rows":5,
 "panel_rect":{"x":30,"y":30,"w":720,"h":380},"dpr":1.25,
 "inner":{"w":780,"h":440},"backdrop_filter":"none","hidden":false,
 "visibility":"visible","tauri":true}
```
- 窗口：`window shown origin=(1567,812) size=975x550 scale=1.250 dip=(780.0x440.0)`（物理 975×550 = 780×440 DIP；面板 720×380）
- 250Hz 显示器上 rAF=249.66fps、p99=4.1ms 属满帧；若按 P1 的“p99 ≤20ms”判定通过；`backdrop_filter=none`、`tauri=true`
- 截图：`runtime-screens/test3-visible-fps.png`（整屏 2560×1440，DPI-aware，面板完整未裁切）

### 1.4 最小化恢复（用例 4）
命令：`design/audit/runtime-minimize-test.ps1`（为排除自动化失焦干扰，测试期间临时把 `%LOCALAPPDATA%\PeripheralBattery\app.json` 置 `pinned=true`，结束后已恢复 `{"pinned":false}`）
证据（`test4-minimize-restore.log`）：
- 最小化前：`title='外设电量' visible=True iconic=False rect=1567,812 975x550`
- `PostMessage(WM_SYSCOMMAND, SC_MINIMIZE)` 后：`visible=True iconic=True rect=-32000,-32000 199x34`（Win32 确认进入 minimized）
- 第二实例激活：`01:37:36.010 window was minimized -> unminimize before show` → `window shown origin=(1567,812) size=975x550 scale=1.250 dip=(780.0x440.0)`；`IsIconic=False`、`visible=True`
- `RESULT minimize_restore=PASS`（`origin != -32000`、`dip=780x440`、`IsIconic=false` 全部满足）

### 1.5 单实例（用例 5）
命令：`design/audit/runtime-single-test.ps1`
证据（`test5-single-instance.log`）：
- 第二实例 **28ms** 退出，exit=0；期间 `app_count=1 collector_count=1` 不变；日志 `single-instance: existing pid=Some(31160) … exit`；`sidecar_spawn_lines=1`
- 第一实例 `PB_QUIT_AFTER_MS` 退出码 0，`leftover after quit=none`

### 1.6 退出 / 孤儿（用例 6）
- 正常退出：见 1.5，`job object released active_processes=Some(0)（应为 0）`、`app exit`、无残留。
- 超时强杀：`scripts/verify-timeout-kill.ps1 -Exe <release exe>`（collector 用 `cmd /c ping -n 60` 冒充不响应）
证据（`test6-timeout-kill.log`）：
- 退出前树：`peripheral-battery + 6×msedgewebview2 + cmd + conhost + PING`
- `shutdown handshake timeout … terminate`；`TerminateJobObject 终止 3 个进程（before=Some(3) remaining=Some(0)）`；`sidecar exit(code=Some(1))`
- 3 条 WARN 均属该测试的预期路径；**`PASS: no orphan collector/cmd/ping/conhost`**

### 1.7 安装包（用例 7）
命令：`design/audit/runtime-installer-test.ps1`（默认安装到 `%TEMP%\PB-audit-install`）
证据（`test7-installer.log`）：
- 静默安装 exit=0，986ms；安装 64 个文件 / 29.8MB；`collector\collector.exe`、`collector\_internal\python312.dll` 均存在
- 安装版运行：`sidecar spawn path=\\?\C:\Users\…\Temp\PB-audit-install\collector\collector.exe … kind=packaged`；`hello protocol=1` + `collector-state state=ready`；exit=0；无残留
- 卸载（`uninstall.exe /S`）exit=0；安装目录消失；开始菜单/桌面快捷方式（安装前存在）消失；`HKCU\…\Uninstall\PeripheralBattery` 消失；无残留进程
- `RESULT installer=PASS`

### 1.8 日志健康（用例 8）
证据：`evidence/runtime/test8-log-health.log`。审计窗口（01:24:27–01:38）内：
- `[ERROR]`：**0**；`panic/exception`：**0**（仅 2 条 `collector-state state=error` 来自第 6 项故意的 ping 冒充测试）
- WARN 共 15 条，全部可解释：
  - 1 条 `ui_ready not received within 2000ms, showing panel (fallback)` → 兜底条件误判，见 F4
  - 14 条来自超时强杀测试（非 JSON 的 ping 输出、hello timeout、TerminateJobObject/TerminateProcess 预期 WARN）
- 频率样本：
  - Lead 5 分钟隐藏运行（01:15:06–01:20:31）：TrySuspend=1、Resume=0、`tray-state source=rust`=1、`window shown`=0
  - 本次隐藏 90s：TrySuspend=1、Resume=0、rust tray=1
  - mock 隐藏 14s：TrySuspend=1、Resume=1、rust tray=10（变化时更新，约 0.7 次/秒，符合 1.5s 变化节奏）

## 2. 问题清单

> 严重度用 Major / Moderate / Minor / Info；「阻塞发布」是审计建议，最终由 Lead 决定。

### F1【Major｜建议阻塞发布前修复】主卡（hero）锁定到“首个到达的设备”，不随最低电量设备变化

- 现象
  - 可见冷启动（release + mock 逐条推 `device`；真实 collector 也是逐条 device）时：主卡显示 **G502 X PLUS 68%**，列表里 **Arctis Nova 7 12%**（最低、且低电告警/toast 已在提示）却未成为主卡。
  - 隐藏 14s 后第二实例恢复（用例 2）：主卡 **WH-1000XM5 43%**，列表 Arctis 12%，同样不一致。
  - 根因（只读代码定位）：`ui/app.js pickHero()` 在 `state.heroKey` 已存在且在线时**直接返回当前设备**；`render()` 在每一条 `device` 事件后都会执行。于是首个 device（G502 68）到达时锁定主卡，后续更低的 12% 到达也不再切换。
  - UI smoke 自身期望主卡=最低电量：`tools/ui_smoke.js:246` 注释“low：主卡（最低电量 12%）”。浏览器 mock 一次性给全量设备，所以测试通过；真实逐条事件暴露了问题。
- 复现
  ```powershell
  # 可见启动 + mock collector（逐条 device，0.18s 间隔）
  & "D:\desktop\电量显示插件\design\audit\runtime-hero-test.ps1"
  ```
  截图：`design/audit/runtime-screens/test10-hero-visible-start.png`（裁切 `test10-hero-crop.png`）
  日志：`design/audit/evidence/runtime/test10-hero.log`（21.371 首个 device=logi 68 → 21.550 第二个 device=ss 12；主卡未切换）
- 期望：主卡在首轮数据完整（或收到首个完整 `tick`/snapshot）后再选“在线设备中最低电量”；非用户手动选择（`heroPinnedByUser=false`）时，后续出现更低电量设备应切换/或至少首帧不锁定。
- 是否阻塞发布：**建议阻塞**（信息展示错误，且与现有 UI 验收期望冲突；修复成本低）。
- 建议：`pickHero()` 在“非用户固定”时重新取 `lowest(online)`；或首次 hero 选择延迟到首个 tick / 首轮 round 完成；或对首批 device 做 100–200ms 合并再渲染。修复后补一条“逐条 device 到达”的 UI 回归用例。

### F2【Moderate｜指标口径未达标，不阻塞】P2 隐藏进程树 CPU 按全树口径 > 0.5%

- 现象：隐藏稳态进程树 CPU（app + WebView2 + collector + 其 PowerShell/conhost 子进程）
  - 本次 90s：**0.901%**（app 0.0 / webview2 0.104 / collector 0.139 / **other 0.658**）
  - Lead 5 分钟：**0.666%**（app 0.0 / webview2 0.021 / collector 0.13 / **other 0.515**）
  - 其中 `other` 是采集器的**常驻 PowerShell 蓝牙 watcher**（RSS 132.7MB/121.6MB）+ 2 个 conhost；app+WebView2 自身仅 **0.104%/0.021%**。
- 证据：`design/audit/runtime-hidden.json`（`cpu_single_core_pct`、`rss_breakdown`）、`design/verify/lead-hidden-final.json`
- 期望：P2 < 0.5%（BUILD_PLAN §0 原文把 powershell 子进程列入 P2 统计；P3 又把“采集进程 + 其 PowerShell 子进程”列入 <0.8%）。
- 是否阻塞发布：**否**（无功能影响；P3 0.797%/0.645% 仍 <0.8%；P2 超标全部来自上游蓝牙 watcher）。
- 建议：Lead 明确 P2/P3 归属（二者都含 PowerShell 会重复计罚）；若要压 P2，需要降低常驻 PowerShell watcher 的 CPU/RSS（或改为原生/常驻 helper），并把结果写进验收记录。

### F3【Moderate｜不阻塞】纯蓝牙设备冷启动“首次出数”不稳定，最长 ~128s

- 现象：默认隐藏启动后，托盘一直显示默认“未知”图标，直到首次 device 到达：
  - Lead 5 分钟 run：`01:15:06.643` start → **`01:17:15.003`** 首条 `tray-state source=rust`（**128.4s**）
  - 本次 90s run：`01:25:15.448` start → **`01:26:20.796`**（**65.3s**）
  - 另两次（安装版 01:33:43.476 → 01:33:45.257 = 1.78s；单实例 run 01:32:48.793 → 01:32:51.155 = 2.36s）正常 → 间歇性（2/4 慢）
  - `collector.log` 显示前两次 BT watcher snapshot 只报了 `WH-1000XM4 hidden`，没有 `FlyDigi BS2PRO`；第 3 次 snapshot 才出现 → 上游 Bluetooth watcher 首次快照漏设备，下一次要等 60s。
- 证据：`%LOCALAPPDATA%\PeripheralBattery\logs\app.log` 上述时间戳；`collector.log` 01:15:09/01:16:12/01:17:14 三次 snapshot。
- 期望：首次数据应在数秒内可用（P5 仅约束托盘可用，出数无独立指标；但隐藏托盘是唯一指示器，长期“未知”影响体验）。
- 是否阻塞发布：否（间歇、上游行为；不影响进程/退出正确性）。
- 建议：首轮 snapshot 没有 online 设备时，5s 后强制再 refresh 一次；或把最初两次 Bluetooth 轮询间隔缩短（如 5s），并在 P9/24h 中统计“首次出数时间”。

### F4【Minor｜不阻塞】2s 启动兜底日志文案错误，且可能把用户主动隐藏的面板重新拉起

- 现象：`2026-10-19 01:30:50.290 [WARN] ui_ready not received within 2000ms, showing panel (fallback)`——实际 `ui_ready` 已收到（窗口已 `window shown reason=ui-ready`），只是 270ms 失焦自动隐藏把 `window_visible` 置 false，兜底条件 `!window_visible()` 误触发并把窗口重新 show 出来（日志随即出现 `window was minimized -> unminimize before show`）。
- 证据：app.log `01:30:50.290`（测试 4 的重跑被 `pinned=true` 后不再触发）；`design/audit/runtime-minimize-test.ps1` 首版运行记录。
- 期望：兜底只在“确实未收到 ui_ready”时触发；且不覆盖用户/失焦的主动隐藏。
- 是否阻塞：否。建议：`ui_ready` 用 AtomicBool 标记；兜底条件改为 `!ui_ready_received && !window_visible`，并把文案改成实际原因。

### F5【Minor｜不阻塞】低电量 toast 覆盖列表行内容

- 现象：截图 `test2-panel-crop.png` / `test10-hero-crop.png` 中，右下角 `toast`（“Arctis Nova 7 电量低”）覆盖第 4 行（WH-1000XM5）右侧数值区域；toast 未把列表挤出可视区。
- 期望：toast 不遮挡关键电量/剩余时间（或固定为面板底部独立条、列表底部留 padding）。
- 是否阻塞：否（视觉；可点击关闭）。

### F6【Info｜口径提示】隐藏启动瞬时峰值内存 448.2MB，稳态 248.9MB

- 现象：`runtime-hidden.json` `peak_rss_mb=448.2`（t=0，WebView2 初始化期），`t≥20s` 后 231–249MB；Lead 5 分钟 `rss_mb=258.3`、`peak=432.1`。
- 期望：P4 若按稳态 → PASS（<320MB）；若按峰值 → 短暂超标。建议验收口径写清“稳态”（本次按稳态记录）。
- 是否阻塞：否。

### F7【Info｜可观测性】`collector stat` 被截断到 400 字符，`timers_per_s`/`key_migrations` 不进 app.log

- 现象：`sidecar.rs` 对 `stat` 行做 400 字符截断，而 `providers` 字典很长，`timers_per_s` 永远落在截断之后；app.log 的 stat 行以 `…` 结尾，P8 无法从 app.log 复核。
- 证据：app.log `01:26:16.243 collector stat: {…"hyperx":{"polls":4…`（`…` 结尾）；P8 本次通过 collector 独立 probe 验证：`test8-collector-probe-speed1.log` `timers_per_s=0.993`。
- 是否阻塞：否。建议：stat 单独打一行摘要（timers_per_s/key_migrations/最慢 provider），或提高截断上限。

### F8【未验证项】P9（24h key 抖动）/ P7（24h 内存增长）未跑

- 现象：本次审计为短时运行（≤90s/用例，最长隐藏 5 分钟引用 Lead 数据），未做 24h 长稳/插拔/休眠唤醒循环。
- 影响：不能宣称 P1–P10 全绿；若发布门禁要求 24h，需另排期。其余 P1/P3/P4/P5/P8/P10 有本次/Lead 数据支撑。

## 3. 与 BUILD_PLAN 指标对照（P1–P10）

| # | 目标 | 实测（本次 / Lead） | 判定 |
|---|---|---|---|
| P1 | 面板可见 58–60fps，p99≤20ms | **249.66fps / p99 4.1ms**（250Hz 屏满帧；720×380、dpr1.25、backdrop=none、tauri=true） | **PASS**（按 250Hz 环境换算） |
| P2 | 隐藏进程树 CPU <0.5% | **0.901%（90s）/ 0.666%（5min）**；app+webview2 仅 0.104%/0.021% | **未达标（全树口径）；按 app+webview2 口径达标**，见 F2 |
| P3 | 采集器 CPU <0.8%（含 PowerShell） | **0.797%（90s）/ 0.645%（5min）** | **PASS（余量很小）** |
| P4 | 进程树内存 <320MB | 稳态 **248.9MB / 258.3MB**；启动峰值 448.2/432.1MB | **PASS（稳态口径）**，见 F6 |
| P5 | 冷启动到托盘可用 <1.5s | `app start 01:25:15.448 → tray created 01:25:15.655` ≈ **207ms**；Lead 232ms | **PASS** |
| P6 | 点托盘→面板首帧 <200ms | 未做真实托盘点击（release 无测试钩子）；第二实例激活 21–28ms、snapshot→前端更新 9ms、shown→前端 5ms | 代理指标 **PASS**（非直接测量） |
| P7 | 24h 内存增长 <15MB | 未跑 | **未验证** |
| P8 | 定时器唤醒 ≤1 次/秒 | collector probe `timers_per_s=0.993`（`--fake --speed 1`）；fake 默认 speed=6 时为 5.873（tick_period=1/speed，属测试夹具缩放，非生产） | **PASS**（生产口径） |
| P9 | 24h 设备 key 抖动 <2 次 | 未跑 | **未验证** |
| P10 | 更新/退出后孤儿 0 | 正常退出/单实例/超时强杀/卸载：**0 残留**；`job object released active_processes=Some(0)` | **PASS** |

补充：P2/P4 采用本次 90s 采样 + Lead 5 分钟数据；两者数量级一致（90s 因含启动期 WebView2 初始化，CPU/RSS 略高）。

## 4. 未覆盖 / 残余风险

- 24h 长稳（P7/P9）：插拔、休眠唤醒、key 迁移、内存增长未验证。
- 真实托盘点击路径：release 构建移除了 `PB_TEST_TRAY_ACTION`（debug-only），本机无物理点击自动化；P6 用第二实例激活代理。
- DPI 矩阵：只在 125% 单屏验证；100%/150%/多屏/任务栏在屏幕顶部等场景未测。
- Win10 回退（无 DWM 圆角/acrylic 关闭）未测（本机 Win11 build 26300）。
- 安装包只测了 currentUser + 默认路径 + 中文用户名环境；非管理员/自定义路径未测。

## 5. 复现脚本与证据索引

复现脚本（审计新增，均不修改产品代码）：
- `design/audit/runtime-tray-test.ps1`（用例 2：隐藏托盘 + 第二实例恢复）
- `design/audit/runtime-fps-test.ps1`（用例 3：可见 FPS）
- `design/audit/runtime-minimize-test.ps1`（用例 4：最小化恢复）
- `design/audit/runtime-single-test.ps1`（用例 5：单实例/正常退出）
- `design/audit/runtime-installer-test.ps1`（用例 7：安装-运行-卸载）
- `design/audit/runtime-hero-test.ps1`（F1：主卡选择）
- `scripts/verify-timeout-kill.ps1 -Exe <release exe>`（用例 6）

证据文件：
- `design/audit/runtime-hidden.json`（用例 1 机器可读结果）
- `design/audit/evidence/runtime/test1-hidden.log` … `test10-hero.log`
- `design/audit/evidence/runtime/test8-collector-probe-speed1.log`（P8 timers_per_s）
- `design/audit/runtime-screens/test2-tray-resume.png`、`test2-panel-crop.png`、`test3-visible-fps.png`、`test4-minimize-restore.png`、`test10-hero-visible-start.png`、`test10-hero-crop.png`
- 参照：`design/verify/lead-hidden-final.json`、`design/verify/lead-acceptance.json`

## 6. 审计过程对环境的改动（已还原）

- 每个用例独立 `PB_SINGLE_INSTANCE_NAME`，未与 Lead 的默认单实例冲突。
- 用例 4 为屏蔽自动化失焦，临时把 `%LOCALAPPDATA%\PeripheralBattery\app.json` 设为 `{"pinned":true}`，结束后已还原为 `{"pinned":false}`（已核对）。
- 安装测试目录 `%TEMP%\PB-audit-install` 已随卸载删除；测试结束确认无 `peripheral-battery.exe` / `collector.exe` 残留。
