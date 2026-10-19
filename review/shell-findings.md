# Tauri/Rust 外壳与 Windows 11 集成审查（task-1 / shell-reviewer）

> 审查对象：`src-tauri/src/*.rs`、`build.rs`、`capabilities/default.json`、`tauri.conf.json`
> 环境：Windows 11 **build 26300（25H2）**，单显示器 DISPLAY1 2560×1440 @125%（工作区 2560×1380 物理像素），
> cargo 1.99.1 / rustc 1.99.1 / .venv Python 3.12.9。代码基线 a24f2fc。
> 方法：静态审查 + cargo check/test/clippy + 用 release exe 副本（隔离 `LOCALAPPDATA`、独立
> `PB_SINGLE_INSTANCE_NAME`、`PB_COLLECTOR` 指向 .venv fake 采集器）做启动/隐藏/焦点/托盘/崩溃/配置实验。
> **限制**：本机只有 1 块活动显示器，100/150/200% DPI 运行中切换、多屏（负坐标/竖屏/混合 DPI）、Win10、
> 睡眠唤醒、锁屏/UAC 安全桌面、Explorer 重启、触摸屏、RDP 多用户等**无法在本机实测**，相关结论标注为"静态/未验证"。
> **并行干扰说明**：测试期间另有并行审查实例在跑（同一台机器、同一 %LOCALAPPDATA%）。
> 16:59:26 我的一次可见启动把另一实例的打包 collector（pid 25760）误杀，见 P1-3 证据；这本身就是本报告的一个缺陷，
> 也请 Lead 在汇总他人测试日志时注意这些 cleanup residual / collector stdout EOF 可能是被本缺陷误杀造成的。

---

## 0. 结论摘要

| 级别 | 数量 | 主题 |
|---|---|---|
| P0 | 0 | 本轮未复现崩溃/数据错乱；旧的可见启动主线程死锁（B1）已修复并复测 |
| P1 | 4 | 系统级最小化状态滞留、单实例跨会话失效、cleanup_residual 误杀、虚拟桌面不可见 |
| P2 | 14 | 托盘双击/首批定位竞态、collector 崩溃不自动恢复、电源事件缺失、Job 竞态、主线程阻塞写、build 号、Win10 回退、命中测试、全屏游戏、capabilities、配置降级、启动失败无提示、单实例 ShowWindow 兜底 |
| P3 | 12 | 日志/路径/重启串行化/图标尺寸/诊断性等 |

**旧审计"已修复"项复核**：可见启动死锁（B1）、隐藏态托盘陈旧（P1-2）、托盘"设置"隐藏态无效（P2-1）、
数据目录/采集器配置分离（P2-4）、`\\?` 路径归一化（P3-1）、最小化按钮改隐藏（P1-1 主路径）——**均验证仍成立**。
但"minimize 修复"只覆盖应用内按钮路径，**系统级最小化（Win+↓ / Win+D）仍会复发同类问题**（P1-1）。

---

## 1. P1（必须修）

### P1-1 系统级最小化（Win+↓ / Win+D / Show Desktop）后 window_visible 状态滞留：托盘第一次点击无效，隐藏四步与 WebView 内存回收不执行

- **文件**：`src-tauri/src/panel.rs:232-239`（`toggle_panel` 仅按 `state.window_visible()` 判断）、
  `panel.rs:306-344`（`attach_window_events`：只处理 CloseRequested/Focused/ScaleFactorChanged，
  没有最小化事件；Tauri/tao 也没有 Minimized 事件）、`src-tauri/src/state.rs:95-101`。
- **问题**：用户按 Win+↓ 或用 Win+D/Show Desktop 让系统最小化面板时，**不会**触发 `Focused(false)`
  （实测最小化后无 auto-hide / window hidden 日志），Rust 侧 `window_visible` 仍为 `true`。
  下一次点击托盘时 `toggle_panel` 判定"已可见"→ 走 `hide_panel`：窗口本来就是最小化/不可见，
  用户看到**第一次点击毫无反应**；第二次点击才真正 `show_panel`（含 unminimize）。
  同一时刻：隐藏四步、`ICoreWebView2Controller::SetIsVisible(false)`、300ms 后 TrySuspend **全部跳过**，
  隐藏态 CPU/内存指标在"被系统最小化"后不成立（旧 P1-1 的同类问题）。
- **触发场景**：面板打开时按 Win+↓；或按 Win+D / 点击任务栏最右侧"显示桌面"；Windows 11 任务视图/
  多桌面切换后最小化；pinned=true 时同样发生（pinned 只抑制失焦隐藏）。
- **证据**（%TEMP%\pb-shell-review\trayclick\app.log，见 §5.4）：
  ```
  # 最小化前：窗口 foreground，visible=True
  # ShowWindow(hwnd, SW_MINIMIZE) 后：
  after SW_MINIMIZE iconic=True visible=True
  # 之后模拟一次托盘左键（PostMessage tray_icon_app 6002/LBUTTONUP）：
  2026-10-19 17:06:24.404 [INFO] tray left click rect=(2164,1380 40x60) scale=1.25
  2026-10-19 17:06:24.404 [INFO] window hide reason=tray-click webview IsVisible(false) -> S_OK
  2026-10-19 17:06:24.409 [INFO] window hidden reason=tray-click
  # 期间没有任何 auto-hide / hidden reason=focus-lost 记录；第二次点击才 show：
  2026-10-19 17:06:25.327 [INFO] window show reason=tray-click ... window shown
  ```
- **修复方向**：
  1. `toggle_panel` 先查 `win.is_minimized()`（Tauri 有 API）→ true 时直接 `show_panel`，而不是 hide；
  2. 计算 `window_visible` 时以"Win32 IsWindowVisible + !IsIconic"为准（或周期/事件驱动同步）；
  3. 更彻底：用 `with_webview`/子类化 HWND 拦截 `WM_SIZE`/`WM_SYSCOMMAND(SC_MINIMIZE)`，或在
     `WindowEvent::Resized` 中 `win.is_minimized()` 判定并走完整隐藏四步+TrySuspend；
  4. 补一条 Win+↓ / Win+D 的自动化回归用例（本报告的做法可复用：PostMessage tray_icon_app 6002 模拟点击）。

