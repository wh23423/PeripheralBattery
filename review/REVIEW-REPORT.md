# 外设电量显示器 — 独立审查报告与待修复清单

> 审查对象：**D:\desktop\电量显示插件**（HEAD a24f2fc，v0.1.0，Tauri 2 + Python collector + WebView2 UI）
> 审查时间：2026-10-19 16:53–17:22（Asia/Shanghai）
> 审查环境：Windows 11 build 26300（25H2）/ x64 / 单显示器 2560×1440 @125%（工作区 2560×1380）/ WebView2 155.0.4283.62 / Python 3.12.9 / rustc & cargo 1.99.1 / node 24.20.0
> 判定口径：**P0** 崩溃、数据错乱、完全不可用；**P1** 明显功能受损或主路径可用性/数据可信度问题；**P2** 边缘场景、体验、健壮性、可维护性；**P3** 改进建议。

---

## 1. 结论摘要

- **P0：0 项**。未发现崩溃、数据错乱、完全无法使用级缺陷；旧审计的可见启动死锁（B1）经 10/10 次压测确认已修复。
- **P1：13 项**（发布前建议全部修复）。集中在六类：
  1. **安装/卸载**：默认安装目录与应用数据目录是同一个目录，卸载残留、删除数据删错目标；
  2. **单实例/多用户**：Global 命名对象在 RDP、快速用户切换、UAC 提权不一致时失效或静默双实例；
  3. **Win11 系统交互**：系统级最小化（Win+↓/Win+D）状态滞留、虚拟桌面看不到面板、透明安全边吞点击；
  4. **隐藏态事件**：托盘“固定面板”在 WebView 暂停时事件丢失/迟到触发；采集器崩溃后无自动恢复、托盘冻结旧电量；
  5. **休眠恢复**：Modern Standby 唤醒后所有设备被误判 gone 再整批重加；
  6. **前端两个硬伤**：拖拽区实际只有 6px 缝隙可用；生产面板弹出 WebView2 右键菜单且 F5 会整页重载并重置窗口位置。
- **P2：约 50 项**（按模块归并，见 §5），覆盖 Win11 高对比度/文本缩放/透明效果、多屏与混合 DPI、电源事件、全屏游戏、无障碍、采集器数据边界与持久化、安装更新链路等。
- **P3：20+ 项**（日志、诊断性、性能细节等，见各专项报告）。
- **正向结论**：核心数据链路（stdio 协议、快照门控、稳定 key、Job Object 清树、打包 collector）、UI 渲染（keyed diff、XSS、CSP、隐藏暂停、125% 渲染）、同会话单实例、中文/空格/非系统盘路径、中国区 Win11 125% 主路径均通过实测。

### 评审团队与专项报告

| 专项 | 报告 | 结论 |
|---|---|---|
| Tauri/Rust 外壳与 Win11 集成 | [review/shell-findings.md](shell-findings.md) | P0 0 / P1 4 / P2 14 / P3 12 |
| Python 采集器与设备兼容性 | [review/collector-findings.md](collector-findings.md) | P0 0 / P1 2 / P2 18 |
| WebView2 前端 UI 与可访问性 | [review/ui-findings.md](ui-findings.md) | P0 0 / P1 2 / P2 14 / P3 12 |
| 打包安装、更新与部署矩阵 | [review/platform-findings.md](platform-findings.md) | P0 0 / P1 3 / P2 10 |
| Lead 集成/运行时复测 | 本报告 §2、§4 | 新增 P1 2、性能复测 2 |

> 去重说明：不同专项交叉发现的重复项（Global 单实例、cleanup 互杀、Job 竞态、build=unknown、点击穿透、capability 过宽等）已合并为一条，以本报告编号为准。

---

## 2. 实际运行过的验证（摘要）

| 验证 | 命令/方法 | 结果 |
|---|---|---|
| 采集器单测 | .venv\Scripts\python.exe -m pytest tests\collector -q | **94 passed** in 7.97s |
| Rust 单测 | cargo test --all-targets | **10 passed**，0 failed |
| Rust 静态检查 | cargo clippy --all-targets | 0 error，**2 warning**（io::Error::other、redundant guard） |
| 前端冒烟 | node tools\ui_smoke.js | **15 passed, 0 failed** |
| 可见启动压测 | release exe 临时副本 ×10 | **10/10** 正常退出，无 hang/panic，tray begin/end 配对 |
| 单实例（同会话） | 第二实例启动 | 17ms 退出并激活已有面板 |
| 隐藏态性能（独立复测） | release 副本 + 真实 packaged collector，隔离数据目录，4 分钟 | 见 §5.5：稳态 WS ~240MB；**CPU 0.49–1.04%/单核，4 分钟均值约 0.8%**；20s 启动峰值 WS ~450MB |
| 采集器崩溃 | 杀掉 collector 后观察 8s | 状态 exited，无自动重启，**托盘继续显示最后电量** |
| 托盘“固定面板”（隐藏态） | debug 构建 PB_TEST_TRAY_ACTION=pin | 事件丢失；下次显示时**迟到触发并持久化 pinned=true** |
| 中文/空格/非系统盘路径 | 复制 release+collector 到 %TEMP%\电量 显示 测试\pb app | packaged collector 正常、hello、exit 0、Job active=0 |
| 默认路径安装/卸载 | setup /S 安装 → uninstall /S | exit 0，但安装目录与 HKCU 残留（P1-01） |
| 125% DPI 真机截图 | 面板矩形截图 + 原生输入 | 尺寸 975×550=780×440 DIP，内容完整；拖拽仅 6px 缝可用、F5 重载（P1-08/P1-09） |
| 高对比度/减少动效/文本缩放 | Playwright 同特性矩阵 + 静态 | 减少动效通过；forced-colors/文本缩放不达标（P2） |
| 安装包检查 | 7-Zip 解包 + Authenticode | 69 文件、资源映射一致；**全部 NotSigned**（P1-03） |

**未验证**：真实睡眠/唤醒（用注入时钟模拟）、Win10、100/150/200% DPI、双屏/混合 DPI、RDP 双会话、ARM64、真机高对比度、触摸屏、24h 常驻、真实 HID 外设矩阵、企业 PowerShell 策略、GitHub Releases（网络阻断）。

---

## 3. P1 详情（发布前必须修）

### P1-01 默认安装目录 = 应用数据目录：卸载残留，且“删除应用数据”删错目录

