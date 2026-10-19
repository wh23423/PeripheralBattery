# v0.1.1 修复验收记录（阶段 A：A1–A14）

> 对象：`wh23423/PeripheralBattery`，HEAD `5d096f5` + 本工作区未提交修复（v0.1.1）。
> 依据：[review/REVIEW-REPORT.md](REVIEW-REPORT.md) §3/§7 阶段 A + §10，以及 root `review_report.md`。
> 执行：Lead + 5 名 teammate（packaging / rust-window / rust-lifecycle / collector / frontend），2026-10-19 20:50–21:45（Asia/Shanghai）。
> 环境：Windows 11 build 26300 / x64 / 单显示器 2560×1440 @125% / WebView2 155.0.4283.62 / rustc 1.99.1 / Node 24.20.0 / Python 3.12.9（.venv）。
> 结论：**14 项 P1 全部落地**；自动化测试与可自动化的实机验收全部通过；签名、无 WebView2/断网、真实休眠、Win10/多屏/RDP、24h 长稳等**未验证项如实列在 §5**。

---

## 1. 最终验证汇总

| 验证 | 命令 | 结果 |
|---|---|---|
| 采集器单测 | `.venv\Scripts\python.exe -m pytest tests\collector -q` | **117 passed**（基线 103，+14 A10/A11 测试） |
| Rust 单测 | `cargo test -p peripheral-battery --all-targets` | **55 passed / 0 failed**（基线 10） |
| Rust 静态检查 | `cargo clippy -p peripheral-battery --all-targets` | **0 warning**（顺手修掉 pre-existing `config.rs io_other_error`） |
| 前端冒烟 | `node tools\ui_smoke.js` | **21 passed / 0 failed**（基线 15，+A4–A9 六项） |
| 运行验收（自动） | `.venv\Scripts\python.exe tools\verify_phase_a.py all` | **52/52 checks passed**（A14/A4/A12/A13/A5/A6/A2/A9/A8） |
| 虚拟桌面验收 | `.venv\Scripts\python.exe tools\probe_virtual_desktop.py` | **9/9 passed**（A7，见 §4.5） |
| 启动压测 | `pwsh -File scripts\stress-tray-startup.ps1 -Visible 20 -Hidden 5` | **visible 20/20 + hidden 5/5，0 hang**，exit 0 |
| 默认路径安装/卸载 | 手工步骤（§4.1，PowerShell） | 安装→迁移→卸载保数据→重装→`/DELETEAPPDATA` 全 PASS |
| 发布构建 | `pwsh -File scripts\build.ps1`（含 collector 重建） | exit 0，NSIS 10.64 MiB，无证书 → `signed=false` |
| Release 产物 | `src-tauri\target\release\bundle\nsis\` | `PeripheralBattery_0.1.1_x64-setup.exe` sha256 `7e1832b95aa0dbc17f292db5ae1b0bf2d86561d631e11a9614442a9e6f4feded` |

证据文件：`design/verify/phase-a-runtime.json`（52 项明细）、`a7-virtual-desktop.json`、`tray-stress-bplan.json`、`bt-watch.json`、`region-variants.json`、`region-before/after*.png`、`a7-desktop2-*.png`、`ui-prod-720x380*.png`、`a1-install-uninstall.json`（手工步骤的检查项见 §4.1）。

---

## 2. 各条修复与证据

### A1 安装目录/数据目录拆分 + 卸载清理（P1-01）
- 数据目录迁 `%APPDATA%\PeripheralBattery`（`src-tauri/src/paths.rs`；`PB_DATA_DIR` 可覆盖并回写环境变量供 collector 继承）；首启从旧 `%LOCALAPPDATA%\PeripheralBattery` 迁移 config/app/aliases/history/logs/diagnostics，只补缺失、写 `migration-v0.1.1.json`、失败不阻塞。
- 卸载钩子 `src-tauri/nsis/installer-hooks.nsh`：无条件删 HKCU 厂商/产品键与 v0.1.0 旧 vendor 键、删 WebView2 profile、守卫式 `RMDir /r $INSTDIR`；`$DeleteAppDataCheckboxState` 或 `/DELETEAPPDATA` 才删真实数据；`/UPDATE` 不清数据并把 0.1.0 配置文件复制到新数据目录。
- **实机证据（默认路径 `%LOCALAPPDATA%\PeripheralBattery`，本机真实 0.1.0 残留数据）**：
  - `/S` 安装 exit=0，程序文件落在默认路径，旧数据文件保留；卸载项 DisplayVersion=0.1.1、Publisher=peripheralbattery、InstallLocation=默认路径；
  - 首次启动 exit=0，迁移日志 `legacy=...\Local\PeripheralBattery -> ...\Roaming\PeripheralBattery copied=6 skipped_existing=0 errors=0 marker=written`，4 个 json + logs + diagnostics 全部到位；
  - `uninstall.exe /S` exit=0：INSTDIR 递归删除、HKCU 厂商键与卸载项删除、WebView2 profile 删除、`%APPDATA%\PeripheralBattery` 保留；
  - 重装后 `uninstall.exe /S /DELETEAPPDATA` exit=0：数据目录、INSTDIR、注册表全部清除（`/DELETEAPPDATA` 无值开关解析有效）。
  - 注意：安装时若注册表存在**陈旧的 MANUPRODUCTKEY InstallLocation**（历史上的 /D= 测试残留），NSIS 会在 .onInit 前用它决定 INSTDIR；preinstall 钩子只能删键、无法改变已决定的 INSTDIR。真实 0.1.0→0.1.1 升级路径的键值是旧默认路径，不受影响；已在 §5 记录该边界。

### A2 WebView2 交付与运行期预检（P1-02）
- `tauri.conf.json`：`embedBootstrapper` + `minimumWebview2Version=118.0.0.0`（重新生成的 installer.nsi 已确认 `INSTALLWEBVIEW2MODE "embedBootstrapper"`、`MINIMUMWEBVIEW2VERSION "118.0.0.0"`，setup 内嵌 1.87 MB bootstrapper）。
- 新增 `src-tauri/src/webview2_check.rs`：`GetAvailableCoreWebView2BrowserVersionString` 预检 + 版本比较 + MessageBoxW 中文可操作提示 + 退出码 2/3；main.rs 在 logging 之后、Builder 之前调用，`PB_SKIP_WEBVIEW2_CHECK=1` 可跳过。
- **实机证据**：运行验收 `webview` 用例 2/2 —— 启动日志记录 `webview2 runtime version=155.0.4283.62`，版本 ≥118 检查通过。缺失/过旧路径用 5 个单测覆盖（无法在装有 WebView2 的本机触达）。

### A3 发布安全 / 元数据 / 校验和 / build 号（P1-03、P2-03）
- `tauri.conf`：publisher=`peripheralbattery`（与 0.1.0 注册表键一致，避免升级时 MANUPRODUCTKEY 变化）、copyright、MIT + `licenseFile`、short/long description、homepage、category；NSIS English+SimpChinese + 许可页；`LICENSE`/`THIRD_PARTY_NOTICES.md` 安装到 `licenses\`。
- `build.rs`：监听 `.git/HEAD`、refs、packed-refs，支持 `PB_BUILD_SHA`/`PB_BUILD` 注入 → release 内 `build=5d096f5`（不再是 unknown；exe 元数据实测 FileVersion/ProductVersion 0.1.1）。
- `scripts/build.ps1`：构建后调用 `scripts/sign.ps1`（证书环境变量存在才签，失败即中止），生成 `SHA256SUMS.txt` + `build-info.json`。本次无证书 → `signed=false / sign_reason=no-certificate-configured`（**发布包 NotSigned，属已知限制**）。
- 最终产物 sha256：app `4fa9142d…b37657`、collector `3fda3333…c49b9004`（与 dist 一致，确认 A10/A11 进包）、setup `7e1832b9…f4feded`。

### A4 系统级最小化状态同步（P1-04）
- `toggle_panel` 用 `IsWindowVisible && !IsIconic` 校准；`Resized/Moved/Focused` 检测系统最小化后走完整隐藏四步；`show_panel` 重排为 Resume→VD→show→**无条件 unminimize + set_size(720×380 DIP)**→定位→IsVisible→app-state。
- **实机证据**：`minimize` 用例 9/9 —— `SW_MINIMIZE` 后 `window system-minimize detected` + `webview IsVisible(false)` + `window hidden`，`IsWindowVisible=false`；第二实例激活一次即显示，`IsWindowVisible=true / IsIconic=false`，恢复后尺寸 **900×475 @125%（720×380 DIP）**，不再是 199×34 占位（修复过程中发现并修复了该回归）。

### A5 单实例会话隔离（P1-05）
- 默认对象名 `Local\PeripheralBattery.single`（会话命名空间隔离；保留 `PB_SINGLE_INSTANCE_NAME` 覆盖）；`ERROR_ACCESS_DENIED` 且枚举到本程序窗口时 MessageBoxW 中文提示后退出，不再静默双实例；日志含 session/user/elevated。
- **实机证据**：`single` 用例 4/4 —— 同会话第二实例 <5s 退出（exit 0）、激活已有面板、只剩 1 个 app 进程；不同实例名+独立数据目录可并行运行（测试隔离前提）。跨会话/RDP/UAC 混用无法在本机自动构造（见 §5）。

### A6 cleanup_residual 归属判定（P1-06）
- `PidRecord` 增加 app_pid/instance/data_dir/exe；`classify_residual` 纯函数：data_dir 一致 + exe 属本 collector + 归属 app 已退出 + creation 匹配才 kill；另一活实例持有 → LeaveAlone；不匹配 → 只删文件。删除全盘按安装路径 kill（降级为 debug 诊断）。
- **实机证据**：`residual` 用例 4/4 —— 残留记录指向无关进程（cmd.exe）时不误杀、仅删 pid 文件且日志 `reason=exe mismatch (foreign install)`；另一活实例持有的 collector 不被杀且日志 `cleanup residual: skip kill`。另有 11 个归属判定单测。

### A7 虚拟桌面（P1-07，尽力而为）
- 实测发现 `IsWindowOnCurrentVirtualDesktop` 在后台进程是**进程相对语义**（切桌面后仍 true），不能作为早退依据。改为：`GetWindowDesktopId(前台窗口)` 作为“当前桌面”启发式 → 与本窗口桌面比较 → 不同则 `MoveWindowToDesktop`；取不到/失败只 WARN 不阻塞；全 0 GUID 视为未知；`PB_DISABLE_VD=1` 逃生口。
- **实机证据（Win+Ctrl+D 创建临时桌面，测后 Win+Ctrl+F4 关闭并恢复桌面数）**：`probe_virtual_desktop.py` 9/9 —— 普通窗口（Chrome）在桌面 2 上 `DWMWA_CLOAKED=2`（阳性对照，证明确实切走了）；**面板窗口 cloaked=0 且激活前后均为 0** → 面板在桌面 2 可见；app 日志 `result=moved-to-current desktop_id=<桌面2 GUID> ours=unknown`（该 Tauri 窗口的 GetWindowDesktopId 返回 null/未知，MoveWindowToDesktop 为 best-effort）。
- 说明：本机该窗口注册在“所有桌面可见”（null desktop id），所以 P1-07 症状无法在本机复现；对窗口被分配到某桌面的环境，前台启发式会执行迁移。

### A8 拖拽区 deep（P1-08）
- `.side-head` 改 `data-tauri-drag-region="deep"`（Tauri 2.12.4 drag.js 支持，自动排除 button/input 等）。
- **实机证据**：`native` 用例 —— 在标题区用真实鼠标拖动 100px，窗口从 (1642,887) 移到 (1552,887)，**拖动成功**；ui_smoke A4 断言标题/副标题/空白 header 均在拖拽子树、按钮不在。

### A9 右键菜单 / 浏览器加速键（P1-09）
- 双层修复：① WebView2 层（根治）`SetAreDefaultContextMenusEnabled(false)` + `ICoreWebView2Settings3::SetAreBrowserAcceleratorKeysEnabled(false)`（`PB_DISABLE_WEBVIEW2_HARDENING=1` 逃生口，老运行时失败只 WARN）；② 前端 document 捕获阶段拦截 contextmenu 与 F5/Ctrl+R/Ctrl+Shift+R/Ctrl+P/Ctrl+F/Ctrl+S/Ctrl+U（不影响编辑键）。
- **实机证据**：`native` 用例 4/4 —— F5 后无第二次 `command ui_ready`（不重载）、窗口位置不变；右键无 `#32768` 浏览器菜单；日志 `webview2 hardening: context_menus=disabled; accelerator_keys=disabled`；ui_smoke A5/A6 覆盖模拟事件与真实按键。