### P1-2 单实例跨会话/跨用户失效：Global\ 命名对象 + CreateMutexW 失败即"放行"，导致第二实例运行或应用静默退到别人桌面

- **文件**：`src-tauri/src/single_instance.rs:23`（`DEFAULT_SINGLE_INSTANCE_NAME = r"Global\PeripheralBattery.single"`）、
  `single_instance.rs:69-99`（`CreateMutexW` 失败 → `AcquireResult::Disabled`）、
  `single_instance.rs:101-113`（`OpenEventW` + SetEvent）、`src-tauri/src/main.rs:44-71`。
- **问题**：单实例对象放在 **Global\ 命名空间**，而"激活已有实例"是 `SetEvent`（跨进程/跨会话无可视效果）。
  1. **快速用户切换 / RDP 多用户（同一台机器两个登录会话）**：用户 A 已运行应用；用户 B（管理员，
     能打开 A 的 mutex/event）启动应用 → `ERROR_ALREADY_EXISTS` → 走 `AlreadyRunning` →
     `signal_existing` 成功 → 触发的是 **A 会话里的窗口 show_panel**，B 的桌面**什么都不会出现**，
     而 B 的进程直接退出。B 认为"程序打不开/没反应"。
  2. 用户 B 若因对象 DACL 打不开 mutex（`ERROR_ACCESS_DENIED`）→ 代码返回 `Disabled` 并**继续启动**，
     于是**同一台机器出现两个实例、两个托盘图标、两个 collector**；HID 独占/重复读取冲突，
     且 P1-3 的 cleanup_residual 会互杀（见下）。
  3. **UAC 提权不一致**（常见的更现实场景）：先以管理员运行过一次（安装器/快捷方式"以管理员身份运行"），
     之后普通权限启动：普通进程打不开高完整性级别进程的 Global mutex → `Disabled` → **双实例**；
     反向（先普通后管理员）则第二实例静默"激活"了普通实例的窗口，管理员这侧没有 UI。
- **触发场景**：RDP/快速用户切换多用户、UAC 提权一次后再普通启动、以不同用户身份运行。
- **证据**：
  - 代码路径如上；README 亦承认"**单实例：Global\ 命名对象跨用户场景未测**"（README.md:165）。
  - 本地同会话单实例功能验证 **正常**（见 §5.2：第二实例 124ms 退出并激活已有面板），说明问题只在跨会话/权限不一致。
  - review/platform-findings.md 也把"单实例 Global 跨用户"列为仍在的缺口，可交叉验证。
- **修复方向**：命名对象改为 `Local`（每会话一个实例），或名字里拼 `ProcessIdToSessionId` + 用户 SID；
  跨会话"单实例"若确实需要，使用不同机制（例如每用户一个 mutex + 会话内激活），
  并在 `ERROR_ACCESS_DENIED` 时明确区分"已有实例但不可访问"，**至少不要静默降级为第二个实例**；
  激活已有实例失败时给出可见提示（托盘气泡/MessageBox），而不是只写日志。

### P1-3 cleanup_residual 会误杀"另一个活着的实例"的 collector（并行测试隔离失效；单实例被绕过时同样发生）

- **文件**：`src-tauri/src/sidecar.rs:829-865`（cleanup_residual：读共享 collector.pid.json 直接
  `kill_process(rec.pid)`，再按 resource_dir/collector/collector.exe 路径扫描所有同名进程并 kill）、
  `sidecar.rs:298-301`（每次 `start()` 都先执行）。
- **问题**：collector.pid.json 与按安装路径的扫描都是 **所有实例共享** 的；新实例启动时不会验证
  pid 记录里的 collector 是否属于**自己**，也不检查是否还有别的 app 实例活着。结果：任何第 2 个 app 实例
  （并行测试、PB_SINGLE_INSTANCE_NAME 隔离、单实例 Disabled 路径、跨会话可互开的场景）启动时，
  会把**另一个健康实例的 collector 杀掉**。被杀实例进入 collector-state state=exited，
  且**不会自动重启**（见 P2-3），托盘值冻结。
  README 明确建议"测试并行时用 PB_SINGLE_INSTANCE_NAME 隔离"，**该隔离被此逻辑破坏**。
- **触发场景**：
  - 并行 UI/运行测试（本轮真实发生：16:59:26 我的实例把另一并行实例的 collector pid 25760 杀掉）；
  - 单实例 Disabled 分支（P1-2）导致双实例；
  - 任何残留/更新场景下第二个 app 启动。
- **证据**（%TEMP%\pb-shell-review\mutual\...app.log，见 §5.3）：
  ```
  17:01:32.374 [INFO] collector-state state=ready pid=Some(5292) ...        # 实例 A 正常
  17:01:36.419 [INFO] sidecar start requested                              # 实例 B 启动
  17:01:36.420 [WARN] cleanup residual collector pid=5292 exe=...\install\collector\collector.exe
  17:01:36.423 [WARN] cleanup residual packaged collector pid=5292
  17:01:36.431 [INFO] collector stdout EOF
  17:01:36.433 [INFO] collector-state state=exited pid=Some(5292) code=Some(1)
  ```
  以及 16:59:26 并行实例的真实误杀：
  ```
  iter 4: ... [WARN] cleanup residual packaged collector pid=25760
  ```
- **修复方向**：
  1. pid 文件里增加**实例身份**（app pid + 启动随机 token + session id），只有与当前实例匹配或确认
     "该 pid 的父/属主实例已死"才 kill；最简单的可靠判据：`process_info(rec.pid).parent_pid == 当前 app pid`
     （或 Job Object 归属），而不是仅比对 exe 名称+创建时间；
  2. 按路径扫描只在**没有其他活实例**（自身 mutex 是唯一持有者）时执行，或干脆删掉该扫描，
     依赖 Job Object(KILL_ON_JOB_CLOSE) 已能保证级联退出；
  3. 并行测试隔离应改为"独立数据目录 + 独立安装副本"，并在文档中写清 PB_SINGLE_INSTANCE_NAME 不足以隔离。

### P1-4 虚拟桌面（Windows 11 多桌面）下托盘/第二实例激活看不到面板

