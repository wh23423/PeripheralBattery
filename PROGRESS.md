# 施工进度快照（已恢复并完成）

> 暂停点：2026-10-19 01:46；**恢复并完成：2026-10-19 13:22（Asia/Shanghai）**。
> 完成情况：全量重建 collector + Tauri release + NSIS（8.92 MiB，13:12）；pytest **94** / ui_smoke **15** / cargo test **10** 全绿；
> P2 **0.318%**（<0.5%）、P3 0.297%（<0.8%）、P4 稳态 **247.7MB**、P1 250fps/p99 4.2ms；
> P0 可见启动死锁已修复（40/40 + 20/20 压测 0 hang）；F1 主卡、F3 首出数 1.3–1.7s、F4 无误判、F7 stat 摘要、新包安装/运行/卸载全部 PASS。
> 最终指标与剩余风险见 [README.md](README.md) §5–§7 与 `design/verify/lead-acceptance.json`；本文件保留为暂停/续作历史记录，§7 为续作记录。
> 原暂停点说明（历史）：所有 teammate 已停止，无 app/collector 进程、无后台 jobs/构建任务；NSIS 安装包当时仍是 01:14 旧包。
>
> **v0.1.1（2026-10-19 晚）**：按 `review/REVIEW-REPORT.md` 修复 A1–A14（数据目录/卸载、WebView2、发布安全、
> 窗口/生命周期/采集器/前端）。v0.1.1 施工记录与 packaging 验收项见 **§8**；Release Notes 见
> [docs/release-notes-v0.1.1.md](docs/release-notes-v0.1.1.md)。
>
> **v0.1.2（2026-10-19 深夜）**：修复用户反馈的三个问题（拖拽死区 / 未知连接 / 剩余时间冷启动未知），
> 记录见 **§9**；Release Notes 见 [docs/release-notes-v0.1.2.md](docs/release-notes-v0.1.2.md)。

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

---

## 8. v0.1.1 修复记录（2026-10-19 20:30 起，评审 A1–A14）

目标：按 `review/REVIEW-REPORT.md` 修复 14 项 P1 + 高价值 P2，发布 v0.1.1（`tauri.conf.json` / `Cargo.toml` / `package.json` 版本 0.1.1）。
并行分工：Lead + 5 名 teammate（packaging / rust-window / rust-lifecycle / collector / frontend），
Release Notes：[docs/release-notes-v0.1.1.md](docs/release-notes-v0.1.1.md)。

### 8.1 packaging（A1/A2/A3 + 发布文档）——已完成，待 Lead 最终验收

