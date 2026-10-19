# 最终独立运行复验（Final Runtime Audit）

- 复验人：`audit-runtime-final`（只读源码/配置；只跑测试；未改 `ui/`、`src-tauri/`、`collector/`、`scripts/`、`dist/`、`package.json`，未构建）
- 时间：2026-10-19 12:20–12:43（Asia/Shanghai）
- 机器：Windows 11 build 26300 / 2560×1440（工作区 2560×1380）/ 125% / 250Hz
- 被测产物（本轮实测 sha256）：
  - `src-tauri/target/release/peripheral-battery.exe` 10,906,112 B `56fcf6a6…f922e4`
  - `src-tauri/target/release/bundle/nsis/PeripheralBattery_0.1.0_x64-setup.exe` 9,351,160 B (8.918 MiB) `847047ec…ffb07b`
  - `src-tauri/target/release/collector/collector.exe` 2,127,615 B `255211db…193fda`
- 方法：每个用例独立 `PB_SINGLE_INSTANCE_NAME=Global\PBAuditFinal-<name>-<随机>`；从 app.log 字节偏移读本次日志；可见用例前把 `%LOCALAPPDATA%\PeripheralBattery\app.json` 置 `{"pinned":true}`，结束后已还原 `{"pinned":false}`；collector 的 `config.json` 未改。审计脚本/证据在 `design/audit/final-runtime-*`。
- 结论先行：**不可发布。** 清单 8 项中 6 项 PASS、1 项 FAIL（F3 间歇）、并新增 1 个**可复现的可见态 UI 线程死锁**（P0 阻塞）。

## 0. 复验清单结果

| # | 用例 | 结果 | 关键证据 |
|---|---|---|---|
| 1 | 隐藏 60s（release + packaged collector） | **PASS** | `design/audit/final-hidden.json`；`final-runtime-evidence/test1-hidden.log` |
| 2 | 可见 FPS + DPI 截图 | **PASS** | `final-runtime-evidence/test2-visible-fps.log`、`final-runtime-screens/test2-visible-fps.png` |
| 3 | F1 逐条 device 到达 + ui_smoke | **PASS** | `final-runtime-screens/test3-f1-mock-hero.png`、`final-runtime-evidence/test3-ui-smoke.log` |
| 4 | F3 冷启动首出数 ×2 | **FAIL（间歇，1/6 次 >30s）** | `final-runtime-evidence/test4-f3*.json`、`test4-f3-batch1-attempt0.log` |
| 5 | F4 失焦兜底误判 | **PASS** | `final-runtime-evidence/test5-f4.json`、`test5-f4-attempt0.log` |
| 6 | F7 stat 摘要可观测 | **PASS** | `test1-hidden.log`（60s 处 stat 行） |
| 7 | 安装包大小/内容/安装-运行-卸载 | **PASS** | `final-runtime-evidence/test7-installer.log` |
| 8 | 日志健康（0 ERROR/0 panic） | **PASS（0 WARN，但死锁无日志）** | `final-runtime-evidence/test8-log-health.txt` |
| A | **新增：可见启动 UI 线程死锁（P0）** | **FAIL / 阻塞发布** | `final-runtime-evidence/hang-rate.txt`、`test9-hang.json`、`test2-visible-loop*.log` |

### 1) 隐藏 60s（PASS）
命令：
```bash
PB_SINGLE_INSTANCE_NAME='Global\PBAuditFinal-hidden1' \
  .venv/Scripts/python.exe tools/verify_runtime.py hidden --duration 60 \
  --exe 'src-tauri\target\release\peripheral-battery.exe' \
  --json 'design\audit\final-hidden.json'
```
证据（`final-hidden.json`）：
- `sidecar spawn … target\release\collector\collector.exe … kind=packaged`，`hello protocol=1`，`collector-state state=ready`；`window hidden reason=startup-default` + `TrySuspend completed suspended=true`。
- `hidden_window_probe=[[],[],[]]`（3 次枚举无可见标题窗口）。
- **P2 进程树 CPU：app 0.0 + WebView2 0.026 + collector/PowerShell 0.416 + other 0.0 = 0.442% < 0.5% PASS**（余量仅 0.058pp，高于 Lead 的 0.386%）。
- **RSS 稳态 234.3MB < 320MB PASS**；启动瞬时峰值 440.3MB（t=0，若按“任意时刻”口径超标）。
- `app_exit_code=0`，`leftovers=[]`，本段 0 WARN/0 ERROR。

