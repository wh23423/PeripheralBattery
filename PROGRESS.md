# 施工进度快照（已恢复并完成）

> 暂停点：2026-10-19 01:46；**恢复并完成：2026-10-19 13:22（Asia/Shanghai）**。
> 完成情况：全量重建 collector + Tauri release + NSIS（8.92 MiB，13:12）；pytest **94** / ui_smoke **15** / cargo test **10** 全绿；
> P2 **0.318%**（<0.5%）、P3 0.297%（<0.8%）、P4 稳态 **247.7MB**、P1 250fps/p99 4.2ms；
> P0 可见启动死锁已修复（40/40 + 20/20 压测 0 hang）；F1 主卡、F3 首出数 1.3–1.7s、F4 无误判、F7 stat 摘要、新包安装/运行/卸载全部 PASS。
> 最终指标与剩余风险见 [README.md](README.md) §5–§6 与 `design/verify/lead-acceptance.json`；本文件保留为暂停/续作历史记录，§7 为续作记录。
> 原暂停点说明（历史）：所有 teammate 已停止，无 app/collector 进程、无后台 jobs/构建任务；NSIS 安装包当时仍是 01:14 旧包。

---

## 1. 一句话状态

M0 采集器、M1 Tauri 外壳、M2 前端真实数据接入均已完成并通过各自测试；release + NSIS 安装包曾完成一次全链路验收（安装/运行/卸载、隐藏 CPU/内存、帧率、托盘、单实例、退出无孤儿）。
**最后一轮审计发现的问题已修完代码，但最新改动（前端主卡 F1、toast F5、Rust F4/F7、采集器蓝牙 watcher 降耗/F3）尚未合并进最终 NSIS 包，也尚未复测。** 当前仓库里的安装包是旧的（01:14，不含这轮修复）。

---

## 2. 已完成并验证（可引用）

### 采集器 collector/
- `collector/`：StateStore 完整快照门控、last_seen gone、稳定 key（aliases.json + replaces + names/hidden 迁移）、stdio 五条硬化、协议 hello/device/gone/tick/alert/stat、`--probe/--once/--fake/--diag/--duration`、子进程 registry + Windows Job Object、蓝牙 watcher 60s 兜底、真实 transport 推断。
- 测试：**pytest 93 passed**（01:44 最新，含本次 watcher 优化后的版本）。
- 真实设备证据：本机唯一可读蓝牙设备 FlyDigi BS2PRO（`--probe --duration 8`，level 94）。
- 审计：`design/audit/collector-audit-independent.md`（独立）+ `design/audit/collector-audit.md`（修复记录）。
- 已知未做：CfgMgr32 蓝牙直读、WGI 常驻 helper、suspend/resume、offline 告警、真机 HID/P9 24h。