| 项 | 实现 | 验证 |
|---|---|---|
| A1 数据目录 | `src-tauri/src/paths.rs`：数据目录迁 `%APPDATA%\PeripheralBattery`；`PB_DATA_DIR` 覆盖并 `set_var` 回写供 collector sidecar 继承；首启迁移 0.1.0 旧目录（config/app/aliases/history/logs/diagnostics，只补缺失、写 `migration-v0.1.1.json`、失败不阻塞并写 app.log） | 新增 5 个单测（迁移/不覆盖/marker 幂等/缺失/同目录）；当时全仓 48 passed，最终全仓 **55 passed**（Lead 复核） |
| A1 卸载 | `src-tauri/nsis/installer-hooks.nsh`（tauri.conf `bundle.windows.nsis.installerHooks`）：`NSIS_HOOK_POSTUNINSTALL` 无条件删 HKCU `Software\peripheralbattery\PeripheralBattery`（publisher 与 0.1.0 相同，键不变；若未来 publisher 变化条件编译再清旧 vendor 键）、清 WebView2 profile `%LOCALAPPDATA%\com.peripheralbattery.app`、守卫式 `RMDir /r "$INSTDIR"`；`$DeleteAppDataCheckboxState=1` 或 `/DELETEAPPDATA` 才删 `%APPDATA%\PeripheralBattery`；`/UPDATE` 路径不清理；未勾选删除时把 0.1.0 的 4 个配置文件先复制到新数据目录（保证“先卸载再装”不丢数据） | `makensis -INPUTCHARSET UTF8` 静态编译 hooks 通过（peripheralbattery/wh23423 两种 publisher 变体均 0 error/unknown 变量）；默认路径安装→卸载待 Lead 用 `scripts/verify-uninstall.ps1` 复验 |
| A1 验收脚本 | `scripts/verify-uninstall.ps1`：静默安装到 `%TEMP%` 自定义目录→写 0.1.0 风格数据→静默卸载→断言 INSTDIR/HKCU/WebView2 profile/数据目录；默认不删真实数据，`-DeleteAppData -ConfirmDeleteAppData` 才测删除；有已有安装/进程时拒绝执行（`-Force` 才继续） | PowerShell Parser 解析通过；真实运行由 Lead 执行 |
| A2 WebView2 | `tauri.conf.json`：`bundle.windows.webviewInstallMode={type:"embedBootstrapper",silent:true}`、`minimumWebview2Version="118.0.0.0"`；新增 `src-tauri/src/webview2_check.rs`（`GetAvailableCoreWebView2BrowserVersionString` + 版本比较 + MessageBoxW 中文提示 + 退出码 2/3，`PB_SKIP_WEBVIEW2_CHECK=1` 逃生口）；main.rs 由 rust-lifecycle 集成 `mod webview2_check;` + `ensure_available_or_exit()`（logging 之后、Builder 之前） | `npx tauri build --no-bundle` 通过；5 个单测；NSIS 构建实测成功（bootstrapper 已从 go.microsoft.com 下载并内嵌，setup 10.64 MiB），断网/旧版场景待在干净环境复验 |
| A3 元数据 | tauri.conf：publisher `peripheralbattery`（与 0.1.0 注册表键一致，避免升级时 MANUPRODUCTKEY 变化）、copyright `Copyright (c) 2026 wh23423`、MIT license + `licenseFile=../LICENSE`、short/long description、homepage、category；NSIS 语言 English+SimpChinese；`LICENSE`/`THIRD_PARTY_NOTICES.md` 随包映射到安装目录 `licenses\`；窗口 720×380（A14 备选 B） | release exe 版本信息实测：FileVersion/ProductVersion 0.1.1、company/copyright/filedescription 元数据；NSIS 生成物 defines 已确认（publisher/embedBootstrapper/minWebView2/languages/license 页） |
| A3 build 号 | `src-tauri/build.rs`：监听 `.git/HEAD`、`.git/refs/heads`、`.git/packed-refs`（worktree 兼容）+ `PB_BUILD_SHA`/`PB_BUILD` 注入，输出 `APP_BUILD`/`PB_BUILD` | release exe 内实测 `build=5d096f5`（不再 unknown） |
| A3 校验和/签名 | `scripts/build.ps1`：构建后调用 `scripts/sign.ps1`（`PB_SIGN_THUMBPRINT` 或 `PB_SIGN_CERT_PATH+PASSWORD`，signtool `/fd sha256 /tr` 签 setup+app+collector，失败即终止；无证书打印跳过并记 signed:false），再生成 `SHA256SUMS.txt` 与 `build-info.json`（版本/git sha/时间/每个文件哈希/signed）；`-NoSign` 可跳过 | 三个脚本 PowerShell Parser 通过；`sign.ps1` 无证书跳过路径实测 exit 0 + status JSON；签名 Valid 需证书复验 |
| 版本/文档 | package.json 0.1.1；新增 `docs/release-notes-v0.1.1.md`；README 增加系统要求、发布/签名/部署参数、卸载行为表、v0.1.1 A1–A14 汇总、配置路径更新；THIRD_PARTY_NOTICES 补 GPL 来源声明与随包许可说明 | 静态检查 |

### 8.2 并行 teammate 状态（最终）

- **collector**（A10/A11）：完成，pytest **117 passed**。
- **frontend**（A8/A9/A12/A14 前端）：完成，ui_smoke **21 passed**。
- **rust-window**（A4/A7/A14 窗口层）：完成（A14 按 Lead 实验结论改走备选 B：窗口 720×380、无安全边；弃用 SetWindowRgn）。
- **rust-lifecycle**（A5/A6/A12/A13/main.rs）：完成，已集成 `webview2_check`，A13 重试预算修复合入。
- **Lead**：运行验收 A14/A4/A5/A6/A12/A13/A2 通过（A7 最后复跑）；回归 **cargo test 55 passed**、collector 117、ui_smoke 21；运行验收清单由 Lead 写在 `review/FIX-VERIFICATION.md`。
- **发布**：最终 NSIS 重建后由 Lead 做默认路径安装/卸载验收；GitHub Release 待用户确认后执行。

### 8.3 packaging 未验证 / 风险（交 Lead 验收）

1. **NSIS 真实构建与默认路径安装/卸载**：hooks 只做了 makensis 静态语法编译；`RMDir /r "$INSTDIR"`、
   `/DELETEAPPDATA`、复选框变量 `$DeleteAppDataCheckboxState` 的实际行为需在安装包上复验（重点：默认路径
   `%LOCALAPPDATA%\PeripheralBattery` 安装→产生数据→卸载后 INSTDIR/HKCU/profile 无残留；`/DELETEAPPDATA`
   删 `%APPDATA%\PeripheralBattery`；不勾选时数据保留）。
2. **WebView2 embedBootstrapper**：最终构建需联网下载 bootstrapper（go.microsoft.com）；断网安装、
   旧版 WebView2 升级/提示、完全缺失时的中文 MessageBoxW 需干净环境复验。
3. **签名**：无证书，发布包默认 NotSigned；`signtool` 实际签名/时间戳/Valid 未验证（需 PFX 或证书指纹）；
   且为构建后签名，安装包内 app/collector 副本仍未签名。
4. **0.1.0 升级路径未实机验证**：publisher 保持 `peripheralbattery`、注册表键与安装目录不变（custom `/D=` 也沿用），
   数据迁移已有单测与 NSIS 复制兜底；但“真实 0.1.0 → 0.1.1 静默覆盖 / 交互式先卸载”两条路径需 Lead 在干净环境实测。
5. `paths.rs` 迁移在真实 `%LOCALAPPDATA%\PeripheralBattery`（含 1.6MB app.log）上的耗时/权限未实测；
   单测覆盖了逻辑与幂等性。

### 8.4 A14 设计变更（Lead 实机实验结论）

- Lead 实拍 4 组变体：**region+acrylic = 灰边**、no-region+acrylic = 磨砂玻璃、
  region+no-acrylic = 透桌面、no-region+no-acrylic = 透桌面。结论：`SetWindowRgn` + acrylic 会被
  DWM 降级成纯灰 `#545454`，评审报告 §10 的“方案 A（区域裁剪）”前提不成立。