### 2) 可见 FPS（PASS）
命令（脚本 `design/audit/final-runtime-fps.ps1`，等价于清单命令 + 截图）：
```powershell
pwsh -File design/audit/final-runtime-fps.ps1 -Instance 'Global\PBAuditFinal-fps7b4'
# env: PB_SHOW_ON_START=1 PB_FPS_PROBE=2000
#      PB_COLLECTOR='.venv\Scripts\python.exe -m collector --stdio --fake --scenario static --speed 1'
#      app.json pinned=true；capture_screen.ps1 截图
```
app.log 最后一条 `FPS_PROBE`（12:24:56.948）：
```json
{"duration_ms":2000,"frames":500,"fps":249.51,"avg_ms":4.01,"p50_ms":4,"p95_ms":4.1,
 "p99_ms":4.2,"max_ms":8.1,"rows":5,"panel_rect":{"x":30,"y":30,"w":720,"h":380},
 "dpr":1.25,"inner":{"w":780,"h":440},"backdrop_filter":"none","hidden":false,
 "visibility":"visible","tauri":true}
```
→ fps≈250（本机 250Hz 满帧）、p99 4.2ms≤20ms、rows=5、panel 720×380、backdrop_filter=none、tauri=true。另 5 次可见启动复测 249.5–250.01fps、p99 4.2–4.3ms（`test2-visible-loop.log`）。
截图 `test2-visible-fps.png`（2560×1440，DPI-aware，面板完整）：static 夹具主卡=**WH-1000XM5 41%**，为 5 台中最底（41<52<55<68<80），F1 逻辑正确。
**口径说明**：清单里“fake static：Arctis 12%”与当前夹具不符——`collector/fake.py::static_states` 只有 G502 68 / BlackShark 55 / Keychron 52 / Xbox 80 / XM5 41，没有 Arctis；Arctis 12% 属 `scripts/mock-collector.py`（见第 3 项）。

### 3) F1 逐条到达 + ui_smoke（PASS）
命令：`.venv/Scripts/python.exe design/audit/final-runtime-f1.py`（可见 + `PB_COLLECTOR=<py> scripts\mock-collector.py`，设备 0.18s 间隔逐条到达，无突变）
证据：`test3-f1-mock-attempt0.log` 5 条 `collector device key=…` 依次到达；截图 `test3-f1-mock-hero.png`：**主卡 Arctis Nova 7 12%（最低，建议充电）**；列表 G502 68 / Keychron 55(约) / Xbox 84 充电中 / XM5 43；面板 5 行、720×380；`exit_code=0`，0 WARN。
`node tools/ui_smoke.js` → **15 passed, 0 failed**（`test3-ui-smoke.log`；注意 ui_smoke 会重写 `design/verify/ui-*.png` 测试截图，属既有测试行为）。

### 4) F3 冷启动首出数（FAIL，间歇）
命令：`.venv/Scripts/python.exe design/audit/final-runtime-f3.py <tag>`（隐藏、唯一实例、`PB_QUIT_AFTER_MS=30000`、packaged collector）
6 次冷启动，app start → 首条 `tray-state source=rust`/device：

| 批次 | run0 | run1 |
|---|---|---|
| a (12:34) | **>30s（无任何 device/tray-state）** | 1.54s |
| b (12:36) | 1.66s | 1.63s |
| c (12:37) | 1.66s | 1.57s |