- **文件**：`src-tauri/src/panel.rs:148-186`（`show_panel` 只 `show()+set_focus()`）、
  `src-tauri/src/main.rs:48-60`（单实例激活）、`src-tauri/tauri.conf.json`（没有 `visibleOnAllWorkspaces`）。
- **问题**：Windows 上窗口属于**创建时所在的虚拟桌面**；tao 0.37.1 明确
  `set_visible_on_all_workspaces` 对 Windows **Unsupported**（tao-0.37.1/src/window.rs:1222-1230 注释
  "iOS / Android / Windows: Unsupported"）。应用通常常驻在桌面 1；用户切到桌面 2 后点托盘（托盘图标是
  全局的）或再次启动应用 → `show_panel` 在桌面 1 弹出 → **当前桌面看不到任何东西**，用户认为程序坏了。
  这是 Win11 常见多桌面工作流的直接功能失效。
- **触发场景**：任务视图/多桌面（常见）；RDP 重连后桌面 session 变化；单实例二次启动。
- **证据**：代码无任何 IVirtualDesktopManager / MoveWindowToDesktop / IsWindowOnCurrentVirtualDesktop
  调用；tao 不支持；本地无多桌面自动化验证（静态结论，置信高）。
- **修复方向**：用 `IVirtualDesktopManager`（公开 COM）在显示前判断
  `IsWindowOnCurrentVirtualDesktop`，不在当前桌面时用 `MoveWindowToDesktop(hwnd, currentDesktopId)`
  把窗口挪到当前桌面（再 show），或退化为"隐藏旧窗口 + 在当前桌面重建窗口"；
  失败时给出托盘气泡提示。

---

## 2. P2（应该修）

### P2-1 托盘双击 = 显示后立刻隐藏（净效果"双击没反应"）

- **文件**：`src-tauri/src/tray.rs:269-298`（只匹配 `MouseButtonState::Up`；`TrayIconEvent::DoubleClick` 未处理）。
- **原因**：Windows 托盘双击的消息序列是 LBUTTONDOWN → LBUTTONUP → LBUTTONDBLCLK → LBUTTONUP；
  tray-icon 0.25.1 对 WM_LBUTTONUP 每次都发 `Click{Up}`（tray-icon-0.25.1/src/platform_impl/windows/mod.rs:44x），
  双击会产生 **两次 Up** → `toggle_panel` 连续两次 → 显示后立即隐藏。
- **证据**（§5.4，向 tray_icon_app 窗口 PostMessage 6002/LBUTTONUP 两次，间隔 60ms）：
  面板隐藏态下双击模拟：`21.389 show → 21.450 hide`，最终 visible=False。
- **修复方向**：记录上次 Up 时间戳，250ms 内第二次 Up 忽略；或实现 `TrayIconEvent::DoubleClick` 分支并
  在双击后短暂忽略 Click。

### P2-2 首次显示定位竞态：tray.rect() 在托盘图标注册后 ~90ms 内偶尔返回错误矩形，面板位置偏移 223px

- **文件**：`src-tauri/src/panel.rs:63-93`（`query_tray_rect`）、`panel.rs:98-145`（`position_panel`）。
- **现象**：10 次"启动即显示"里 1 次 `tray.rect() = (2058,1157 50x50)`（正确值 (2164,1380 40x60)），
  面板 y=589 而不是 812（偏高 223 物理像素，悬浮在屏幕中部而不是贴着托盘）。
  启动瞬间（tray 创建后 89ms）Shell 尚未完成图标布局，Shell_NotifyIconGetRect 返回了非法/临时的矩形，
  且它落在工作区内，clamp 无法纠正。
- **证据**（%TEMP%\pb-shell-review\visible\iter1/...app.log）：
  ```
  16:59:09.402 [DEBUG] tray.rect() = (2058,1157 50x50)
  16:59:09.402 [INFO] panel position origin=(1567,589) ... anchor=(2058,1157,50x50)
  # 其余 9 次均为：(2164,1380 40x60) → origin=(1567,812)
  ```
- **修复方向**：对 `tray.rect()` 结果做合法性校验（必须贴近某显示器工作区边缘、尺寸接近系统小图标尺寸），
  不可信时回退光标/延迟重试（如 show 后再 120ms 重新定位一次，或先显示再定位避免闪烁法）；
  启动路径优先用"最近一次真实托盘点击矩形"（当前只有点击后才有）。

### P2-3 collector 崩溃/被杀后无自动重启；托盘数值永久冻结

- **文件**：`src-tauri/src/sidecar.rs:632-707`（wait_loop/on_process_exit 只发布 exited）、
  无 supervisor/backoff；`src-tauri/src/tray_mirror.rs` 只在收到消息时更新。
- **证据**（§5.5）：kill collector 后 8s 内 `spawned pid=` 计数仍为 1，`collector-state state=exited`，
  `tray-update end` 计数不再增长 → 托盘一直显示崩溃前电量。面板隐藏时用户看不到错误，只能打开面板
  点"重启采集器"（ui/app.js 有 btn-restart）。
- **修复方向**：对非正常退出（code≠0/超时）做指数退避自动重启（如 1s/3s/10s，最多 5 次），
  同时把托盘/状态切为 offline；重启次数耗尽才显示错误并提供手动重启。

### P2-4 电源事件（WM_POWERBROADCAST / 挂起恢复）完全未接入

- **文件**：`src-tauri/src/` 全目录 grep 无 `WM_POWERBROADCAST`/`PBT_APM*`/电源设置监听；
  README 已承认："电源事件：suspend/resume 的 new_session 协议已备好，**未接 WM_POWERBROADCAST**"（README.md:163）。
- **影响**：睡眠/休眠恢复后：shell 不触发任何"恢复"动作（不重发 snapshot、不刷新托盘、不重新握手），
  采集器的 HID 句柄失效恢复全靠 collector 自己（BUILD_PLAN §8 要求"恢复后重开 HID、新 session、全量重发"）；
  外壳层至少应负责"恢复后 re-snapshot + 状态重同步 + DWM 圆角/效果在 DWM 重启后重设"。