- 改用报告 **备选 B**：窗口尺寸 = 面板尺寸 **720×380**，去掉 780×440 外框的 30px 安全边，
  acrylic 只铺满面板内部；前端相应调整 padding/定位/阴影（frontend/rust-window 负责）。
- packaging 配合：`tauri.conf.json` 的 `app.windows[0].width/height` 改 720/380，其余
  transparent/decorations/shadow/alwaysOnTop/windowEffects 不变；README / Release Notes 的
  A14 描述与验收口径同步更新。
- 灰边验收口径：三背景（亮/暗/彩色）下**面板外无灰色矩形**、可见外轮廓 = 面板 12px 圆角；
  透明效果 0/1、完全重启、远程/虚拟显示一致；125% DPI / 托盘定位不回归。


---

## 9. v0.1.2 用户反馈修复（2026-10-19 23:00 起）

用户反馈（截图 v0.1.1）：面板“点开后有时候会无法拖动”、设备下方「未知连接」、剩余使用时间「未知」。
Release Notes 见 [docs/release-notes-v0.1.2.md](docs/release-notes-v0.1.2.md)。

### 9.1 修复

| # | 症状 | 根因 | 修复 |
|---|---|---|---|
| B1 | 有时拖不动 | `data-tauri-drag-region="deep"` 只挂在 `.side-head`；hero 卡 / hero 名称 / 列表空白 / 底部提示均非拖拽区 | 拖拽区上移到 `#panel`；抽屉 scrim/抽屉显式 `="false"`；大涟漪改 pointerup + 位移 <6px（拖拽起手不再出涟漪），行/按钮小涟漪不变 |
| B2 | 「未知连接」 | LunaFury Luna33-W 走 2.4G 接收器（373E:0033），provider 未上报链路，`infer_transport()` 无 lunafury 规则 → unknown | `DeviceStatus.transport` 新字段；LunaFury 检测 0x0033 → `2.4g`、仅 0x0032 → `usb`；`infer_transport` 优先 provider 值，旧启发式兜底；xinput 仍 unknown |
| B3 | 剩余时间未知 | 只来自实测掉电外推（≥30min 且掉 ≥3%），冷启动 history 为空即一直 null | 新增 `collector/device_specs.py` 标称续航目录；无实测时按 标称续航×电量 回填，协议增量字段 `left_s_src`（history/spec/mains/""）；UI：spec → `≈ X 小时 Y 分`（title「按标称续航估算」）、mains → 「外接供电」、history 原样 |