### A10 休眠恢复 gone 风暴（P1-10）
- `StateStore.on_tick` 记录 monotonic 时钟，相邻 tick 间隔 > max(5s, 3×tick 周期) 视为休眠恢复：跳过该轮 `_expire`、把全部 `_last_seen` 拨到 now、不发 gone/device，日志 `clock jump detected resume_s=..., suppressing expire`；stats 增加 `clock_jumps`。
- **证据**：+4 注入时钟单测（正常 2 分钟无 gone；+3600s 首次 tick 无 gone/device 且设备保留；恢复后 gone 判定正常；连续 tick 不误触发）；pytest 117 passed。真实合盖睡眠本机未执行（§5）。

### A11 蓝牙 watcher 按最早 due 调度（P1-11）
- `btconfig` 不再替换固定 sleep，而是整段替换循环：睡眠 = min(连接检查节拍、`$queue[0]`、`$nextFull`) − now（下限 100ms），连接检查只在自身节拍做 WinRT 查询；连接变化 +0/+3/+8/+15、冷启动 +3/+8/+15/+30、60s 全量快照全部保留；上游文本变化时安全降级并 WARNING。
- **证据（本机真实 PowerShell 执行生成脚本，非 mock）**：`tools/verify_bt_watch.py --interval 2000 --duration 42` 实测快照偏移 **0 / +4.1 / +9.0 / +16.1 / +31.0s**（对应 +3/+8/+15/+30 补采，±1.2s 为 PowerShell 冷启动开销）→ PASS；另有 10 个单测（含用 stub 虚拟时钟真实执行 PowerShell 的调度测试）覆盖连接变化 0/3/8/15 与 30s 节拍。