- **修复方向**：用 RegisterPowerSettingNotification（GUID_CONSOLE_DISPLAY_STATE/GUID_MONITOR_POWER_ON）
  或消息窗口（CreateWindowEx message-only）+ WM_POWERBROADCAST 处理 PBT_APMRESUMEAUTOMATIC：
  调 `sidecar.request_snapshot()`、`state.emit_app_state`、`panel::apply_platform`、
  必要时重启 sidecar；同时给 collector 发 refresh。

### P2-5 Job Object：先 spawn 后 assign（子进程逃逸窗口）+ assign 失败回退只杀直接进程

- **文件**：`src-tauri/src/sidecar.rs:334-370`（spawn 后才 `AssignProcessToJobObject`）、
  `sidecar.rs:772-803`（terminate：job 为空时只 `kill_process(pid)`，不清理 PowerShell 进程树）。
- **问题**：collector 在 assign 前若已启动子进程（含 PowerShell/conhost 等），这些进程**不在 Job 内**，
  app 崩溃时不会被 KILL_ON_JOB_CLOSE 带走；assign 失败（Win11 嵌套 Job 限制/父 Job 限制/权限）时只记录 WARN
  并继续，超时退出只杀 collector 自身，仍留 PowerShell 孤儿。旧审计已提（P3-3），当前仍未修。
- **修复方向**：CREATE_SUSPENDED → 先 assign 再 ResumeThread（需要 thread handle，
  std::process::Command 需换成 CreateProcessW 或使用 windows crate）；
  assign 失败时用父进程遍历兜底杀整棵树。

### P2-6 主线程在 sidecar stdin 上的阻塞写：collector 卡死时可能冻结 UI（B1 同类风险的另一条路径）

- **文件**：`src-tauri/src/sidecar.rs:425-451`（send_line 持 stdin 锁 write_all+flush 阻塞）、
  `panel.rs:174`（show_panel 在主线程调 request_snapshot）、`tray.rs:302-307`（托盘"刷新"在主线程调
  sidecar.refresh()）、`commands.rs:31-35`（collector_send）。
- **问题**：Windows 匿名管道缓冲区约 64KB；若 collector 活着但不再读 stdin（HID 卡死/PowerShell 卡死/
  调试挂起），主线程在"显示面板→发 get snapshot"或"托盘刷新"时会阻塞在 WriteFile 上，窗口/托盘消息循环
  停摆 → 与旧 B1 相同的用户可见冻结，而且同样没有日志。
- **证据**：代码路径明确；本轮未能造出 64KB 积压（小命令会进缓冲区立即返回），属静态风险。旧审计 P3-7 已提示。
- **修复方向**：所有写 stdin 改由**专用写线程 + 有界 channel**；写用超时（WriteFile + Overlapped/超时）
  或检测 collector 无响应后丢弃；主线程永不直接写管道。

### P2-7 build 号：交付的 release exe 内嵌 build=unknown；build.rs 不随 git HEAD 变化重跑

- **文件**：`src-tauri/build.rs:4-15`（只声明 `cargo:rerun-if-env-changed=APP_BUILD`，**没有**
  `cargo:rerun-if-changed=../.git/HEAD`；git 失败静默 unknown）、`src-tauri/src/state.rs:192-202`。
- **证据**：所有 release exe 运行日志（可见循环 10 次、单实例、mutual 等）都是
  `app start version=0.1.0 build=unknown`；而当前 `git rev-parse --short HEAD` = a24f2fc 正常。
  我用 `APP_BUILD=probe cargo build` 强制重跑 build script 后，debug 产物即包含 a24f2fc（2 处），
  说明脚本逻辑本身可用，问题是交付二进制在 git 不可用/脚本缓存环境下构建且不会因 HEAD 更新而刷新。
- **影响**：诊断日志无法定位版本；"版本过期/更新后行为差异"无法追溯（平台审查 task-4 也提了同一项）。
- **修复方向**：`println!("cargo:rerun-if-changed=../.git/HEAD")`（+ refs）；构建脚本里 git 失败时打印
  `cargo:warning` 并写入 unknown:<时间>；发布脚本校验 APP_BUILD != unknown。

### P2-8 Win10 回退路径：set_effects(None) 结果被吞，日志无法证明 acrylic 已关闭；Win10 完全未实测

- **文件**：`src-tauri/src/panel.rs:51-58`；tauri-2.12.4/src/window/mod.rs:2173-2179 的 `set_effects`
  是 run_on_main_thread + `let _ = crate::vibrancy::set_window_effects(...)`，**忽略内部失败**，
  所以本项目的 `Ok(()) → "win10 fallback ... success"` 日志是**虚假成功**；clear_effects 在 win10
  上是否真的清掉创建期 `windowEffects: acrylic`（tauri.conf.json:27-30）没有证据。
- **场景**：Windows 10 21H2/22H2（build<22000）启动；Win10 上 config 里全局 acrylic 会先被应用再被清。
- **修复方向**：改用 window-vibrancy 直接调用并检查返回值，或在设置后读取
  DwmGetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)/重绘验证；Win10 增加实测矩阵（100/125/150% +
  任务栏/壁纸），确认方角/透明/性能。

### P2-9 透明窗口没有点击穿透/命中测试：CSS 透明区域（含圆角外）仍拦截鼠标

- **文件**：无 WM_NCHITTEST 处理；tao-0.37.1 的 WM_NCHITTEST 只在
  "无边框且 resizable" 时处理缩放边（event_loop.rs:2128+），本项目 `resizable:false` + `decorations:false`，
  其余交给 DefWindowProc；实测扩展样式 `0x00040118`（APPWINDOW|WINDOWEDGE|ACCEPTFILES|TOPMOST），
  **无 WS_EX_TRANSPARENT、无 WS_EX_LAYERED**（GetWindowLong 测量）。
- **影响**：面板圆角外/CSS 透明区域仍属于窗口矩形命中区，点击这些像素会聚焦面板并触发失焦隐藏逻辑，
  也会挡住后面应用的点击（用户感知"鼠标被透明区域吃掉"）。
- **修复方向**：若需要严格点击穿透，子类化 HWND 处理 WM_NCHITTEST：根据 CSS 圆角/透明区域返回
  HTTRANSPARENT（用前端把命中区域/圆角半径传给 Rust，或对固定 28px 圆角+边距做几何判断）。