标称目录：LunaFury Luna33-W 250mAh、厂商无官方续航 → 按 2.4G/1kHz ≈6mA 估 **40h**（注释标注 ESTIMATE，来源 voptronics 产品页）；
FlyDigi BS2PRO 为 USB-C 供电散热器（Scythe 产品页），`powered=True` → 「外接供电」，其 Windows BLE 94% 为静态值。协议仍为 1（增量字段）。

### 9.2 验证（Lead 复跑）

- collector `pytest` **133 passed**（基线 117 + 16）；`node tools/ui_smoke.js` **22 passed**；`cargo test --release` **55 passed**。
- 真实 Win32 鼠标拖拽 `tools/verify_drag.py`：修复前 **8/12**（hero 卡 / hero 名称 / 列表空白 / 唤出后 hero 卡 FAIL，复现用户 bug），修复后 **13/13 passed**（证据 `design/verify/drag-native{,-before}.json`）。
- 真实设备 probe：`lunafury:373e:0000:u1 "LunaFury Luna33-W" ... left_s=136800 left_s_src=spec ... transport=2.4g`；`FlyDigi BS2PRO level=94% left_s_src=mains transport=bluetooth`（`design/verify/transport-endurance.json/.log`）。
- 真机 UI 截图 `design/verify/v012-ui-real.png`：hero 93% + 「≈ 1 天 13 小时」+ 「2.4 GHz」；列表「蓝牙 · 外接供电」。安装版同一画面见 `design/verify/v012-ui-installed.png`。
- 安装/卸载验收（新脚本 `tools/verify_install_v012.ps1`，**24 项全 PASS**，日志 `design/verify/v012-install.log`）：
  /S 安装到自定义 `/D=` 临时目录 → 校验 exe 0.1.2 / collector 哈希 / licenses / 注册表 DisplayVersion → 安装版真实设备启动截图
  （隔离 APPDATA，真实用户数据 8 文件 0 变化）→ 退出无孤儿 → `/S` 卸载后安装目录、注册表、WebView2 profile 均清理 →
  profile 先备份后还原（卸载钩子会无条件删除 `%LOCALAPPDATA%\com.peripheralbattery.app`，已恢复 69.3MB 用户 profile）→ 用户数据目录保留。

### 9.3 产物

- 全量构建 `pwsh -File scripts/build.ps1 -NoSign`（日志 `design/verify/v012-build.log`）：PyInstaller collector + Tauri release + NSIS 全部成功。
- 安装包 `src-tauri/target/release/bundle/nsis/PeripheralBattery_0.1.2_x64-setup.exe`（约 10.65 MiB），
  哈希与构建信息见同目录 `SHA256SUMS.txt` / `build-info.json`（最终包在干净 commit 上重建后回填）。
- 代码/文档改动：collector（base/lunafury/state/device_specs/protocol/__main__）、protocol.md、UI（index/app/bridge/styles）、
  tools（ui_smoke/verify_drag/verify_v012_ui/verify_install_v012/collector_ndjson_check）、README §6.1/§7、版本号 0.1.2。

### 9.4 已知限制

- Luna33-W 的 40h 是估算（厂商无官方续航小时），满 30min / 掉 3% 后自动切实测；未收录设备冷启动仍「未知」。
- BS2PRO 显示「外接供电」；Xbox 手柄等 unknown 链路仍「未知连接」；dongle+cable 同时存在按 2.4g；updater 未接。