- **现象**：currentUser 安装到 %LOCALAPPDATA%\PeripheralBattery，而该目录同时是采集器/日志/配置的数据目录。卸载器只做非递归 RMDir，程序文件删掉后 app.json、config.json、aliases.json、history.json、logs、diagnostics 全部残留；HKCU\Software\peripheralbattery\PeripheralBattery 也残留。卸载器里的“删除应用数据”清的是 %LOCALAPPDATA%\com.peripheralbattery.app（73MB 的 WebView2 profile），**不是**真正的数据；静默/被动卸载永远取不到该勾选。
- **位置**：src-tauri/src/paths.rs:26,36；target/release/nsis/x64/installer.nsi:499-514、912-914、964-977；tauri.conf.json:50-53。
- **触发**：用户按默认路径安装 → 使用数天 → 卸载（README 声称“卸载后目录/进程/快捷方式全清”仅对 /D= 非默认路径成立）。
- **证据**：默认路径 /S 安装 → /S 卸载 exit=0；INSTDIR 存在；残留 aliases.json/app.json/config.json/history.json/logs（app.log 1.6MB）/collector.pid.json；注册表 MANUPRODUCTKEY 仍在；WebView2 profile 73MB 未被删。
- **修复**：拆分安装目录与数据目录（安装到 %LOCALAPPDATA%\Programs\PeripheralBattery，或数据迁到 %APPDATA%\PeripheralBattery）；卸载用 RMDir /r 并指向真实数据目录；提供 /DELETEAPPDATA；回归必须覆盖默认路径。
- **验收**：默认路径安装→产生数据→卸载：安装目录消失、按勾选删除真实数据、注册表无残留；WebView2 profile 与数据目录分别可控。

### P1-02 WebView2 交付只有在线 bootstrapper：离线/内网/Server/Sandbox 装不上，也无运行期检测

- **现象**：tauri.conf.json 未配置 webviewInstallMode；生成的 installer.nsi 为 INSTALLWEBVIEW2MODE "downloadBootstrapper"、MINIMUMWEBVIEW2VERSION ""。仅当注册表查不到 WebView2 时联网下载，失败即 Abort；不检查最低版本；应用内无 WebView2 检测/提示。发行说明未写系统要求。
- **位置**：tauri.conf.json；installer.nsi:56、60、552-642。
- **触发**：Win10 1809/21H2 LTSC、企业精简单、Windows Server/RDS、Windows Sandbox（默认断网）、代理阻断 go.microsoft.com。
- **证据**：7-Zip 解包无 bootstrapper/offline 包；MINIMUMWEBVIEW2VERSION 为空；本机 WebView2 155 永远触发不到该分支。
- **修复**：embedBootstrapper 或 offlineInstaller；设置 minimumWebview2Version；启动时 GetAvailableCoreWebView2BrowserVersionString 预检并弹 MessageBoxW 指路；README 增加“系统要求”。
- **验收**：断网安装成功；旧 WebView2 被升级或给出可操作提示；无 WebView2 启动时用户能看到中文错误而非“双击没反应”。

### P1-03 setup/app/collector 全部未签名、无发布者元数据、无校验和

- **现象**：Get-AuthenticodeSignature 三个二进制均 NotSigned；COPYRIGHT/LICENSE 为空；build.ps1 不生成 SHA256SUMS，但发行说明让用户下载 .sha256。
- **位置**：tauri.conf.json:37-54；installer.nsi:41、50；scripts/build.ps1:64-67。
- **触发**：任意用户从 GitHub Releases 首次下载 → SmartScreen“未知发布者”；PyInstaller onedir + 未签名易被 Defender/EDR 启发式拦截；企业无法自证来源。
- **修复**：对 setup.exe、uninstaller、app.exe、collector.exe 做 Authenticode 签名+时间戳；build.ps1 生成 SHA256SUMS 与 build-info.json；填写 publisher/copyright/licenseFile。
- **验收**：签名状态 Valid；发布目录含 SHA256SUMS；SmartScreen 无“未知发布者”。

### P1-04 系统级最小化（Win+↓ / Win+D / 显示桌面）后 window_visible 滞留：第一次点托盘无效、隐藏四步不执行

- **现象**：用户用 Win+↓ 或 Win+D 最小化面板时不会触发 Focused(false)，Rust 的 window_visible 仍为 true。下一次点托盘走 hide（用户看到没反应），第二次才 show；隐藏四步/IsVisible(false)/TrySuspend 全部跳过，隐藏态 CPU/内存优化不生效。
- **位置**：src-tauri/src/panel.rs:232-239、306-344；state.rs:95-101。
- **证据**：SW_MINIMIZE 后 iconic=True visible=True，无 hidden 日志；模拟托盘点击 → window hide reason=tray-click；第二次点击才 show。
- **修复**：toggle_panel 先查 win.is_minimized() → 直接 show；状态以 IsWindowVisible + !IsIconic 校准；拦截 WM_SIZE/WM_SYSCOMMAND(SC_MINIMIZE) 或监听 Tauri 最小化事件走完整隐藏四步。
- **验收**：Win+↓/Win+D 后单击托盘一次即显示；隐藏日志、IsVisible(false)、TrySuspend 均出现；指标口径在该场景仍成立。

### P1-05 单实例使用 Global\ 命名对象：RDP/快速用户切换/UAC 提权不一致时失效或静默双实例

- **现象**：默认 Global\PeripheralBattery.single。跨会话时第二用户（管理员）能打开 mutex/event，触发的是第一用户桌面的 show，自己桌面无 UI；若 DACL 拒绝则返回 Disabled 继续启动，导致双实例/双托盘/双 collector。UAC 提权一次再普通启动同理。
- **位置**：src-tauri/src/single_instance.rs:23、69-99、101-113；main.rs:44-71。
- **证据**：同会话单实例 PASS（第二实例 124ms 退出并激活面板）；跨会话/跨权限为静态结论 + README 自认未测。
- **修复**：用 Local\ + 会话/用户维度的命名（或名字拼 SessionId/SID）；明确区分“已有实例但不可访问”并给出可见提示；不要把 ERROR_ACCESS_DENIED 静默降级成第二个实例。
- **验收**：两个会话/两个用户各能独立运行；同会话第二实例仍正确激活；提权/非提权混用不产生双实例；必要时在 UI 明确提示冲突。

### P1-06 cleanup_residual 会误杀另一个活实例的 collector（并行测试隔离失效）