### P2-10 全屏游戏/专注助手静默策略未实现（BUILD_PLAN §5.2 明确要求）

- **文件**：全项目无 `SHQueryUserNotificationState`/`QUERY_USER_NOTIFICATION_STATE` 调用。
- **影响**：在无边框全屏游戏/视频上，托盘点击或第二实例激活会让 alwaysOnTop 面板盖在游戏上（用户预期
  "全屏时不主动弹出"）；也影响低电量告警静默（告警是前端，但外壳没有给"是否全屏"的信息）。
- **修复方向**：SHQueryUserNotificationState 返回 QUNS_BUSY/QUNS_RUNNING_D3D_FULL_SCREEN 时，
  单实例/自动路径不主动 show（用户显式点击托盘仍可显示，或显示后自动隐藏更快）；结果随 app-state 下发给 UI。

### P2-11 capabilities 过宽（最小权限原则）

- **文件**：`src-tauri/capabilities/default.json:6-31`。UI 实际只用到：invoke 的 8 个自定义命令、
  `data-tauri-drag-region`（需要 `core:window:allow-start-dragging`）、事件监听。
  center / set-size / set-position / inner-size / outer-size / scale-factor / current-monitor /
  primary-monitor / available-monitors / set-always-on-top / set-focus / minimize / unminimize /
  show / hide / is-visible / is-focused / close / core:path:default **均未被前端使用**，
  却对主窗口全部开放；配合 `withGlobalTauri: true`（tauri.conf.json:10）放大了任何未来 XSS/注入的利用面。
- **修复方向**：按实际调用裁剪 capability；移除 core:path:default；如无必要关闭 withGlobalTauri
  改用 bundle 的 @tauri-apps/api（当前 UI 用 window.__TAURI__，改造成本低）。

### P2-12 数据目录不可写 / 配置损坏时静默降级、不修复、不提示

- **文件**：`src-tauri/src/config.rs:20-35`（解析失败→默认值，**不备份不重写**）、`config.rs:70-84`（保存失败仅 WARN）、
  `src-tauri/src/logging.rs:149-180`（日志目录不可写时静默回退 %TEMP%\PeripheralBattery\app.log）。
- **证据**：
  - 损坏 app.json（BOM/截断）每次启动都报 `app.json 解析失败...使用默认值`，文件保持损坏（§5.6）；
  - `LOCALAPPDATA` 指向"父路径是文件"的不可建目录时应用仍能启动并退出（code=0），
    日志落到 %TEMP%\PeripheralBattery\app.log——**用户无任何提示**，pinned 设置保存也会静默失败。
- **修复方向**：损坏配置备份为 app.json.bad-<时间> 并重建；日志回退/配置不可写时在 UI 显示一次性提示；
  数据目录写入能力在启动时探测并把结果放进 app-state。

### P2-13 启动期严重失败无用户可见提示（WebView2 缺失 / 托盘创建失败 → panic 静默退出）

- **文件**：`src-tauri/src/main.rs:179-180`（`.expect("error while building tauri application")`）、
  `main.rs:119`（`tray::create_tray(&handle)?`，失败则 setup 失败）。
- **影响**：#![windows_subsystem = "windows"] 无控制台；未安装 WebView2（精简版 Win10/Win11、某些 LTSC/服务器）
  或托盘创建失败时，用户看到"双击没反应"，只有 app.log 里一行 panic（而且没有 panic hook，日志可能都没有）。
- **修复方向**：启动时 GetAvailableCoreWebView2BrowserVersionString 预检，失败弹 MessageBoxW
  （"需要 WebView2 运行时" + 下载链接）；设置 std::panic::set_hook 把 panic 写进 app.log；
  托盘创建失败降级为"无托盘 + 显示窗口/提示"，而不是整个 setup 失败。

### P2-14 单实例 ShowWindow 兜底不同步状态（少数情况下会留下空白/冻结的悬浮窗）

- **文件**：`src-tauri/src/platform.rs:228-237`（activate_existing_window 直接 `ShowWindow(SW_SHOWNORMAL)`）、
  `main.rs:49-60`（仅在 event 不可用时走兜底）。
- **问题**：被隐藏的窗口处于 `IsVisible(false) + TrySuspend` 状态；直接 ShowWindow 会显示一个**空白/无帧**
  的顶层窗口，且不发送 window-visibility、不 Resume、不更新 window_visible（此后失焦也不会自动隐藏）。
- **触发**：第一实例 CreateEventW 失败（名字冲突/权限）后，第二实例激活只能走 ShowWindow 兜底。
- **修复方向**：兜底改为"发自定义 WM 消息/写一个 activate 标志文件 + 第一实例定时检查"，或至少
  兜底前先通过 SetWindowPos 置顶并让第一实例的 Focused(true) 处理里补一次 show_panel 语义。

---

## 3. P3（建议修 / 记录）

1. **日志轮转失败分支**（`logging.rs:88-101`）：重开失败时保留旧句柄继续写**已改名**的 app.log.N 且 size 归零，
   后续日志会写进 .1 文件、轮转节奏错乱；写错误被完全忽略（write_line 返回 ()），磁盘满时无感知。
2. **时间戳无时区**（`platform.rs:51-60`，GetLocalTime）：跨 DST/手动改时间会导致日志时间倒序/重复；
   诊断时易误判。建议加 UTC 偏移或在 app start 行记录时区。
3. **多实例共享回退日志**（`logging.rs:154`）：日志不可写时所有实例写同一个 %TEMP%\PeripheralBattery\app.log，
   并行测试会互相污染轮转（本轮并行审查就踩到）。
4. **fps 测试钩子进 release**（`fps.rs:15-97`）：PB_FPS_PROBE 会 `win.eval` 注入 JS（固定模板、数值来自 env，
   无注入风险），但发布版保留测试钩子不必要；grep 确认 release 含 PB_FPS_PROBE（无 PB_TEST_TRAY_ACTION，那项已正确排除）。
5. **restart 未串行化**（`sidecar.rs:411-421` + `commands.rs:38-42`）：连续 collector_restart 会并发
   shutdown/start，可能双 spawn 或互相 shutdown（start() 开头 shutdown(300ms,false) 会杀刚起的子进程）。
   建议 Mutex/状态机串行化并让 UI 命令返回"进行中"。