### A12 托盘 pin 在 Rust 侧生效（P1-12）
- 菜单改 `CheckMenuItem`；托盘“固定面板”由 Rust 直接 `set_pinned(!pinned)` + 同步勾选 + emit app-state，不再依赖暂停的 WebView；`commands::set_pinned` 也同步勾选；前端 hasTauri 下不再自行 toggle。
- **实机证据**：`pin` 用例 12/12 —— 隐藏态（WebView 已 TrySuspend）注入托盘动作后立即 `app.json saved pinned=true`、app.json 持久化 true、只发生一次；随后显示面板 pin 仍为 true（无迟到 toggle）。

### A13 collector 崩溃自动重启（P1-13）
- 非 shutdown 退出 → 立即发布 offline 托盘 + `collector-state` 增加 restart 信息；按**存活时长**决定预算：uptime≥60s 视为健康重置预算，否则 crash-loop 逐次 +1，退避 1s/3s/10s，最多 5 次（`PB_COLLECTOR_RESTART_MAX` 覆盖，0 禁用），用尽发布 error 保留手动重启；hello 不再无条件清零预算；spawn_lock + token + generation 防双 spawn；旧 stdout 残留行按 generation 丢弃。
- **实机证据**：`restart` 用例 7/7 —— 崩溃后 `tray-state source=rust online=false` 立即出现；日志 `auto-restart attempt=1/4 delay_ms=1000`、`attempt=2/4 delay_ms=3000`、`attempt=3/4 delay_ms=10000`；第 4 次 spawn 恢复 ready（计数=4），无 `budget reset by hello`；单测 6 项覆盖 crash-loop/健康重置/shutdown/手动重启/max=0。