- **现象**：每次 sidecar.start() 都执行 cleanup_residual：读取共享 collector.pid.json 直接 kill，并按安装路径全盘扫描同名 collector.exe 再 kill；不校验该 collector 是否属于自己/父实例是否存活。第二个实例启动会把第一个健康实例的 collector 杀掉，第一实例进入 exited 且不自动重启，托盘冻结。
- **位置**：src-tauri/src/sidecar.rs:829-865、298-301。
- **证据**：实例 A 的 collector(pid 5292 ready) 被实例 B 启动时杀掉，A 日志 cleanup residual + EOF + exited，无第三次 spawn；本机 16:59:26 并行测试中也真实误杀了另一实例的 collector pid 25760。
- **修复**：pid 记录绑定实例身份（app pid + 随机 token + session）并校验归属；删除按安装路径的全盘 kill，依赖 Job Object(KILL_ON_JOB_CLOSE) 已可级联；文档写明 PB_SINGLE_INSTANCE_NAME 不足以隔离并行测试，需独立数据目录+独立安装副本。
- **验收**：两个 app 实例（不同实例名/不同数据目录）互不杀 collector；残留 pid 文件不会误杀无关进程。

### P1-07 Win11 虚拟桌面下托盘/第二实例激活看不到面板

- **现象**：窗口属于创建时的虚拟桌面；tao 0.37.1 在 Windows 不支持 visible-on-all-workspaces，代码也没有 IVirtualDesktopManager 处理。应用常驻桌面 1，用户切到桌面 2 点托盘 → 面板弹在桌面 1，当前桌面什么都没发生。
- **位置**：src-tauri/src/panel.rs:148-186；main.rs:48-60；tauri.conf.json（无相关配置）。
- **触发**：Win11 多桌面工作流（高频）、RDP 重连后 session 变化、单实例二次启动。
- **修复**：显示前用 IVirtualDesktopManager.IsWindowOnCurrentVirtualDesktop 判断，不在当前桌面则 MoveWindowToDesktop(hwnd, 当前桌面) 后再 show；失败给托盘气泡提示。
- **验收**：桌面 1 运行 → 切桌面 2 → 点托盘，面板出现在桌面 2。

### P1-08 拖拽区只写在 .side-head 自身：拖标题/内容完全不能移动窗口，只有约 6px 缝隙可拖

- **现象**：index.html:67 的裸 data-tauri-drag-region 在 Tauri 2 只对元素自身生效；header 面积几乎被标题行/副标题覆盖，命中子元素时不算拖拽区。真机在 h1 上拖 100px 窗口不动，只有标题行与副标题之间约 6px 缝隙能拖。
- **位置**：ui/index.html:67；Tauri 2.12.4 tauri-2.12.4/src/window/scripts/drag.js:44-67。
- **证据**（真机 125%）：drag-h1 moved=False；drag-gap moved=True（窗口从 x=1567 到 1467）。
- **修复**：改为 data-tauri-drag-region="deep"（放在 .side-head 或 .panel），Tauri 会自动排除 button/input 等可交互元素；补原生拖拽回归。
- **验收**：在标题、副标题、空白 header 上拖动均能移动窗口；按钮仍可点击。

### P1-09 生产面板弹出 WebView2 浏览器右键菜单；F5/Ctrl+R/Ctrl+P 加速键生效（F5 实测重载并重置窗口位置）

- **现象**：右键出现“返回/刷新/另存为/打印/更多工具”；F5 触发整页重载，产生第二次 ui_ready 与 show，把用户拖到别处的窗口重置回托盘锚点，并丢失主卡/抽屉状态；Ctrl+P 打印预览覆盖透明小窗，观感像卡死。
- **位置**：ui/index.html（无 contextmenu 处理）；ui/app.js 全局 keydown 只处理 Esc（1468-1470）；tauri.conf.json 未禁用；wry 0.57 默认 browser_accelerator_keys=true、default_context_menus=true。
- **证据**（真机）：右键截图菜单项齐全；F5 后 app.log 17:05:40 第二次 command ui_ready / window show reason=ui-ready，窗口 origin 从 (1467,812) 回 (1567,812)。
- **修复**：前端 contextmenu preventDefault + 拦截 F5/Ctrl+R/Ctrl+P/Ctrl+F/Ctrl+S；根治在 wry/Tauri 层关闭默认右键菜单与浏览器加速键。
- **验收**：右键无浏览器菜单；F5/Ctrl+R 不重载、不重置窗口；Ctrl+P 无打印预览。

### P1-10 休眠/唤醒后所有设备被误判 gone 再整批重加（Modern Standby 高频场景）

- **现象**：collector/state.py 用 time.monotonic()（Windows 上为 GetTickCount64，包含睡眠时间）比较 last_seen；唤醒后第一次 tick 所有设备超过 max(2*TTL,10s) → 全部 gone，随后重新 device。面板/托盘闪离线，低电/充满告警状态被清空，P9 的 gone/key 统计被污染。collector 无任何恢复处理，protocol.new_session() 没有调用方。
- **位置**：collector/state.py:449-474、423-436；collector/protocol.py:99-104；collector/collector.py:612-639。
- **证据**：可注入时钟单测：正常 2 分钟 gone=[]；模拟 +3600s 后第一次 tick → 2 台全部 gone、visible=0；重新轮询后累计 4 个 device 事件。
- **修复**：Rust 接 WM_POWERBROADCAST(PBT_APMRESUMEAUTOMATIC) 给 collector 发 new_session；或 on_tick 检测单调时钟跳变（>5s 或 >3×tick）时跳过该轮 _expire 并把 last_seen 拨到 now；补注入时钟单测。
- **验收**：模拟睡眠 1 小时唤醒：无 gone 风暴；UI 保留设备；session/告警状态按设计重置。

### P1-11 蓝牙 watcher 的 30s 轮询把“连接后 +3/+8/+15s 补采”退化成 +30s，F3 冷启动补采同样被吞

- **现象**：btconfig.py 只替换 Start-Sleep 数值，WATCH_SCRIPT 的 $queue 在 30s 睡醒后一次性弹出所有 due 点，只做一次快照。连接后/冷启动的 +3/+8/+15s 补采全部丢失，实际只有 +30s 一次；README 与注释“补采不受影响”不成立。
- **位置**：collector/btconfig.py:39-40、102-112；collector/providers/bluetooth.py:107-131；collector/collector.py:286-300。
- **证据**：真实 --stdio 75s 日志只有两组蓝牙快照：16:59:32.618 与 17:00:03.810，间隔 31.19s；70s stat snapshots=2 watch_ms=30000。
- **修复**：循环按“最早 due 时间”睡眠（min(nextConnCheck, oldestQueueDue, nextFull)），或保留 2s 细粒度 tick 但用时间戳门控实际查询；补断言 +3/+8/+15 时间戳的测试。
- **验收**：设备连接后（电量晚几秒上报）3–8s 内出现电量；冷启动首快照漏设备时按 +3/+8/+15/+30 重试。