### Tauri 外壳 src-tauri/
- 窗口 780×440、透明/acrylic、DWM 圆角、Win10 回退、托盘 resvg 状态色、单实例互斥体、失焦隐藏、pinned 持久化。
- sidecar：packaged resource_dir 优先、Job Object、1MiB 行协议、stderr 轮转、shutdown 2s→TerminateJobObject→Job 清空、残留清理（含 `\\?\` 修正）。
- 隐藏四步：`IsVisible(false)` → 事件 → hide；`TrySuspendAsync` + MemoryUsageTargetLevel LOW（隐藏内存 468→222.5MB debug）。
- **Rust 侧 tray_mirror**：不依赖被冻结的 WebView，隐藏期间托盘仍随 device/tick 更新；show 时自动 `{"t":"get","what":"snapshot"}`。
- 测试/构建：`cargo test` **10 passed**、`cargo check` 0 warning、`npx tauri build --no-bundle`（release）通过。
- 审计：`design/audit/shell-audit.md`（P1×2/P2×4 均已修）。
- 最新（01:42）已修：F4 ui_ready 兜底误判、F7 stat 摘要落盘；release exe 已重建。
- 已知未做：updater 接线、Win10 实机、多屏、全屏游戏抑制、`build` 恒 unknown。

### 前端 ui/
- `ui/bridge.js` + `app.js` + `styles.css`：Tauri IPC、浏览器 mock、keyed DOM diff、null 电量、transport/left_s、toast、设置抽屉、隐藏暂停（WAAPI/rAF/timer/DOM）、CSS 性能改造（生产 0 backdrop-filter、specular 外置、涟漪 PNG+transform、无内联 style/script）、CSP 下运行。
- 测试：**node tools/ui_smoke.js → 15 passed**（01:44 最新，含 F1 主卡逐条到达、F5 toast 不遮挡）。
- 审计：`design/audit/shell-audit.md`。
- 最新（01:44）已修：F1 主卡自动取最低电量（用户点击才固定，固定设备 offline/gone 回落）、F5 toast 位置。
- 已知未做：完整「全部设备」视图、按电量排序 FLIP（当前稳定首现序）、real Tauri 自动化 WebDriver。

### 最终运行审计（旧包，01:24–01:38）
- 报告：`design/audit/runtime-audit.md`（证据 `design/audit/evidence/runtime/`、`design/audit/runtime-screens/`）。
- PASS：隐藏启动无窗口、托盘 Rust 镜像（TrySuspend 后仍更新）、最小化恢复、单实例、正常退出/超时杀进程树、NSIS 安装-运行-卸载、可见 FPS、日志 0 ERROR/panic/孤儿。
- 发现：F1 主卡 bug（已修代码，未复测）、F2 P2 口径超标（已用 watcher 降耗处理，**未复测**）、F3 纯蓝牙首出数间歇 65–128s（已修代码，**未复测**）、F4/F5/F7（已修）、F6 启动峰值 RSS 432–448MB（稳态 258MB）、F8 P7/P9 24h 未跑。

### 关键实测数据（旧包，供对照）
- P1：250Hz 显示器，rAF **250fps / p99 4.1ms**（`lead-visible-final.json`、`lead-visible-acrylic.png`）。
- P2：隐藏 5min 全树 **0.666%**（app 0 + WebView2 0.021 + collector 0.130 + PowerShell/conhost 0.515）→ 未达标；app+WebView2 仅 0.021%。
- P3：collector+PS **0.645%** < 0.8% → 达标（余量小）。
- P4：隐藏稳态 **258.3MB** < 320MB；启动前 ~15s 峰值 432MB。
- P5：app start→setup complete **232ms**。
- P6：托盘点击→`window shown` **17ms**（近似，未取 first-paint）。
- P8：`timers_per_s=0.995`。
- P10：正常退出/卸载/强杀/超时均无孤儿。
- 汇总：`design/verify/lead-acceptance.json`、`lead-hidden-final.json/.log`、`lead-visible-final.json`。

---

## 3. 暂停时的未完成态（重要）

1. **采集器 watcher 降耗 + F3 补采**：`collector/btconfig.py` 新增；`collector/collector.py`、`settings.py` 已改；`dist/collector/collector.exe` 已重新 PyInstaller（01:44）；`pytest 93 passed`。**但没有收到 collector-dev 的最终验收报告**（被暂停中断）：尚未确认 `PB_BT_WATCH_MS` 默认 10000 的实际生效、真实 CPU 前后对比、连续 3 次冷启动首出数 ≤30s。
2. **Rust F4/F7**：代码已改，release exe 01:42 已重建；未打 NSIS。
3. **UI F1/F5**：代码已改，ui_smoke 15 passed；未并入 NSIS。
4. **NSIS 安装包是旧的**：`src-tauri/target/release/bundle/nsis/PeripheralBattery_0.1.0_x64-setup.exe`（01:14）不含上述三组修复；`target/release/collector/` 也可能是旧副本（full build 会刷新）。
5. **未复测**：P2/P3/P4（新 watcher 后）、F3 首出数、F1 真实 app 主卡、F4 无误判、F7 日志、安装版新包。
6. **环境已清理**：`%LOCALAPPDATA%\PeripheralBattery\app.json={"pinned":false}`、`config.json={}`；无 peripheral-battery/collector/python 测试进程残留。

---

## 4. 下一步（按顺序执行）

```powershell
cd D:\desktop\电量显示插件
# 0) 环境
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"   # 3.12.9
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
```

1. **复核 collector-dev 改动**（源码 + 测试 + 真实证据）：
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests\collector -q        # 期望 ≥93 passed
   .\.venv\Scripts\python.exe -m collector --probe --duration 20  # 应含 FlyDigi + timers_per_s
   Get-Content collector\btconfig.py
   ```
   确认 `PB_BT_WATCH_MS`（默认 10000）生效：真实模式 60s CPU 采样中 PowerShell CPU 应明显低于 0.5%（目标总 P2 <0.5%）；连续 3 次冷启动首条 device/tray-state ≤30s。