6. **stdout 线程无 generation 防护**（`sidecar.rs:466-491`）：重启后旧进程残留行仍会 emit('collector-message')，
   前端靠 session 兜底；应在 on_line 前校验 inner.generation == gen。
7. **硬编码开发者 Python 路径**（`sidecar.rs:919-927`）：C:\Users\28482\...\Python312\python.exe
   只在本机成立；打包资源缺失时回退到它或 PATH 的 python，可能启动错误的解释器。建议去掉硬编码，只用 PATH/明确错误。
8. **非 JSON 行仍转发前端**（`sidecar.rs:531`）：collector-message 在 JSON 校验前 emit，前端必须自己容错；
   建议只转发合法 JSON（或加 t:"raw" 信封）。
9. **托盘图标尺寸口径**（`tray.rs:128-140`）：渲染 32*scale（125%→40px）；TECH_PLAN §3.2 列的
   16/20/24/32/40 是按 100/125/150/200% 的目标尺寸，当前约为目标 2 倍，由 Explorer 下采样。
   多数情况下可接受，但 100% 下 32→16 的降采样可能发虚；建议按 SM_CXSMICON（per-DPI）取尺寸。
10. **tray_rect 缓存长期不清**（`state.rs:184-190` + `panel.rs:102`）：点击托盘后缓存的锚点永久保留，
    任务栏/DPI/显示器变化后可能用过时锚点（下一次点击会刷新）；建议带时间戳过期或 DPI 变化时清空。
11. **长路径/重定向**（`paths.rs:25-53`）：路径拼接未加 \\?\ 前缀，非 ASCII 已实测正常（本次测试路径含中文/空格），
    但超长用户名/重定向到深层路径可能触发 MAX_PATH；建议关键文件操作前用 verbatim 前缀兜底。
12. **启动期主线程 I/O**：`sidecar.start()` 内同步 cleanup_residual（枚举全进程 + 打开进程 + 可能 kill）与
    fs 操作在 setup 主线程执行；进程多/杀软扫描时增加首帧延迟（本轮未测到明显问题，记录）。

---

## 4. 旧审计修复复核（逐条实测/静态）

| 旧项 | 结论 | 证据 |
|---|---|---|
| B1 可见启动主线程死锁（旧 P0） | **已修复** | 10/10 可见启动 0 冻结、全部正常 app exit；tray-update begin/end 7/7 配对；无 ERROR/panic（§5.1）。根因改法（tray API 一律 run_on_main_thread、不持锁调用）在当前代码 tray.rs:217-267 成立 |
| P1-1 最小化不可恢复 | **主路径已修，系统级最小化仍复发** | window_action minimize → hide、show_panel 先 unminimize；但 Win+↓/Win+D 场景见新 P1-1 |
| P1-2 隐藏态托盘陈旧 | **已修复** | 隐藏 300ms 后 TrySuspend，日志中仍有 tray-state source=rust + tray-update end（§5.4/5.5） |
| P2-1 隐藏态"设置"无效 | **已修复** | tray.rs:312-318 先 show_panel 再 emit |
| P2-4 config.json RMW 竞态 | **已修复** | config.rs 独立 app.json，旧 config.json 只读迁移（单测通过） |
| P3-1 \\?\ 前缀导致残留清理失效 | **已修复（但清理逻辑本身过激，见 P1-3）** | normalize_path 单测 + 实际 packaged 路径匹配日志 |
| P3-3 Job assign 在 spawn 之后 | **未修** | sidecar.rs:353-370，见 P2-5 |
| P3-8 硬编码 Python 路径 | **未修** | sidecar.rs:919-927，见 P3-7 |
| P3-9 build 恒为 unknown | **未修** | 见 P2-7 |
| P3-20 权限过宽/测试钩子 | **部分**：PB_TEST_TRAY_ACTION 已仅在 debug 编译；capability 仍过宽 | release 无该字符串；见 P2-11 |

---

## 5. 实际运行的命令与结果

> 所有 app 运行都使用 release exe 的**临时副本**（%TEMP%\pb-shell-review\portable|install\），
> LOCALAPPDATA 指向隔离临时目录，PB_SINGLE_INSTANCE_NAME=review-shell-*，PB_COLLECTOR 指向
> .venv\Scripts\python.exe -m collector --stdio --fake --scenario static --speed 1。
> 产品源码/配置/发布产物未改动；仅 cargo test/clippy/build 按任务要求更新了 target/debug 构建缓存。

### 5.1 构建与静态检查

```
$ cargo test --all-targets
   Compiling peripheral-battery v0.1.0
running 10 tests
test sidecar::tests::normalize_strips_verbatim_prefix ... ok
test sidecar::tests::stat_summary_keeps_tail_fields_visible ... ok
test tray_mirror::tests::all_offline_reports_offline ... ok
test tray_mirror::tests::null_level_counts_as_infinity_and_can_be_selected ... ok
test tray_mirror::tests::does_not_emit_before_first_device ... ok
test tray_mirror::tests::picks_lowest_online_level ... ok
test tray_mirror::tests::tick_is_full_alignment_and_keeps_first_seen_order ... ok
test sidecar::tests::outgoing_rejects_newline_and_oversize ... ok
test config::tests::migrates_pinned_from_legacy_config_without_touching_it ... ok
test config::tests::pinned_roundtrip_and_default_when_missing ... ok
test result: ok. 10 passed; 0 failed

$ cargo clippy --all-targets
warning: this can be `std::io::Error::other(_)`   --> src\config.rs:75:26
warning: redundant guard                           --> src\sidecar.rs:97:22
Finished `dev` profile ... (2 warnings, 无错误)
```

- cargo check/test/clippy 均通过；无 unsafe 相关 lint 报错。
- 无自动化 UI 测试覆盖托盘点击/最小化/虚拟桌面（见 §6）。

### 5.2 单实例（同会话，功能验证 PASS）