### P1-12 隐藏态点击托盘“固定面板”事件丢失，且在下次显示时迟到触发、持久化

- **现象**：隐藏时 WebView2 已 TrySuspend，托盘菜单“固定面板”只 emit tray-action 给前端；前端收不到，pin 不变。该事件会排队到下次 Resume，用户下次打开面板时突然执行 toggle-pin 并写入 app.json——意外固定/取消固定。
- **位置**：src-tauri/src/tray.rs:308-311；ui/app.js:1304-1305、1415、1244-1246；面板隐藏四步见 panel.rs:211-255。
- **证据**（debug PB_TEST_TRAY_ACTION=pin）：17:08:04.305 tray action=toggle-pin，隐藏态无任何 set_pinned；17:08:08.752 激活显示后立即 command set_pinned pinned=true 并 app.json saved pinned=true。
- **修复**：pin 在 Rust 侧直接完成（state.set_pinned + 托盘菜单勾选状态 + emit app-state），不依赖 WebView；或先 show_panel/resume 再 emit。托盘“刷新”已是 Rust 侧，可复用同一模式。
- **验收**：隐藏态点“固定面板”立即生效（app.json、托盘菜单勾选、下次显示状态一致）；下次显示不会发生意外 toggle。

### P1-13 采集器崩溃/退出后无自动恢复，托盘永久冻结在最后电量

- **现象**：collector 非正常退出（code≠0）后只发布 collector-state=exited：不自动重启、不把托盘切成离线；tray_mirror 只在收到消息时更新，于是托盘 tooltip/图标继续显示崩溃前电量，用户以为数据仍然实时。面板隐藏时用户看不到错误，必须打开面板点“重启采集器”。
- **位置**：src-tauri/src/sidecar.rs:632-707；tray_mirror.rs 只在消息回调更新；ui/app.js 1290 重启按钮。
- **证据**：假采集器上报 77% 后 exit 2；8 秒内无 tray-state 更新、无新 spawn，托盘保持“外设电量 · 77%”。shell 专项同样复现（kill 后 8s spawns=1，tray_end 不再增长）。
- **修复**：非正常退出做指数退避自动重启（1s/3s/10s，最多 5 次）；退出/错误时立即发布 offline 托盘状态；重试用尽才显示错误并保留手动重启。
- **验收**：kill collector 后 ≤2s 托盘变离线；≤3s 自动重启并恢复设备；无重复实例/孤儿；日志可追溯重启次数。

---

## 4. Windows 11 适配矩阵（本次实测/静态判定）

| 场景 | 结果 | 说明 / 证据 | 关联 |
|---|---|---|---|
| Win11 25H2 build 26300 启动/托盘/显示/隐藏 | **通过** | 10/10 可见启动无冻结；setup 194–232ms；隐藏四步日志完整 | — |
| 125% 缩放（最常见）渲染 | **基本通过** | 975×550=780×440 DIP，内容无裁切；边框/圆角有半像素发虚、阴影被 30px 边裁切 | P2 |
| 100/150/200%、运行中改缩放 | **未验证** | 仅单屏 125%；代码有 ScaleFactorChanged 路径 | P2/未验证 |
| 多显示器（副屏/负坐标/竖屏/混合 DPI） | **未验证** | 本机单活动屏；position_panel 用窗口 scale 而非托盘所在屏 scale，静态有 1–2 帧错位风险 | P2/未验证 |
| 任务栏位置/自动隐藏 | **未验证** | 依赖 tray.rect() 与工作区夹取；缓存锚点不随 DPI/任务栏变化失效 | P3 |
| 系统级最小化 Win+↓/Win+D | **失败** | 状态滞留、首次托盘点击无效 | P1-04 |
| 虚拟桌面（多桌面） | **失败（静态高置信）** | 面板弹在创建时桌面 | P1-07 |
| 快速用户切换 / RDP 多会话 | **失败（静态高置信）** | Global 单实例静默激活他人桌面或双实例 | P1-05 |
| UAC 提权/非提权混用 | **失败（静态高置信）** | 同上 | P1-05 |
| 睡眠/Modern Standby 唤醒 | **失败** | 全部设备 gone 风暴；无 WM_POWERBROADCAST | P1-10、P2 |
| 锁屏/解锁 | **未验证** | 锁屏会触发失焦隐藏，路径合理；无恢复逻辑 | 未验证 |
| 高对比度 forced-colors | **失败** | UI 整体变灰、图标不可见、无状态文本替代 | P2 |
| 系统文本缩放 125/150% | **失败** | 全 px 单位，字号不随 TextScaleFactor 缩放 | P2 |
| 系统“透明效果”关闭（旧 WebView2） | **部分风险** | prefers-reduced-transparency 需 WebView2≥118，旧版无兜底 | P2 |
| 全屏游戏/专注助手 | **未实现** | 无 SHQueryUserNotificationState，alwaysOnTop 可能盖住游戏 | P2 |
| Explorer 重启 | **低风险（未验证）** | tray-icon 0.25.1 处理 TaskbarCreated 重注册 | 未验证 |
| 触摸/笔输入 | **体验不足** | 控件 24–30px，低于 Win11 触控建议 44px | P3 |
| 非 ASCII / 空格 / 非系统盘路径 | **通过** | 中文+空格路径 packaged collector 正常 | — |
| 长路径/重定向 LOCALAPPDATA/漫游 | **未验证** | 关键路径未加 \\?\ 兜底；日志/配置可回退 %TEMP% 但无提示 | P2 |
| Win10 1809/21H2 回退 | **未验证** | 代码路径存在；set_effects(None) 结果被吞，日志是虚假成功 | P2 |
| Windows Server / Sandbox / 精简系统 | **失败（交付）** | WebView2 在线 bootstrapper 断网即 Abort | P1-02 |
| ARM64 Win11 | **未验证** | 仅 x64 包；x64 模拟大概率可跑，NSIS/WebView2 探测未验证 | P2 |
| 默认路径安装→运行→卸载 | **失败** | 数据目录=安装目录，卸载残留 | P1-01 |
| 静默安装/卸载、部署选项 | **部分** | /S、/D、/NS 等可用但无文档；静默默认建桌面快捷方式；allowDowngrades=true | P2 |
| 无网络安装 | **失败** | 无 WebView2 时在线下载失败即 Abort | P1-02 |
| 企业禁用 PowerShell / ConstrainedLanguage | **降级但静默** | 蓝牙退化为 PnP 连接标志（会闪），无用户提示 | P2 |
| 34 个 HID/蓝牙厂商设备全覆盖 | **未验证** | 本机仅 FlyDigi BS2PRO 等少数设备；24h key 抖动未跑 | 未验证 |
| 24h 常驻内存/句柄/P7/P9 | **未验证** | 仅 4 分钟采样；启动峰值 450MB 已复现 | P2/未验证 |