### A14 灰色边框（§10，方案变更为备选 B）
- **实测否定了报告首选方案 A**：4 组变体实拍（`design/verify/region-variants.json` + 截图）：
  - region+acrylic → 安全边为纯灰 `#545454`（DWM 把 acrylic 降级为纯色）；
  - no-region+acrylic → 真磨砂玻璃（透出并使背后内容模糊）；
  - region+no-acrylic、no-region+no-acrylic → 透桌面。
  结论：`SetWindowRgn` 与 acrylic 不可兼得，采用 review §10 的 **备选 B**。
- 最终实现：窗口尺寸 = 面板尺寸 **720×380 DIP**（去掉 780×440 的 30px 安全边），`body padding: 0`、`.stage/.panel` 填满窗口、面板圆角 **12px**（贴近 Win11 DWM ~8px，避免圆角外露 acrylic 角）；不再调用 `SetWindowRgn`；acrylic 只覆盖面板内部。
- acrylic 降级：Win10 / `HKCU EnableTransparency=0` / 远程会话 / `PB_DISABLE_ACRYLIC=1` → `set_effects(None)` + `app-state.acrylic=false` → 前端 `html[data-acrylic="off"]` 用不透明烟熏底（#0a1830，不新增 `backdrop-filter`）。
- **实机证据**：`region` 用例 16/16 —— 窗口物理尺寸 900×475=720×380@125%、`GetWindowRgn`=None（未使用区域裁剪）、`DWMWA_SYSTEMBACKDROP_TYPE=3`（真 acrylic）、面板内部绘制 98.5%、**窗口外无均匀灰带**（只观察到 DWM 柔和阴影，随距离衰减；band 采样 gray_like=0）、可见面板贴托盘上方且按工作区 clamp；`PB_DISABLE_ACRYLIC=1` 降级路径 backdrop 关闭、尺寸不变、日志 `acrylic disabled: PB_DISABLE_ACRYLIC=1`；截图 `design/verify/region-after.png`、`region-off-crop.png`。
- 文档同步：README/Release Notes/PROGRESS/BUILD_PLAN/HANDOFF 均改为“窗口=面板 720×380、12px 圆角、无安全边”，并记录方案 A 被实测否定的原因。