2. **全量重建**（collector sidecar + UI + Rust + NSIS）：
   ```powershell
   .\scripts\build.ps1            # PyInstaller → dist\collector，然后 npx tauri build → NSIS
   ```
3. **回归测试**：`pytest`（≥93）、`node tools/ui_smoke.js`（15）、`cargo test`（10，在 src-tauri 下）。
4. **复测验收**（机器空闲、唯一 `PB_SINGLE_INSTANCE_NAME`）：
   ```powershell
   # P2/P3/P4 隐藏 5 分钟（真实 packaged collector）
   .\.venv\Scripts\python.exe tools\verify_runtime.py hidden --duration 300 --exe src-tauri\target\release\peripheral-battery.exe --json design\verify\lead-hidden-final2.json
   # P1 帧率（PB_FPS_PROBE，fake static；先临时 app.json {"pinned":true} 防止失焦隐藏，测完还原 false）
   .\.venv\Scripts\python.exe tools\verify_runtime.py visible --duration 20 --exe src-tauri\target\release\peripheral-battery.exe --collector-args "--stdio --fake --scenario static --speed 1" --fps --shot design\verify\lead-visible-final2.png --json design\verify\lead-visible-final2.json
   ```
   另外手动/脚本验证：F3 冷启动首出数、F1 真实 app 主卡（最低电量）、F4（pinned=false + PB_SHOW_ON_START=1，失焦隐藏后 2s 不得再 show/无 WARN）、F7（app.log 有 `collector stat: timers_per_s=... key_migrations=...`）。
5. **新 NSIS 安装验收**：静默安装 `PeripheralBattery_0.1.0_x64-setup.exe /S /D=%TEMP%\PB-install-test2`，确认 `collector\collector.exe` + `_internal\python312.dll`，运行安装版 8s 检查 `sidecar spawn ... kind=packaged` + hello，卸载后目录/快捷方式/进程全清。
6. **更新记录**：README §5/§6 实测数字与结论、`design/verify/lead-acceptance.json`；必要时在 `design/audit/runtime-audit.md` 追加「修复后复测」。
7. **结束**：确保无残留进程；最终答复给出运行问题清单（P2 是否达标、F3、启动峰值、P7/P9 24h 未跑、真机/多屏/Win10 未验证等）。

---

## 5. 环境与工具备忘

- Python：`C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe`（PATH 无）；项目 venv `.venv\Scripts\python.exe`。
- Rust：`%USERPROFILE%\.cargo\bin`（bash PATH 不含，需 export）。
- Tauri CLI：项目 `npx tauri`（2.12.3），Rust 端 tauri 2.12.4。
- 显示器：2560×1440 / 125% / 250Hz；`powershell.exe`(5.1) 解析 UTF-8 无 BOM 的中文脚本会失败，脚本请用 **pwsh 7**。
- 验收脚本：
  - `tools/verify_runtime.py`（hidden/visible CPU/RSS/FPS probe/截图；FPS 用 `PB_FPS_PROBE`，CDP 端口被 wry 覆盖不可用）。
  - `tools/capture_screen.ps1`（DPI-aware 整屏截图，用 pwsh 7 调用）。
  - `tools/ui_smoke.js`、`tools/ui_shots.js`、`tools/collector_demo.ps1`、`tools/collector_ndjson_check.py`。
  - `scripts/verify-hidden-tray.ps1`、`verify-timeout-kill.ps1`、`verify-residual-cleanup.ps1`、`verify-f4-f7.ps1`。