---

## 5. P2 清单（按模块归并；位置与修法见专项报告）

### 5.1 安装 / 更新 / 分发

- **P2-01 自动更新链路完全缺失**：无 tauri-plugin-updater、无签名 manifest/回滚；只能手动重装（platform-findings P2-10）。
- **P2-02 卸载注册表/WebView2 profile 残留**：MANUPRODUCTKEY 只在勾选分支删除；profile 73MB 永不清理；静默卸载无法删数据（platform P2-4）。
- **P2-03 build 号恒为 unknown**：release 二进制内 build=unknown；build.rs 缺 .git/HEAD 依赖，git 失败静默；安装包无法回溯 commit（shell P2-7 / platform P2-1）。
- **P2-04 构建不可复现、无 CI**：scripts 硬编码个人 Python 路径、运行时 pip install、版本三处手写、setup 内主 exe 与 target/release 差 3 字节、dist 新鲜度不校验（platform P2-3）。
- **P2-05 安装器仅英文、无许可页、LICENSE/THIRD_PARTY_NOTICES 未随包**：且 am_infinity.py 的 GPL 来源未在 NOTICES 声明（platform P2-2）。
- **P2-06 开机自启未实现**：托盘常驻工具每次开机需手动启动，与 TECH_PLAN M3 不符（platform P2-5）。
- **P2-07 部署选项/静默参数无文档**：/S、/D=、/NS、/R、/ARGS、/P、/UPDATE；静默默认建桌面快捷方式；allowDowngrades=true；无 MSI/per-machine（platform P2-8）。
- **P2-08 无 OS 下限声明、仅 x64**：app manifest 无 supportedOS/dpiAware/longPathAware；Win7/8.1 也能装但 WebView2 不支持（platform P2-9）。
- **P2-09 sidecar 无完整性校验**：安装目录用户可写且未签名，collector.exe 可被替换（platform P2-7）。

### 5.2 外壳 / 生命周期 / Win11 集成

- **P2-10 托盘双击净无效**：只处理 Up，双击 = 两次 toggle（先显示后隐藏）（shell P2-1）。
- **P2-11 首次显示 tray.rect() 竞态**：10 次中 1 次返回临时矩形，面板偏高 223px；clamp 无法纠正（shell P2-2）。
- **P2-12 WM_POWERBROADCAST/电源通知未接**：恢复后不 re-snapshot、不刷新托盘、不重设 DWM 效果（shell P2-4）。
- **P2-13 Job Object spawn→assign 竞态 + assign 失败回退不足**：子进程可能逃逸；失败只杀直接进程（shell P2-5 / platform P2-7）。
- **P2-14 主线程阻塞写 sidecar stdin**：collector 不读 stdin 时 64KB 管道写满会冻结 UI（shell P2-6）。
- **P2-15 restart 未串行化、stdout 线程无 generation 防护**：并发 restart 可能双 spawn；旧进程残留行会转发前端（shell P3-5/P3-6）。
- **P2-16 启动期严重失败无用户提示**：WebView2 缺失/托盘创建失败 → panic 静默退出；无 panic hook（shell P2-13）。
- **P2-17 配置损坏/数据目录不可写静默降级**：app.json 损坏不备份不重建；写失败只 WARN；日志回退 %TEMP% 无提示（shell P2-12 / collector P2-14）。
- **P2-18 Win10 回退可观测性差**：set_effects 失败被吞，日志“成功”不可信；Win10 未实测（shell P2-8）。
- **P2-19 透明窗口无命中测试：30px 安全边/圆角外仍吞鼠标**（UI P2-13 / shell P2-9）。
- **P2-20 全屏游戏/专注助手静默策略未实现**（shell P2-10）。
- **P2-21 capabilities 过宽 + withGlobalTauri:true**：大量未用 window 权限、core:path，配合全局 __TAURI__ 放大注入面（shell P2-11 / UI P2-9）。
- **P2-22 单实例 ShowWindow 兜底不同步状态**：可能留下空白/冻结悬浮窗（shell P2-14）。
- **P2-23 托盘图标尺寸口径偏差**：渲染 32*scale，100% 下 32px 被 Explorer 下采样发虚（shell P3-9）。
- **P2-24 硬编码开发者 Python 路径**：sidecar.rs:919-927 只在本机成立（shell P3-7）。

### 5.3 采集器 / 设备兼容

- **P2-25 电量边界**：255 被显示成 100%、-1 成 0%；应把超出 0..100 视为未知（collector P2-1）。
- **P2-26 NaN/inf 使 make_device 抛异常**：能中断整轮 merge，该设备存在期间持续失败；应按设备隔离并返回 None（collector P2-2）。
- **P2-27 aliases.json 损坏静默清零**：hidden/rename 会错配到别的设备，migration 历史归零（collector P2-3）。
- **P2-28 KeyStore.save 先清 dirty 再写盘**：写失败静默丢持久化，且不重试（collector P2-4）。
- **P2-29 同品牌多设备 key 合并**：mchose/wlmouse/lamzu/gwolves 共用同一 key，同型号两只只能显示一个；XInput 同型号按 slot 无法区分（collector P2-5）。
- **P2-30 --fake migration 场景演示不出迁移**：duration/时间线不匹配，replaces 不出现（collector P2-6）。
- **P2-31 冷启动/--once 首快照约 8s**：30 provider/4 worker/1Hz 分批；纯 HID 用户无等价 F3 验收（collector P2-7）。
- **P2-32 WGI 每 8s 起 PowerShell**：手柄在线时 0.22–0.48s CPU/次，P2/P3 未覆盖该场景（collector P2-8）。
- **P2-33 stdout 背压无超时/守护**：Rust 读端停顿会在持锁状态阻塞，只能靠 2s 超时硬杀（collector P2-9）。
- **P2-34 DaemonPool worker 永不回收**：provider 永久卡死会逐步吃满 4 个槽，最终停止轮询（collector P2-10）。
- **P2-35 offline 告警从未发出**：协议常量有 offline，实现只发 low/full（collector P2-11）。
- **P2-36 蓝牙 TTL 余量仅 1 个快照**：漏一次快照即 gone 抖动；阈值 120s vs 稳态 60s（collector P2-12）。
- **P2-37 设备名/字符串原样进日志**：蓝牙 FriendlyName 可含换行/控制字符，能伪造日志行（collector P2-13）。
- **P2-38 --probe/--once 不安装 stderr 日志**：排障时关键错误全部丢弃（collector P2-15）。
- **P2-39 poll 配置无上限**：允许 100000s，gone 阈值随之变成 55h，设备拔掉永不 gone（collector P2-16）。
- **P2-40 BLE 手柄 kind 识别失败**：FlyDigi BS2PRO 显示通用蓝牙图标（collector P2-17）。
- **P2-41 --speed 与固定 poll 间隔交互**：demo 时间线事件被跳过，演示结果失真（collector P2-18）。
- **P2-42 企业 PowerShell 策略未适配/无提示**：ConstrainedLanguage 下 Add-Type 失败，蓝牙退化为会闪的 PnP 标志；完全禁用则无数据（collector §4）。