---

## 3. 代码改动清单（本工作区未提交）

| 模块 | 文件 |
|---|---|
| 窗口层（A14/A4/A7/A9） | `src-tauri/src/platform.rs`、`panel.rs`、`state.rs` |
| 生命周期（A5/A6/A12/A13） | `src-tauri/src/single_instance.rs`、`sidecar.rs`、`tray.rs`、`tray_mirror.rs`、`main.rs`、`commands.rs` |
| 打包/发布（A1/A2/A3） | `src-tauri/src/paths.rs`、`webview2_check.rs`、`build.rs`、`src-tauri/tauri.conf.json`、`src-tauri/nsis/installer-hooks.nsh`、`scripts/build.ps1`、`scripts/sign.ps1`、`scripts/verify-uninstall.ps1` |
| 采集器（A10/A11） | `collector/state.py`、`btconfig.py`、`collector.py`、`tests/collector/*` |
| 前端（A8/A9/A12/A14） | `ui/index.html`、`ui/app.js`、`ui/styles.css`、`tools/ui_smoke.js` |
| 版本/文档 | `Cargo.toml`、`Cargo.lock`、`package.json`、`README.md`、`PROGRESS.md`、`BUILD_PLAN.md`、`HANDOFF.md`、`THIRD_PARTY_NOTICES.md`、`docs/release-notes-v0.1.1.md`、`src-tauri/src/config.rs`（clippy 1 行） |
| 验收脚本/夹具（本轮新增） | `tools/verify_phase_a.py`、`tools/mock-crash-collector.py`、`tools/probe_virtual_desktop.py`、`tools/verify_bt_watch.py`、`tools/probe_region_variants.py`、`tools/verify_install_uninstall.ps1`、`scripts/stress-tray-startup.ps1`（数据目录隔离 + APPDATA 日志） |