- 运行环境变量：`PB_SHOW_ON_START / PB_HIDE_AFTER_MS / PB_QUIT_AFTER_MS / PB_LOG_LEVEL / PB_COLLECTOR / PB_PYTHON / PB_SINGLE_INSTANCE_NAME / PB_FPS_PROBE / PB_BT_WATCH_MS`。
- 日志：`%LOCALAPPDATA%\PeripheralBattery\logs\app.log` / `collector.log`；配置 `app.json`（Rust）+ `config.json`（collector）。
- 无 git 仓库（未 init），不要假设 `git status`。

---

## 6. 文件清单（关键）

| 路径 | 说明 |
|---|---|
| `BUILD_PLAN.md` / `TECH_PLAN.md` | 原始方案 |
| `protocol.md` | 三方协议（已定稿，含 Rust↔前端 §4） |
| `README.md` | 运行/构建/验收/遗留问题（数字待本轮复测后更新） |
| `collector/`、`tests/collector/` | 采集器 + 93 项测试 |
| `src-tauri/` | Tauri 外壳（含 `src/tray_mirror.rs`、`fps.rs`、`single_instance.rs`） |
| `ui/` | 静态前端（bridge/app/styles） |
| `scripts/` | build/dev/verify 脚本 |
| `tools/` | 验收脚本 |
| `dist/collector/` | PyInstaller onedir（12:10 重建，19.4MB） |
| `src-tauri/target/release/` | release exe（12:10 重建）；`bundle/nsis/` 新安装包 8.92 MiB（12:10） |
| `design/verify/` | 验收截图/JSON/日志 |
| `design/audit/` | 5 份审计报告 + 证据（含 final-runtime-audit.md） |

---

## 7. 续作记录（2026-10-19 12:06–13:22）

1. 复核对 collector-dev 暂停前改动：`collector/btconfig.py`（WATCH_SCRIPT 轮询 patch、`PB_BT_WATCH_MS` 可覆盖；F3 首快照 +3/+8/+15/+30s 补采）；pytest 93→94 passed；真实 stdio 首条 FlyDigi 在 1.36–2.1s。
2. 全量重建（多轮）：`scripts/build.ps1`（PyInstaller onedir → Tauri release → NSIS）→ EXIT=0；最终包 `PeripheralBattery_0.1.0_x64-setup.exe` **8.92 MiB（13:12）**，dist collector **13:12**。
3. 最终独立复验（audit-runtime-final）发现 **P0 可见启动主线程死锁 9/39≈23%**（`tray.rs update_tray_icon` 持 `state.tray` 锁调 `set_icon`，侧车 tray_mirror 与主线程 set_tray_state 并发互等）→ tauri-dev 修复：tray API 一律 `run_on_main_thread`、clone 句柄后释放锁、DPI scale 缓存；压测可见 **40/40**、隐藏 10/10，Lead 独立 **20/20**，0 hang，`tray-update begin/end=1320/1320`。
4. 独立复验 F3 仍间歇失败（1/6 30s 无首出数）：collector-dev 把补采门控改为 roots-only（首快照 `roots>0` 即补采一次 +3/+8/+15/+30）；10/10 冷启动 1.33–1.45s，Lead 独立 5/5 1.53–1.71s。
5. P2 仍有波动（10s interval 时 0.386–0.662%）→ 默认 watcher 间隔 10s→30s（PS CPU 0.078–0.130%，连接检测延迟 ≤30s 的产品折衷）；最终 5 分钟复测 **P2 0.318%**、P3 0.297%、P4 稳态 **247.7MB**（启动峰值 449.3MB）。
6. 回归：pytest **94**、ui_smoke **15**、cargo test **10** 全绿；最终 NSIS 包安装 → packaged collector 启动 + hello → 退出 Job=0 → 卸载目录/进程全清。
7. 证据：`design/verify/lead-hidden-final5.json`、`lead-hero-fps.png`、`stress-visible.json`、`tray-stress-result.json`、`lead-acceptance.json`、`release-build-final4.log`；独立报告 `design/audit/final-runtime-audit.md`。
8. 剩余风险（未解决）：P7 24h 内存增长（5 分钟 +21MB）、P4 启动峰值、P9 24h、真实 HID 真机、P6 first-paint、Win10/多屏/DPI 矩阵、PowerShell watcher（CfgMgr32/WGI helper 未做，30s 连接检测折衷）、updater 未接线、`build` 仍 unknown。