### 5.4 前端 / 无障碍 / 安全

- **P2-43 高对比度/prefers-contrast 零适配**：forced-colors 下面板变灰、齿轮图标不可见，低电仅靠颜色（UI P2-1/P2-3）。
- **P2-44 无障碍打包问题**：subtitle live region 重复播报、抽屉无 dialog/aria-modal/背景 inert、toast 键盘不可关、hero aria-label 残留、行 focus 外框被裁、删行焦点掉 body（UI P2-4/P2-5/P2-12）。
- **P2-45 125% 半像素 + 阴影被 30px 安全边裁切**（UI P2-2）。
- **P2-46 旧 WebView2(<118) 无 reduced-transparency 兜底**：系统关透明时面板透壁纸、可读性下降；应由 Rust 下发透明能力标志（UI P2-6）。
- **P2-47 旧 session 迟到事件把 UI 切回旧 session**：面板闪旧设备/空列表；helloReceived 不复位导致重启后快照兜底失效（UI P2-7/P2-8）。
- **P2-48 toast 超长文本溢出（scrollWidth 4400 vs 298）+ toast 吞掉底部行点击**（UI P2-10/P2-14）。
- **P2-49 “全部设备”按钮语义不符**：有设备时无可见反馈，空列表时打开设置抽屉（UI P2-11）。
- **P2-50 文本缩放不生效、10.5–11px 小字**：全部 px，根字号 200% 字号不变（UI P2-11）。
- **P2-51 release 暴露 window.__pbMock 与完整 __TAURI__，CSP 可再收紧**（UI P2-9）。

### 5.5 性能 / 资源（Lead 复测）

- **P2-52 隐藏态 CPU 未复现 README 的 0.318%，独立 4 分钟均值约 0.8%/单核**：60s 窗口 0.485–1.043%，其中常驻 PowerShell 全量快照约 0.3–0.5s CPU/次（每 60s 一次）是主因；P2 目标 <0.5% 在“蓝牙设备在线”场景**不达标/需重新验收**。
- **P2-53 启动峰值 WS 约 450MB（复现 README 449MB），超 P4 320MB 目标**：t=20s 树内 WS 约 450MB（含约 269MB 重复计数的 WebView2 子进程，去重后），TrySuspend 生效后稳态约 240MB。若指标按“任意时刻”统计则不达标。
- **P2-54 P7 24h 内存增长、P9 24h key 抖动仍未验证**：仅 4 分钟/75s 采样；无法判定 15MB/2 次目标。

> P3 见各专项报告：日志轮转失败分支、时间戳无时区、多实例共享回退日志、release 保留 PB_FPS_PROBE、托盘图标尺寸、tray_rect 缓存不过期、长路径、启动期主线程 I/O、触摸目标偏小、Esc 不隐藏面板、user-select:none、缺少 color-scheme、主卡无法恢复自动模式等。

---

## 6. 已复验成立（避免误报/返工）

- **数据/协议**：stdio 五条硬化（stdout=None、UTF-8、flush、stderr 排空、1MiB 行上限/半行/EOF）；完整快照门控；last_seen gone（正常场景）；稳定 key + aliases + replaces 迁移；Job Object 硬杀清树；PyInstaller onedir 独立运行；P8 timers_per_s=0.991。
- **外壳**：旧 B1 可见启动死锁已修复（10/10）；隐藏态托盘镜像（TrySuspend 后仍更新）；托盘“设置”先 show 再 emit；config.json/app.json 分离；\\?\ 路径归一化；窗口 780×440、acrylic、DWM 圆角；同会话单实例；退出/超时/强杀均无 app/collector/PowerShell 残留。
- **前端**：主卡取最低电量；keyed diff 复用；隐藏四步（WAAPI/rAF/timer/DOM 全停）；XSS 不可利用（有效注入未执行）；CSP 0 违规；生产 0 backdrop-filter；reduced-motion；125% 内容完整；键盘设主卡焦点；DOM/heap 无明显泄漏。
- **打包**：bundle.resources 映射正确（collector 与 dist 哈希一致）；非默认路径（空格/中文/非系统盘）安装布局可运行；安装/卸载主链路 exit=0。

---

## 7. 待修复清单（执行顺序建议）

### 阶段 A：发布前必须修（14 项 P1）

| # | 事项 | 验收标准 | 预估 |
|---|---|---|---|
| A1 | 拆分安装目录与数据目录；卸载 RMDir /r + 正确数据目标 + /DELETEAPPDATA | 默认路径安装→卸载后安装目录消失、数据按勾选删除、注册表无残留 | 1–2d |
| A2 | WebView2 改 embed/offline + 最低版本 + 运行期检测提示 | 断网可安装；旧版被升级/提示；缺 WebView2 有中文错误框 | 1–2d |
| A3 | setup/uninstall/app/collector 签名 + SHA256SUMS + 元数据 | Valid 签名；发布含校验和；SmartScreen 无未知发布者 | 0.5d（证书除外） |
| A4 | 系统级最小化状态同步（Win+↓/Win+D） | 一次托盘点击即显示；隐藏四步/TrySuspend 执行 | 0.5d |
| A5 | 单实例改 Local/会话+用户维度，明确跨会话策略与可见提示 | 双会话/双用户独立运行；同会话单实例正常；无双实例 | 1d |
| A6 | cleanup_residual 绑定实例身份、删除全盘扫描 | 并发实例互不杀 collector；残留 pid 不误杀 | 0.5d |
| A7 | 虚拟桌面支持（IsWindowOnCurrentVirtualDesktop/MoveWindowToDesktop） | 桌面 2 点托盘在桌面 2 显示 | 1d |
| A8 | 拖拽区改 deep | 标题/副标题/空白 header 均可拖动；按钮可点 | 0.1d |
| A9 | 禁用 WebView2 右键菜单与浏览器加速键（F5/Ctrl+R/P） | 右键无菜单；F5 不重载/不重置窗口 | 0.5d |
| A10 | 休眠恢复：电源事件或时钟跳变抑制 gone | 模拟睡眠唤醒无 gone 风暴；UI 保留设备 | 1d |
| A11 | 蓝牙 watcher 按最早 due 时间睡眠，恢复 +3/+8/+15 补采 | 连接后 3–8s 出电量；冷启动补采按计划执行 | 0.5–1d |
| A12 | 托盘 pin 在 Rust 侧直接生效 | 隐藏态 pin 立即生效；下次显示无迟到 toggle | 0.5d |
| A13 | collector 崩溃自动重启 + 托盘立即离线 | kill 后 ≤2s 离线、≤3s 重启恢复；无孤儿 | 1d |
| A14 | 展开后灰色边框：SetWindowRgn 面板区域裁剪 + acrylic 不可用降级（详见 §10；同时修复 P2-13/P2-19 透明边吞点击） | 三背景无灰框；透明效果 0/1/重启/虚拟显示；面板外不吞点击 | 0.5–1d |