```
$ PB_SINGLE_INSTANCE_NAME=review-shell-single ... powershell run-single.ps1
B exited=True code=0 elapsed_ms=124 A_alive_when_B_exited=True A_quit_hung=False
2026-10-19 17:00:37.201 [INFO] single-instance: mutex acquired name=review-shell-single pid=26580
2026-10-19 17:00:41.215 [INFO] single-instance: existing pid=Some(26580) ... signaled=true window_activated=false exit
2026-10-19 17:00:41.215 [INFO] single-instance: activation event received, showing panel
2026-10-19 17:00:41.233 [INFO] window show reason=single-instance webview IsVisible(true) -> S_OK
2026-10-19 17:00:41.233 [INFO] window shown reason=single-instance
2026-10-19 17:00:46.019 [INFO] window hidden reason=focus-lost     # 面板显示后失焦自动隐藏（预期）
2026-10-19 17:00:51.464 [INFO] app exit
```
→ 同会话单实例工作正常；跨会话/权限差异问题见 P1-2（本机无法实测，静态+README 缺口）。

### 5.3 并行实例 cleanup_residual 互杀（P1-3 证据）

```
A ready=True pid file=True  pid=5292
collector pids before B: 5292      # B 启动前，A 的 collector 活着
collector pids after  B: 6756      # B 杀掉 5292 后起自己的
A log:
17:01:32.374 [INFO] collector-state state=ready pid=Some(5292)
17:01:36.420 [WARN] cleanup residual collector pid=5292 exe=...\install\collector\collector.exe
17:01:36.423 [WARN] cleanup residual packaged collector pid=5292
17:01:36.425 [INFO] collector-state state=starting pid=Some(6756)
17:01:36.433 [INFO] sidecar exit(code=Some(1)) pid=5292 source=wait
17:01:36.433 [INFO] collector-state state=exited pid=Some(5292) code=Some(1)
A restart? spawn count=2 exited count=2 error count=0   # 没有第三次 spawn → 无自动重启
```

### 5.4 托盘点击 / 双击 / 系统最小化（P1-1、P2-1 证据）

用 PostMessage(tray_icon_app, 6002, 0, WM_LBUTTONUP) 模拟真实托盘左键（tray-icon 的 WM_USER_TRAYICON=6002）：

```
click1: 17:06:18.146 tray left click rect=(2164,1380 40x60) -> window hidden reason=tray-click
click2: 17:06:19.084 window show reason=tray-click ... window shown
# 双击模拟（隐藏态，两个 UP 间隔 60ms）:
21.389 window show reason=tray-click
21.450 window hide reason=tray-click
after double-click visible=False          # 净效果：双击不显示
# 最小化后单击:
after SW_MINIMIZE iconic=True visible=True
17:06:24.404 tray left click ...
17:06:24.404 window hide reason=tray-click   # 第一次点击在"已最小化"的窗口上执行 hide —— 用户无感知
17:06:25.327 window show reason=tray-click   # 第二次点击才真正显示
```

窗口属性（GetWindowLong/GetWindowRect/DwmGetWindowAttribute，PowerShell 未声明 DPI 感知故坐标被虚拟化）：

```
Tauri Window hwnd=14354654 visible=True iconic=False rect=1254,650,2034,1090(虚拟化 780x440)
  exstyle=0x00040118 topmost=True layered=False toolwindow=False
  dwm_corner_hr=0 pref=2   # DWMWCP_ROUND，DWM 圆角生效
# 独立进程 GetWindowRect 物理尺寸 = 975x550 = 780x440 DIP @1.25（与日志一致）
tray.rect() 首次偶发错误: (2058,1157 50x50) vs 正常 (2164,1380 40x60) → panel y=589 vs 812（P2-2）
```

### 5.5 collector 崩溃（P2-3 证据）

```
collector pid=24264 ready=True devices=5
Stop-Process -Id 24264 -Force 后 8s：
after kill: exited_state=1 spawns=1 tray_end_before=7 after=7
17:03:52.786 [INFO] sidecar exit(code=Some(-1)) pid=24264 source=wait
17:03:52.786 [INFO] collector-state state=exited pid=Some(24264) code=Some(-1)
# 无 spawn、无 tray 更新 → 托盘冻结在最后一次电量
```

### 5.6 焦点隐藏 / pinned / 配置 / 不可写目录 / 启动错误

```
焦点隐藏（notepad 抢前台）:
  2026-10-19 17:02:17.616 [INFO] auto-hide triggered foreground=Some(330166) ours=Some(3671934)
  2026-10-19 17:02:17.618 [INFO] window hidden reason=focus-lost
pinned=true（app.json）:
  app.json loaded pinned=true；notepad 抢前台后 focus_lost_hide=0（失焦隐藏被抑制，符合预期）
损坏 app.json（BOM/截断）:
  每次启动 [INFO] app config: app.json 解析失败(expected value at line 1 column 1)，使用默认值
  文件保持损坏，不备份、不重建
不可写数据目录（LOCALAPPDATA 的父级是文件）:
  process exited=True code=0；日志回退到 %TEMP%\PeripheralBattery\app.log（含本次运行），无用户提示
采集器路径不存在:
  [INFO] collector-state state=error ... message=Some("启动采集器失败: 系统找不到指定的路径。 (os error 3)")
  [INFO] window shown reason=ui-ready；进程正常退出（无崩溃）
collector 5s 未握手:
  [WARN] hello timeout 5s pid=10380
  [INFO] collector-state state=error ... message=Some("采集器 5 秒内未完成握手")
```

### 5.7 可见启动压测（B1 回归）

```
iter 1..10: hung=False exit=0 elapsed≈5.2-5.5s shown=1 app_exit=True tray 7/7 err=0 warn=0(iter4 warn=1: cleanup residual pid=25760)
合计：10/10 无冻结、无 ERROR、无 panic、正常退出
```

### 5.8 环境信息

```
$ powershell: [System.Windows.Forms.Screen]::AllScreens
\.\DISPLAY1 bounds={0,0,2048,1152} work={0,0,2048,1104} primary=True   # PowerShell 未感知 DPI，实际 2560x1440@125%
$ (Get-CimInstance Win32_VideoController).Name
NVIDIA GeForce RTX 5070 Ti Laptop GPU / OrayIddDriver Device / GameViewer Virtual Display Adapter
$ git rev-parse --short HEAD → a24f2fc
$ cargo --version → 1.99.1；rustc 1.99.1；.venv Python 3.12.9
```