- 5/6 ≈1.5–1.7s（与 Lead 结论一致）；**1/6 在 30s 内没有任何首出数**（`test4-f3-batch1-attempt0.log`：12:34:24.699 app start → 12:34:54.915 quit，中间无 `tray-state source=rust`/device）。
- `collector.log` 12:34:26.284 首快照只有 `WH-1000XM4 … WinRT=False PnP=False -> hidden`，缺唯一可读设备 FlyDigi BS2PRO；+3/+8/+15s 补采**没有发生**。
- 只读定位：`collector/btconfig.py:45-49` 的补采门控为 `roots.Count>0 && (!hasOnline || !hasBattery)`；本例快照含一台 `status=OK` 但未连接的配对耳机（有缓存电量），于是被判“健康”不补采，要等 60s 全量快照。→ **F3 修复只覆盖“首快照为空/无电量”分支，未覆盖“只剩非连接设备”分支。**

### 5) F4 失焦兜底（PASS）
命令：`.venv/Scripts/python.exe design/audit/final-runtime-f4.py`（`PB_SHOW_ON_START=1`、`app.json pinned=false`、启动后 ~1s Notepad 抢焦点）
证据（`test5-f4-attempt0.log` / `test5-f4.json`）：
- `12:33:58.883 auto-hide triggered …` → `12:33:58.885 window hidden reason=focus-lost`。
- **无** `ui_ready not received within 2000ms`，**无** fallback re-show（隐藏后无任何 `window show/shown`）。
- `12:33:59.891 ui_ready received; fallback thread exits (window_visible=false)`；`exit_code=0`，0 WARN。

### 6) F7 stat 摘要（PASS）
`test1-hidden.log` 12:22:19.449（60s 运行处）：
`collector stat: timers_per_s=0.99 key_migrations=0 providers=30 slowest=mchose:2.0ms`
→ 摘要行不再被 400 字符截断，P8 可直接从 app.log 复核。（P8 生产口径 0.99 ≤1 次/秒。）

### 7) 安装包（PASS）
命令：`pwsh -File design/audit/final-runtime-installer.ps1`（`/S /D=%TEMP%\PB-audit-final`，安装版隐藏运行 6s，`uninstall.exe /S`）
证据（`test7-installer.log`）：
- setup 存在，**8.918 MiB > 8.5MB**；静默安装 exit=0、869ms、64 文件 29.9MB。
- `collector\collector.exe`、`collector\_internal\python312.dll` 均存在。
- 安装版从安装目录 packaged 启动：`sidecar spawn …\PB-audit-final\collector\collector.exe … kind=packaged`、`hello protocol=1`、`state=ready`、`app exit`、exit=0、无残留。
- 卸载 exit=0，目录消失，开始菜单/桌面快捷方式、`HKCU\…\Uninstall\PeripheralBattery` 全部消失，0 残留、0 WARN/ERROR。

### 8) 日志健康（PASS，但有盲区）
`test8-log-health.txt`（12:20:30 之后全部运行）：
- app.log 2158 行：**WARN=0 / ERROR=0 / panic=0**；`ui_ready not received`=0、`fallback`=0、`collector-state state=error`=0。
- collector.log 59 行：WARN=0 / ERROR=0 / panic=0。
- 无需解释的 WARN；**但 B1 死锁全程 0 日志**（只有“少了一行 `托盘图标更新`”的间接特征），可观测性不足。

## 1. 新增阻塞问题 B1【P0】：可见启动/首次显示时主线程死锁（~23% 可见启动）

- 复现：`design/audit/final-runtime-visible-loop.py` / `final-runtime-hang-repro.py`。**可见启动 39 次中 9 次冻结（23%）**；隐藏启动 10/10 正常（清单 1、F3 6 次、安装版 1 次等均正常退出）。
- 症状：面板已显示（有时只渲染到第 2 台设备），随后整窗无响应；`PB_QUIT_AFTER_MS` 到点后退出线程打完 `quit collector stopped, exiting`，但 `RunEvent::ExitRequested/Exit` 永不出现、`app exit` 缺失 → 进程必须 `taskkill /T /F`（正常退出被破坏）。
- 冻结时诊断（`test9-hang.json` attempt=2）：
  - `Tauri Window` visible=true，但 **WM_NULL `SendMessageTimeout`=0**；`tray_icon_app`/`Tao Thread Event Target` 等同进程窗口全部 0 → 主线程不泵消息；
  - 34 线程、CPU 仅 0.09/0.05s（阻塞而非忙等）；5×msedgewebview2 + collector/python 子进程仍存活。