> 合计约 8.5–12 人日（不含证书采购/真机矩阵）。A1/A2/A3 属发布阻断项；A5/A7/A10/A14 是 Win11 典型工作流/可见体验，建议同批修。

### 阶段 B：下个版本（高价值 P2，建议优先）

1. **P2-13/P2-14/P2-16/P2-17/P2-18**：Job 竞态、主线程写 stdin、启动失败提示、配置/数据目录降级提示、Win10 可观测性——都与“用户看到失败而不是闪退/冻结”相关。
2. **P2-10/P2-11/P2-12**：托盘双击、首次定位竞态、电源事件补齐（A10 的 shell 侧）。
3. **P2-43/P2-44/P2-45/P2-50**：高对比度、无障碍打包、125% 半像素、文本缩放——Win11 辅助功能基本盘。
4. **P2-25/P2-26/P2-27/P2-29/P2-36**：电量边界、NaN、aliases 持久化、同品牌 key、蓝牙 TTL——数据可信度。
5. **P2-31/P2-32/P2-52/P2-53**：冷启动 8s、WGI 成本、隐藏 CPU/启动峰值——性能承诺需重新对齐 README。
6. **P2-01/P2-02/P2-04/P2-05/P2-06/P2-07**：updater、卸载残留、CI/可复现、许可、自启、部署文档。
7. **P2-19/P2-20/P2-21/P2-46/P2-47/P2-48/P2-49/P2-51**：点击穿透、全屏静默、capability 收敛、旧 WebView2 透明兜底、session 回退、toast、按钮语义、安全加固。

### 阶段 C：持续改进（P3）

日志轮转失败/时区、release 测试钩子、托盘图标尺寸、tray_rect 过期、长路径、启动主线程 I/O、触摸目标、Esc 行为、user-select、color-scheme、主卡自动模式恢复、诊断字段展示等。

---

## 8. 未验证与残余风险（发布前建议补测）

1. **真实睡眠/唤醒**（本报告用注入时钟，未真正 suspend）。
2. **Win10 21H2/22H2 实机**：acrylic 关闭、方角、透明、性能、安装。
3. **DPI 矩阵**：100/125/150/200%、运行中改缩放、混合 DPI 双屏、副屏负坐标/竖屏、任务栏自动隐藏/左/上。
4. **RDP/快速用户切换/UAC 提权**：单实例与 collector 归属（P1-05/P1-06）。
5. **ARM64 Win11**、Windows Server、Windows Sandbox、企业 AppLocker/WDAC（PowerShell 受限）。
6. **24h 长稳**：P7 内存增长、P9 key 抖动、句柄/线程趋势。
7. **真机外设矩阵**：30 provider 的 serial/mac/unit 解析、同型号多设备、HID 独占失败（G HUB/Synapse 占用）、2.4G 接收器、Steam/XInput/WGI。
8. **高对比度黑/白主题、TextScaleFactor 125/150%**：需真机截图回归（本轮为 Chromium 模拟）。
9. **GitHub Releases/链接**：本轮网络策略阻断无法验证资产是否存在。

---

## 9. 证据索引

- 专项报告：review/shell-findings.md、review/collector-findings.md、review/ui-findings.md、review/platform-findings.md
- UI 证据：review/ui-evidence/（results*.json + 30 张截图：DPI 矩阵、真机 125%、右键菜单、F5 后、forced-colors、焦点、XSS、长文本等）
- Lead 复现脚本与数据：review/lead/（test-perf-iso2.ps1、test-instances.ps1、test-portable.ps1、test-stale-hidden.ps1、test-force-kill.ps1、test-basic-user.ps1、cdp-check.js、ui-real.png、ui-stale-after-crash.png、ui-stale-hidden-crash.png 等）
- 运行日志：%LOCALAPPDATA%\PeripheralBattery\logs\app.log（关键行：17:07:36 collector exited while suspended；17:08:04 pin 事件 → 17:08:08 迟到 set_pinned；17:06:31 77% → 17:06:35 collector exit 2 后托盘无更新）
- 安装包与解包：src-tauri/target/release/bundle/nsis/PeripheralBattery_0.1.0_x64-setup.exe
- 基准测试：pytest 94 passed / cargo test 10 passed / ui_smoke 15 passed（命令见 §2）

---

> 说明：本报告由 1 名 Lead + 4 名独立审查员在同一工作树（HEAD a24f2fc）上并行完成；未修改任何源码、配置或发布产物（git status 仅有新增 review/）。所有运行时测试均使用 release/debug 临时副本与隔离数据目录，测试后已恢复 app.json pinned=false、无 app/collector 进程残留。

---

## 10. 追加（2026-10-19 20:30）：展开后四周灰色边框——根因与修复计划

> 现象由用户复现：点托盘展开面板后，720×380 面板四周出现约 30px 的灰色边框；按前文建议打开 Windows“透明效果”并完全退出重开后**部分环境仍然是灰的**。
> 本条只记录计划，**本轮未修改源码/构建产物**；确认后与 §7 的 A1–A14 一起实施并发 v0.1.1。

### 10.1 根因