---

## 6. 未能实测项与原因（残余风险）

| 项 | 原因 | 建议 |
|---|---|---|
| Win10（build<22000）实际回退 | 本机 Win11 26300 | 需在 Win10 21H2/22H2 实机跑：acrylic 关闭、方角、透明、性能（P2-8） |
| 100/150/200% DPI、运行中改缩放、混合 DPI 多屏 | 只有 1 块显示器，本轮只观察到 125% | 增加"显示设置改缩放"回归；检查 position_panel 用窗口 scale 与目标屏/托盘屏 scale 不一致时的 1~2 帧错位（静态风险） |
| 副屏负坐标/竖屏/工作区夹取/任务栏自动隐藏 | 无可移动副屏（Oray/GameViewer 虚拟显示器未激活） | 需要多显示器主机或 SetDisplayConfig 虚拟屏 |
| 睡眠/休眠恢复、显示器休眠唤醒 | 会中断整个宿主会话（并行审查 + 人工环境） | 用 powercfg /sleep 的专用机器；检查恢复后 re-snapshot/托盘/圆角（P2-4） |
| 锁屏/解锁、UAC 安全桌面 | 会锁死/中断宿主 | 专用机测试；关注解锁后面板/托盘是否恢复 |
| UAC 高/低完整性双实例 | 审批被禁用，无法启动提权实例 | 用两个用户会话/任务计划程序提权验证（P1-2） |
| Explorer 重启（托盘重建） | 重启 explorer.exe 会影响其他并行审查 | 静态已确认 tray-icon 0.25.1 处理 TaskbarCreated 重注册（platform_impl/windows/mod.rs:51-161），风险低；可专项验证图标状态/tooltip 是否恢复 |
| 虚拟桌面 | 无法脚本化切换桌面（无公开 API 自动化） | 用 IVirtualDesktopManager 手测：桌面 1 启动 → 切桌面 2 → 点托盘（P1-4） |
| 触摸屏/键盘菜单导航 | 无触摸硬件 | 手测托盘触摸、右键菜单键盘操作 |
| 磁盘满、只读安装目录、漫游/重定向 LOCALAPPDATA、超长路径、非 ASCII 用户名 | 非 ASCII 路径已验证可用（测试路径含中文/空格）；其余未构造 | 可用 icacls 拒绝写/小容量 VHD 验证；日志/配置降级行为见 P2-12 |

---

## 7. TOP 待修复清单（按优先级）

1. **P1-1** 系统级最小化（Win+↓/Win+D）后 window_visible 滞留 → 托盘第一次点击无效 + 隐藏四步/内存回收跳过。
   最小改法：toggle_panel 先判 win.is_minimized() 并直接 show；状态以 IsWindowVisible/IsIconic 校准。
2. **P1-3** cleanup_residual 误杀另一活实例的 collector（并破坏 README 的并行测试隔离）。
   最小改法：pid 文件绑定实例身份（app pid/token/session）+ 删除按安装路径的全盘扫描；优先依赖 Job Object。
3. **P1-2** 单实例 Global\ 跨会话失效：RDP/快速用户切换/UAC 提权不一致时"应用打不开"或"双实例"。
   改法：Local\ + 会话/用户维度命名，或明确跨会话策略并给出可见提示。
4. **P1-4** 虚拟桌面下托盘/第二实例激活看不到面板（tao Windows 不支持 all-workspaces）。
   改法：IVirtualDesktopManager 检测并 MoveWindowToDesktop。
5. **P2-3** collector 崩溃无自动重启、托盘永久冻结；**P2-6** 主线程阻塞写 stdin（B1 同类风险）。
6. **P2-4** 接入 WM_POWERBROADCAST/电源通知（恢复后 re-snapshot + 重握手）。
7. **P2-1/P2-2** 托盘双击净无效；首次显示 tray.rect() 竞态导致位置偏移（1/10）。
8. **P2-7** build 号 unknown + build.rs 不随 HEAD 重跑（与 task-4 交叉，建议统一在发布脚本强制校验）。
9. **P2-9/P2-10/P2-11/P2-12/P2-13/P2-14** 命中测试、全屏静默、capability 收敛、配置降级提示、
   启动失败提示、ShowWindow 兜底状态同步。
10. **P3** 日志轮转/timezone/restart 串行化/stdout generation/硬编码 Python/图标尺寸/长路径（见 §3）。

---

## 8. 附录：证据文件索引与脚本

- 本报告证据目录：%TEMP%\pb-shell-review\（隔离数据目录与日志，未写入仓库）
  - visible/iter{1..10}/PeripheralBattery/logs/app.log：可见启动压测（B1 回归、首批定位竞态 iter1）
  - single/LocalAppData/PeripheralBattery/logs/app.log：单实例激活
  - mutual/LocalAppData/PeripheralBattery/logs/app.log：并行实例互杀
  - trayclick/app.log：托盘点击/双击/最小化状态滞留
  - focus/app.log、pinned3/app.log：焦点隐藏、pinned
  - crash/app.log、corrupt/app.log、badcollector/...、hellotimeout/...：崩溃/配置/错误态
  - run-*.ps1：本轮使用的可复现脚本（ASCII PowerShell，可重跑）
- 复现建议（最小集）：
  1. powershell -File %TEMP%\pb-shell-review\run-visible-loop.ps1（10 次可见启动，检查 hung/app_exit/tray 配对）
  2. run-trayclick.ps1（托盘点击/双击/最小化）
  3. run-mutual.ps1（两个实例互杀）
  4. run-pinned-errors.ps1（pinned/坏采集器/hello 超时）
- 相关代码定位：single_instance.rs:23,69-99,101-113、main.rs:44-71,179-180、
  panel.rs:63-93,98-145,148-186,211-239,257-344、tray.rs:217-298,300-326、
  sidecar.rs:298-409,411-451,632-707,710-803,829-865,867-940、state.rs:95-101,184-190,252-287、
  config.rs:20-35,70-84、logging.rs:76-114,149-180、build.rs:4-15、
  tauri.conf.json:10,24-35,50-54、capabilities/default.json:6-31。