> 说明：工作区同时含有本轮之前未提交的 LunaFury provider 与 UI 动效润色改动（与本修复无关，被原样保留）；因此 `git_dirty=true` 属预期。

---

## 4. 验收细节补充

### 4.1 A1 默认路径安装/卸载（手工步骤，PowerShell）
1. 备份真实 `%LOCALAPPDATA%\PeripheralBattery`（1.7MB 0.1.0 数据）+ HKCU 键；记录无安装、无 app 进程。
2. 清理历史残留 `HKCU\Software\peripheralbattery`（含 /D= 测试遗留 InstallLocation）→ 模拟干净机器/正确卸载后的状态。
3. `PeripheralBattery_0.1.1_x64-setup.exe /S` exit=0；INSTDIR=默认路径，含 app/collector/licenses，旧数据文件原样保留；卸载项版本 0.1.1、Publisher=peripheralbattery。
4. 启动已安装 app（mock collector、显示、9s 自动退出）exit=0；迁移 `copied=6 ... marker=written`，新数据目录含 4 json + logs + diagnostics。
5. `uninstall.exe /S` exit=0：INSTDIR（递归）、卸载项、MANUPRODUCTKEY、WebView2 profile 删除；用户数据保留。
6. 重装 + `uninstall.exe /S /DELETEAPPDATA` exit=0：用户数据目录、INSTDIR、注册表全部删除。
7. 恢复：从备份还原 LOCALAPPDATA 数据 + HKCU 键，清理测试 APPDATA 数据；无 app 进程。

### 4.2 启动压测
`scripts/stress-tray-startup.ps1 -Visible 20 -Hidden 5`：可见启动 20/20、隐藏启动 5/5 全部自行退出（exit 0，日志有 app exit，tray begin/end 配对），**0 hang**；脚本已改为使用独立临时 LOCALAPPDATA/APPDATA，不触碰用户数据。

### 4.3 灰框变体实验（A14 决策依据）
| 变体 | 窗口边缘表现 | 结论 |
|---|---|---|
| region + acrylic | 整圈纯灰 `#545454`（不随桌面变化） | 方案 A 会复现原 bug |
| no-region + acrylic | 磨砂玻璃，随桌面变化 | acrylic 有效 |
| region + no-acrylic | 桌面透出，无灰 | 区域裁剪本身正常 |
| no-region + no-acrylic | 桌面透出 | — |

### 4.4 运行验收脚本说明
`tools/verify_phase_a.py` 每个用例使用独立临时 `LOCALAPPDATA/APPDATA` + 唯一单实例名 + mock collector，不读写真实用户数据；`region` 用例含真实截屏像素对比（基线 vs 显示后）与 DWM 属性查询；结果写 `design/verify/phase-a-runtime.json`。

### 4.5 A7 检查方法
用 `DWMWA_CLOAKED`（DWM cloak）判断“窗口此刻是否在当前虚拟桌面可见”，不依赖调用进程所在桌面：阳性对照 Chrome 窗口在桌面 2 上 cloaked=2；本 app 面板 cloaked=0（两种桌面均可见）。同时用 `GetWindowDesktopId(前台窗口)` 验证前台启发式取到桌面 2 GUID，app 日志记录 `result=moved-to-current`。

---

## 5. 未验证 / 残余风险（发布前需知）