- 日志指纹（每次冻结完全一致）：`tray-state source=rust … kind=mouse/headset` 各跟一条 `托盘图标更新`；到 `tray-state source=rust … kind=keyboard` 时**没有** `托盘图标更新`；紧接着 `tray-state source=frontend level=Some(68)` 后**也没有** `托盘图标更新`；之后日志静默。
- 只读根因（高置信）：
  1. `src-tauri/src/tray.rs:205-220 update_tray_icon()` 在持有 `state.tray` 互斥锁的情况下调用 `tray.set_icon()/set_tooltip()`；Win32 `Shell_NotifyIcon` 是同步消息，托盘窗口过程在**主线程**。
  2. 侧车（tray mirror）线程 `state.rs:109-127 → tray_mirror.rs:171-208` 与主线程上的同步命令 `commands.rs:54-68 set_tray_state` 会并发进入 `update_tray_icon()`。
  3. 侧车先拿到 `state.tray` 锁并阻塞在 `Shell_NotifyIcon`（等主线程处理托盘消息）；主线程同时在 `set_tray_state` 里等同一把锁 → 主线程不再泵消息 → 互相等待，永久死锁。
  4. 触发窗口集中在启动/显示后的设备突发（3 次以上托盘目标变化，<2ms 内），与“第 3 台设备时冻结”完全吻合；系统繁忙时 Shell 调用变慢，概率升高。
- 影响：用户打开面板即可能卡死（必须杀进程）；P5/P6/P10 在可见态不成立；不可发布。
- 建议修复（供参考）：托盘更新单线程化——所有 `set_icon/set_tooltip` 只在主线程执行（`app.run_on_main_thread` 串行队列），或不在持锁跨 `Shell_NotifyIcon`；同时保留 payload 去重。

## 2. 其它残留/口径

- **F3 残余**（上文 4）：建议把补采条件改为“首快照在线/可读设备数 < 预期或没有 `connected&status OK` 设备”也补采（至少 +3/+8/+15s），并补一条“首快照只剩离线配对设备”的采集器单测。
- **P2 余量小**：0.442% 接近 0.5%；collector+PowerShell watcher 仍是 0.416% 主项。
- **P4 启动峰值** 440.3MB：稳态 234.3MB 达标；若指标按任意时刻则超标。
- **P1 有效**：250Hz 下 249.5–250.0fps；但可见运行本身受 B1 影响。
- 夹具差异见第 2 节说明（Arctis 12% 属 mock，不属 fake static）。

## 3. 复验结论

**不可发布。** 阻塞项：
1. **B1 可见启动/显示主线程死锁（9/39，需 taskkill）** —— 必须修复并复测（至少 40 次可见启动 0 冻结 + 正常退出）。
2. **F3 冷启动首出数仍间歇 >30s（1/6）** —— 建议修复补采门控后复测 10 次冷启动。

其余清单项（隐藏 60s/P2/RSS/退出、P1 FPS、F1 主卡、F4、F7、安装/卸载、日志 0 ERROR/panic）本轮独立复验可重复 PASS。

## 4. 环境还原与证据索引

- `%LOCALAPPDATA%\PeripheralBattery\app.json` 已还原 `{"pinned":false}`；已确认无 `peripheral-battery.exe`/`collector.exe` 残留；`%TEMP%\PB-audit-final` 已随卸载删除。
- 新增/复用的审计脚本（仅在 `design/audit/final-runtime-*`，非产品代码）：`final-runtime-fps.ps1`、`final-runtime-f1.py`、`final-runtime-f3.py`、`final-runtime-f4.py`、`final-runtime-installer.ps1`、`final-runtime-visible-loop.py`、`final-runtime-visible-steady.py`、`final-runtime-hang-repro.py`。
- 证据目录：`design/audit/final-runtime-evidence/`（含 `hang-rate.txt`、`hang-analysis.txt`、`test9-hang.json`、各用例 log/json）；截图：`design/audit/final-runtime-screens/`；机器可读：`design/audit/final-hidden.json`。