1. 窗口按 BUILD_PLAN §5.1 为 **780×440 DIP**，CSS 面板 **720×380 DIP**，四周各 30px 安全边。
2. Tauri `windowEffects: ["acrylic"]` 作用于**整个窗口矩形**，不是只作用于 `.panel`；因此 30px 安全边也被 acrylic 填充。
3. acrylic 的实际呈现由 DWM 决定：
   - `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\EnableTransparency = 0` 时，Windows 把 acrylic 降级为**纯灰**；
   - 把该开关改为 1 后，**已存在的窗口不会重新应用**，必须完全退出应用再启动；
   - 即使开启，在部分虚拟显示/远程串流/驱动环境（本机装有 `OrayIddDriver Device`、`GameViewer Virtual Display Adapter`）或 DWM 模糊被禁用时，仍会呈现不透明灰底。
4. 所以灰框不是 CSS 背景色/面板颜色问题，而是「窗口矩形大于可见面板 + acrylic 整窗填充」的组合缺陷；用户环境只是把该缺陷放大成了可见问题。

### 10.2 证据

| 项 | 结果 |
|---|---|
| 窗口/面板 | 975×550 物理 = 780×440 DIP @1.25；面板 720×380 DIP；四周 30px |
| DWM backdrop | `DWMWA_SYSTEMBACKDROP_TYPE = 3`（acrylic），`DwmGetWindowAttribute` 返回 S_OK |
| 灰边像素 | 背景为亮粉/白云/深色时，灰边始终 ≈ `#4D4E4F ~ #535354`，不随背景变化 |
| 透明效果 0→1 + 不重启 | 仍灰 |
| 完全退出重开（本机） | 灰边变磨砂（可透出桌面）；但用户环境仍灰 → 不能只依赖 acrylic |
| 会话 | `SM_REMOTESESSION=0`，但存在 Oray/GameViewer 虚拟显示驱动 |

### 10.3 修复方案（推荐 A：窗口区域裁剪 + acrylic 降级）

**目标：可见窗口区域 = 面板形状，30px 安全边从合成与命中测试中裁掉；acrylic 只作用于面板内部，无论 acrylic 是否可用都不再出现灰框。**

1. `src-tauri/src/platform.rs` 新增 `set_panel_region(hwnd, scale)`：
   - 区域 = 面板物理矩形 `(30*scale, 30*scale, 750*scale, 410*scale)`，圆角直径 `28*2*scale`；
   - `CreateRoundRectRgn(...)` + `SetWindowRgn(hwnd, rgn, 1)`；
   - 需启用 `Win32_Graphics_Gdi` feature。
2. `panel::apply_platform()` 创建窗口后调用；`scaleFactorChanged` 重设尺寸后重新应用（先 `SetWindowRgn(hwnd, NULL, 0)` 再重建）。
3. `position_panel()` 定位补偿：可见面板左上角相对窗口偏移 `30*scale`，窗口位置整体减去该偏移；clamp 用**面板矩形**而不是 780×440 外框，保证贴托盘/光标。
4. acrylic 不可用降级：
   - 启动时读 `EnableTransparency`，为 0 → `win.set_effects(None)`，日志 `acrylic disabled: transparency off`；
   - 增加 `PB_DISABLE_ACRYLIC=1` 逃生口；
   - 可选：`SM_REMOTESESSION`/虚拟显示检测时也关闭 acrylic（先以注册表+环境变量为准，避免硬编码驱动名）；
   - `app-state` 增加 `acrylic: bool`，前端 `html[data-acrylic="off"] .panel` 提高烟熏底不透明度，保证无模糊时对比度。
5. 保留 30px 布局与 CSS 不变，只裁窗口区域；CSS `box-shadow` 会被裁掉（原本也基本被窗口裁切），改为更小的内阴影/边框表达深度。
6. 风险与回滚：`SetWindowRgn` 与 acrylic 组合需实机验证刷新/黑边；提供 `PB_DISABLE_PANEL_REGION=1` 回到当前行为。若 DWM 下不稳定，走备选 B。

**备选 B**：窗口直接改为 720×380，CSS `body` padding 30px → 0，`.stage/.panel` 填满窗口；acrylic 只在面板内，DWM 圆角与面板圆角差异用 `SetWindowRgn` 或面板 12px 圆角兼容。代价：失去 30px 阴影空间，定位/拖拽按 720×380 重算。

**备选 C**：移除 `windowEffects`，完全依赖 CSS 烟熏玻璃；30px 区域直接透出桌面（无模糊）。风险：部分远程/虚拟显示下整窗透明也可能异常，不作为首选。

### 10.4 验收标准

- 3 种背景（亮/暗/彩色）截图：面板外不再出现均匀灰色矩形；可见外轮廓与 720×380/28px 圆角一致。
- 透明效果 0/1、完全重启、远程/虚拟显示下行为一致：关闭时面板为 CSS 烟熏底、无灰框。
- 托盘点击/第二实例 show、失焦隐藏、最小化恢复、拖动、按钮/设置抽屉、125% DPI 均正常。
- 修复 P2-13/P2-19：面板外（原 30px/圆角外）不再拦截桌面点击（窗口区域裁剪天然解决）。
- 隐藏态 P2/P4 不回归（`tools/verify_runtime.py hidden`，P2 <0.5%、稳态 RSS <320MB）。
- pytest 94 / ui_smoke 15 / cargo test 10 全绿；20 次可见启动压测 0 hang。
- 发布 v0.1.1：NSIS 重打包、GitHub Release 附 exe + sha256、README/Release Notes 说明。

### 10.5 与现有条目的关系

- **P2-13/P2-19（透明边吞点击）**：方案 A 的窗口区域裁剪同时修复，无需再单独做 WM_NCHITTEST。
- **P2-2/P2-45（125% 半像素、阴影被 30px 边裁切）**：裁剪后阴影不再可见，需接受“面板内阴影/边框”方案或同步调整设计；半像素问题仍可另修。
- **P2-43/P2-44（透明效果关闭/旧 WebView2 无 acrylic）**：方案 A 的 `data-acrylic=off` 样式是同一降级路径。
- **P3 全屏/多屏/DPI 矩阵**：裁剪后窗口矩形仍为 780×440，定位逻辑需按面板矩形 clamp，纳入 DPI 矩阵回归。

### 10.6 实施/发布顺序（确认后）

1. 修改 `src-tauri/src/platform.rs`、`panel.rs`、`state.rs`/`commands.rs`、`tauri.conf.json`（acrylic 开关）、`ui/app.js`/`styles.css`。
2. 本机验收 §10.4（含三背景、透明效果 0/1、重启、DPI、点击穿透、20 次启动、隐藏 P2/P4）。
3. 版本号 0.1.1；更新 README、`docs/release-notes-v0.1.1.md`、PROGRESS。
4. `scripts/build.ps1` 生成 NSIS + sha256；提交并推送 `wh23423/PeripheralBattery`；创建 GitHub Release `v0.1.1`。