1. **代码签名**：无证书，setup/app/collector 为 NotSigned（SmartScreen 可能提示未知发布者）；签名脚本路径已实现但未用真实证书验证；且为构建后签名，安装包内 app/collector 副本仍是未签名。需要 `PB_SIGN_*` 证书环境后重跑 `scripts/build.ps1`，或接入 `bundle.windows.signCommand` 做整链签名。
2. **WebView2 边界场景**：断网安装、旧版本运行时被升级、完全缺失时的 MessageBoxW 中文提示——本机装有 WebView2 155 无法触达，仅有代码与 5 个单测覆盖；需在干净环境/断网虚拟机复验。
3. **0.1.0 → 0.1.1 升级路径**：数据迁移与卸载清理已用“模拟 0.1.0 残留数据”实测；真实旧安装包覆盖安装/先卸载两条路径未在干净环境完整跑。另：若注册表残留了异常的 MANUPRODUCTKEY InstallLocation（如历史 /D= 测试路径），NSIS 会在钩子前用它决定 INSTDIR，preinstall 钩子无法纠正；正常 0.1.0 用户的键值即默认路径，不受影响。
4. **A7 虚拟桌面**：仅 Win11 25H2 本机验证；本机该 Tauri 窗口为“所有桌面可见”（null desktop id），P1-07 症状无法复现；对窗口被分配到单桌面的环境，采用前台窗口桌面启发式，托盘点击时若前台是任务栏/桌面窗口会 `skipped`（不阻塞显示）。RDP 重连/会话变化未验证。
5. **A5 跨会话/权限**：RDP 双会话、快速用户切换、UAC 提权/非提权混用无法在本机自动构造；仅同会话单实例与权限冲突代码路径单测/日志覆盖。
6. **A10 真实睡眠**：用注入时钟验证；真实合盖/Modern Standby 唤醒未跑（需人工睡眠 1 分钟以上观察 `clock jump detected`）。
7. **平台矩阵**：Win10（acrylic 回退、DWM 圆角缺失）、100/150/200% DPI、混合 DPI 多屏/负坐标/竖屏、任务栏左/上/自动隐藏、RDP/虚拟显示驱动下的 acrylic 降级外观：均未实机验证。本机 125% 单屏通过。
8. **24h 长稳**：P7 内存增长、P9 key 抖动、句柄趋势未跑；A13 自动重启在真实 collector（非 fixture）上的恢复时序未验证（fixture 覆盖）。
9. **蓝牙真实连接/断开补采**：用真实 PowerShell 验证了冷启动 +3/+8/+15/+30 时间戳；真实蓝牙设备连接/断开后的 +0/+3/+8/+15 补采需人工操作设备验证（单测覆盖调度模型）。
10. **发布动作**：GitHub Release/NSIS 上传/推送未执行（本机网络对 github.com 阻断，且需用户确认）；`git_dirty=true` 的构建号来自当前未提交工作区。
11. **WebView2 profile 被卸载删除**：本次安装/卸载测试删除了原 71MB profile（纯缓存，应用下次启动会重建）；这是 A1 修复的预期行为。

---

## 6. 复现命令

```powershell
# 全部自动化测试
.venv\Scripts\python.exe -m pytest tests\collector -q
cd src-tauri; cargo test -p peripheral-battery --all-targets; cargo clippy -p peripheral-battery --all-targets
cd ..; node tools\ui_smoke.js

# 运行验收（需先 cargo build 出 debug exe）
.venv\Scripts\python.exe tools\verify_phase_a.py all
.venv\Scripts\python.exe tools\probe_virtual_desktop.py      # 会短暂创建/关闭临时虚拟桌面
.venv\Scripts\python.exe tools\verify_bt_watch.py --interval 2000 --duration 42
"<pwsh7>" -NoProfile -File scripts\stress-tray-startup.ps1 -Visible 20 -Hidden 5

# 发布构建
"<pwsh7>" -NoProfile -File scripts\build.ps1
```
