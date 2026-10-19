# 外壳与前端独立审计报告（Tauri 2 shell + ui/）

- 审计对象：`src-tauri/`（Rust ~2.9k 行）、`ui/`（bridge.js / app.js / styles.css / index.html）、`tauri.conf.json`、`capabilities/`、`scripts/build*.ps1`
- 对照契约：`BUILD_PLAN.md` v1.1（P1–P10、§1–§6、§11 十八条处置）、`protocol.md` v1（§1/§2/§4/§5/§6）
- 审计方式：只读源码逐行审阅 + `cargo check`（通过，exit 0）+ `node tools/ui_smoke.js`（10/10 通过）+ 3 次受控短时运行 debug 外壳（隔离 `%LOCALAPPDATA%`、唯一 `PB_SINGLE_INSTANCE_NAME`、`PB_QUIT_AFTER_MS`）+ 独立 Win32 行为探针。**未修改** `ui/`、`src-tauri/`、`collector/`、`scripts/`、`dist/`。
- 运行环境：Windows build 26300（Win11），125% DPI，工作区 2560×1380，WebView2 155。
- 结论：**未发现 P0**。发现 **2 个 P1（必须修）**、**4 个 P2（必须修/建议修）**、约 18 个 P3。协议 §4 的命令/事件名与 payload 类型基本逐条一致；主要问题集中在「最小化生命周期」「隐藏态托盘同步」「托盘菜单入口」「打包数据目录」。
- 证据：`design/audit/evidence/` 下保存了 3 次运行日志、`ui-smoke.log`、`cargo-check.log`；引用的行号均指当前源码。

> 复现命令（已跑完，勿在 Lead 测量期间重跑）：
> `cargo check`（exit 0）；`node tools/ui_smoke.js`（10 passed, 0 failed；测试脚本自身刷新了 `design/verify/ui-*.png` 截图产物）。
> 运行外壳：`PB_SINGLE_INSTANCE_NAME=audit-shell-N`、`PB_LOG_LEVEL=debug`、`LOCALAPPDATA=%TEMP%\pb-audit-shellN`，配合 `PB_SHOW_ON_START=1` / `PB_HIDE_AFTER_MS` / `PB_QUIT_AFTER_MS`。

---

## 严重度定义

| 级别 | 含义 |
|---|---|
| P0 | 崩溃/数据丢失/安全问题/核心功能完全不可用 |
| P1 | 主要功能缺陷：默认路径可用性受损，用户可稳定复现，必须修 |
| P2 | 明确 bug 或契约偏差，影响体验/稳健性，建议本轮修 |
| P3 | 边界/加固/文档/测试缺口，可排期 |

---

# 一、P1（必须修）

## P1-1 最小化后「面板永久不可恢复」：show 不 unminimize，skipTaskbar 又断了任务栏恢复入口

- 位置：`src-tauri/src/commands.rs:82-88`（`window_action` minimize）、`src-tauri/src/panel.rs:145-168`（`show_panel` 无 unminimize）、`src-tauri/src/panel.rs:96-98`（`position_panel` 用 `outer_size()`）、`src-tauri/tauri.conf.json:23`（`skipTaskbar: true`）
- 证据（`design/audit/evidence/run3-minimize-then-show.log`）：
  - 00:48:55.795 正常 show；00:48:57.2 设备更新；外部 `SW_MINIMIZE` 后再次用第二实例触发 `show_panel`：
    `panel position origin=(2085,1328) size=199x34` → `window shown origin=(-32000,-32000) size=199x34`。
  - 独立 Win32 探针（notepad）：`SW_MINIMIZE → SW_HIDE → SW_SHOW` 后 `IsIconic=True`，只有 `SW_RESTORE` 才 `IsIconic=False`。
- 实际：`show_panel` 只调 `win.show()`（`ShowWindow(SW_SHOW)`），不会清除 `WindowFlags::MINIMIZED`；被最小化的窗口仍然 iconic，位置为 -32000，用户看不到面板。又因 `skipTaskbar:true`，任务栏没有可点击恢复的按钮。托盘左键只是 `toggle_panel`（`panel.rs:191-198`），hide/show 循环都不会 restore → **面板再也打不开，只能退出重开**。
- 期望：最小化应等价于「隐藏四步」（这是无任务栏入口的托盘应用），或在 `show_panel` 里先 `unminimize()` 再定位/显示；`position_panel` 不能用最小化态的 `outer_size()`（199×34）做定位。
- 建议修法（任一）：
  1. `commands.rs` 的 `"minimize"` 改为 `panel::hide_panel(&app, "minimize", true)`（同时获得 WAAPI/rAF/IsVisible 暂停，符合 §3.1；本应用本来也没有任务栏入口）；
  2. 或 `show_panel` 在 `position_panel` 前：`if win.is_minimized().unwrap_or(false) { let _ = win.unminimize(); }`，并把尺寸改为固定逻辑尺寸 `LogicalSize::new(780.0, 440.0).to_physical(scale)`；
  3. `tauri.conf.json` `minimizable:false` 并移除最小化按钮（若产品不想要最小化）。
- 附带：同一路径下「最小化」不会发 `window-visibility{hidden:true}`，前端四步不执行；若焦点恰好仍在本窗口（`schedule_auto_hide` 的 foreground==self 分支会跳过隐藏，见 `panel.rs:247-256`），隐藏态 CPU 指标也会被破坏。

## P1-2 默认（隐藏）模式下托盘图标错误且**永不更新**：启动即显示「离线」，隐藏期间数据变化不推 `set_tray_state`

- 位置：`ui/app.js:637-669`（`syncTray` 只在 `render()` 里被调用）、`ui/app.js:786-791`（hidden 时 `render` 直接 return）、`ui/app.js:999-1000`（hidden 时 `handleCollectorState` 直接 return）、`src-tauri/src/tray.rs:42-48`（离线/未知图标）
- 证据（`design/audit/evidence/run2-hidden-start-single-instance.log`）：
  - 00:47:56.418 `command set_tray_state level=None charging=false online=false kind=` → `托盘图标更新 tooltip=外设电量 · 离线`（此时只是还没数据）；
  - 00:47:56.423 `window startup hidden`（默认启动就是隐藏）；
  - 00:47:57.833 `collector device key=Some("bluetooth:...")`（94%）——**之后再无任何 `set_tray_state`**，直到 00:48:04 被第二实例激活显示面板才在 00:48:04.183 更新为 94%。
- 实际：托盘是隐藏态的**唯一**环境指示器，但默认启动后它长期显示「离线/未知」，隐藏期间电量变化完全不反映；只有用户手动打开面板才会刷新（打开后再次隐藏又会冻结）。
- 期望：托盘状态应在数据语义变化时更新，与 DOM 渲染解耦；首次数据到达前不要下发「offline」。
- 建议修法：在 `handleCollectorMessage` / `handleCollectorState` 的 hidden 分支里调用轻量 `syncTray()`（不是 `render()`；`syncTray` 本身只做 `bridge.call('set_tray_state')`，不写 DOM，不违反 §3.1 第 3 条）；或把 `syncTray` 拆出 render 并在 `resetSession`/`device`/`gone`/`tick` 后调用。启动时若 `state.devices.size===0` 且从未 ready，暂不下发离线状态（保留 Rust 默认「未知」图标）。

---

# 二、P2（必须修/建议修）

## P2-1 托盘菜单「设置」在隐藏态点了没反应（不显示面板）

- 位置：`src-tauri/src/tray.rs:267-270`（仅 `emit tray-action`）、`ui/app.js:1197-1203`（`openDrawer()`）、`src-tauri/src/commands.rs:78-92`（`window_action` 只有 hide/minimize/quit，没有 show）
- 证据：静态调用链；Rust 端 `settings` 分支不调用 `panel::show_panel`，前端也没有任何「显示窗口」的命令。`openDrawer` 只改 `.drawer`/`aria-*`/scrim 的 DOM。
- 实际：应用默认隐藏，用户在托盘右键→设置：抽屉在**隐藏窗口**里被打开，屏幕无任何反馈；下次打开面板才会看到抽屉已开。
- 期望：托盘「设置」应先显示面板（`show_panel` 后再发 `tray-action`），或新增 `window_action:"show"`。
- 建议：Rust `tray.rs` settings 分支改为 `panel::show_panel(app, "tray-settings")` 之后再 emit；如保留前端处理，则在 `handleTrayAction('settings')` 里先 `bridge.call('window_action',{action:'show'})`（需新增 action）。
- 同时注意：`pin` 分支也只 emit，不 toggle Rust 端 pinned（`tray.rs:263-266`）；WebView 未就绪/崩溃时「固定」菜单静默失效，建议 Rust 侧直接 toggle 并 emit `app-state`（见 P3-6）。

## P2-2 应用数据目录与 NSIS 安装目录重合

- 位置：`src-tauri/src/paths.rs:33`（数据目录 `%LOCALAPPDATA%\PeripheralBattery`）+ `tauri.conf.json:50-53`（`installMode: currentUser`）；生成的安装脚本证据：`src-tauri/target/release/nsis/x64/installer.nsi:36` `PRODUCTNAME "PeripheralBattery"`、`:514` `StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"`。
- 实际：安装目录 = `%LOCALAPPDATA%\PeripheralBattery` = 应用数据目录。`config.json / aliases.json / history.json / logs\ / diagnostics\ / collector.pid.json` 全部落在程序安装目录里，与 `peripheral-battery.exe / collector\ / uninstall.exe` 混放。
- 风险：卸载器 `RMDir "$INSTDIR"`（installer.nsi:922，非递归）会因用户数据残留而失败，安装目录永远删不干净；「Delete app data」勾选只清 `${BUNDLEID}` 目录（installer.nsi:984-985），清不到本应用数据；若后续安装/更新策略改为清理安装目录或用户自选目录，设置会丢；也使 §6.3 的数据目录约定与安装目录复用（程序与数据混放）。
- 期望：数据目录独立，如 `%LOCALAPPDATA%\PeripheralBattery\data\`（或 `\com.peripheralbattery.app\`），并与安装目录区分。
- 建议：改 `paths.rs` 的 `data_dir`；或改用 `installMode: perMachine` / 改 `productName`；同时更新 `protocol.md §6` 与迁移旧数据。

## P2-3 隐藏期间仍有 DOM/WAAPI 写入（破坏「隐藏不写 DOM/停动画」约束）

- 位置：`ui/app.js:1168-1179`（`spinRefresh` WAAPI，无 `state.hidden` 判断）、`ui/app.js:1250-1269`（`setDrawer` 写 class/aria/inert）、`ui/app.js:1197-1209`（`handleTrayAction` 调用上述两者 + `togglePin→updatePinUI` `app.js:743-747`）
- 证据：tray-action 在窗口隐藏时仍会到达前端（Rust emit 与可见性无关）；`enterHidden` 只对**已存在**的动画调 `pause()`（`app.js:1104-1112`），之后新建的 WAAPI 不受 `body.is-hidden` 的 CSS 规则约束（CSS 只管 CSS 动画）。
- 实际：隐藏时点托盘「刷新」会创建 600ms WAAPI；点「设置」会写抽屉 DOM；点「固定」会写 `aria-pressed`/checkbox。`D.domWritesWhileHidden` 仅统计数据驱动路径，现有 smoke 覆盖不到（见 §五 测试缺口）。
- 期望：`state.hidden` 期间用户动作只更新状态，不写 DOM；WAAPI 创建前判断 hidden，或统一在 `paint()`/`requestAnimationFrame` 恢复时补做。
- 建议：`spinRefresh`/`setDrawer`/`updatePinUI` 加 hidden 守卫并置 `state.dirty`；或引入统一的 `scheduleUi(fn)` 在 hidden 时排队到 resume。

## P2-4 `config.json` 的 Rust↔collector 读改写竞态会覆盖采集层设置

- 位置：`src-tauri/src/config.rs:20-36`（启动时整份读入并保留 `extra`）、`src-tauri/src/state.rs:139-152`（`set_pinned` 在锁内整份写回）、`collector/settings.py:205`（collector 也会写同一文件，如收到 `set` 命令）
- 实际：Rust 进程启动时把 `extra` 读进内存，之后任何 `pinned` 变更都会用**启动时的快照**整份覆盖；若期间 collector 写入了新的 poll/threshold/rename 设置，会被静默丢掉。当前 UI 尚未暴露 rename/hidden，故是**潜伏**问题，但协议 §2 已定义 `set` 命令。
- 期望：写前重新读盘合并（或双文件/文件锁）；至少保留 collector 侧最新值。
- 建议：`set_pinned` 在写前重新 `AppConfig::load` 并合并 `extra`（加进程内互斥 + 短重试）；长期建议 Rust 只写 `pinned` 键（JSON patch）或改用独立 `app.json`。

---

# 三、P3（建议修 / 可接受但需知会）

## 生命周期与并发（Rust）

### P3-1 `cleanup_residual` 的「按安装路径清理」实际永远不匹配（`\\?\` 前缀）
- 位置：`src-tauri/src/sidecar.rs:747-753`（`resource_dir()` 来自 `current_exe()`，Windows 上带 `\\?\` verbatim 前缀，运行日志已见 `sidecar spawn path=\\?\D:\...`）、`:178-180`（`normalize_path` 只做 `/`→`\`+lowercase）、`:790-804`（用 `normalize_path(expected) == normalize_path(info.exe)`）。
- 证据：运行时日志 `sidecar spawn path=\\?\D:\desktop\...`；独立探针确认 `QueryFullProcessImageNameW(...,0,...)` 返回 `D:\...`（无 `\\?\`）。两者永不相等 → 该 secondary 清理分支是死代码；残留清理只剩 `collector.pid.json` 一条路（Job Object 崩溃兜底仍然有效）。
- 建议：`normalize_path` 先剥离 `\\?\` / `\??\` 前缀并统一 UNC 形态；或用 `std::fs::canonicalize` 前先转换。

### P3-2 `cleanup_residual` 会互杀「同安装路径」的另一个实例的 collector（测试/单实例失效时）
- 位置：`src-tauri/src/sidecar.rs:769-805`、`src-tauri/src/main.rs:42-69`（`PB_SINGLE_INSTANCE_NAME` 可绕过单实例）
- 场景：Lead 建议并行测试时用唯一 `PB_SINGLE_INSTANCE_NAME` —— 两个实例因此在同一 `target/debug/collector/collector.exe` 路径上，后启动者 `cleanup_residual` 会 `kill_process` 前者的 collector。生产上只有 mutex 创建失败（`AcquireResult::Disabled`）才会遇到。
- 建议：清理时排除当前进程的「兄弟实例」很难判断，至少把 sweep 限制在「本进程 pid 文件记录」+「App 专属 mutex 持有」场景；测试环境用独立 `PB_PROJECT_ROOT`/resource 目录。

### P3-3 Job Object 在 spawn 之后才 Assign：早产子进程有窗口逃逸
- 位置：`src-tauri/src/sidecar.rs:306-342`（先 `cmd.spawn()`，再 `JobHandle::create()/assign()`）。
- 影响：collector 在「spawn→assign」之间（毫秒级）自行创建的 PowerShell/helper 不会进入 Job；若 app 恰在此窗口崩溃，P10「孤儿进程 0」不成立。BUILD_PLAN §1.3 要求 Job 绑定。
- 建议：`CREATE_SUSPENDED` 启动 → `AssignProcessToJobObject` → `ResumeThread`；或用 `PROC_THREAD_ATTRIBUTE_JOB_LIST`（Win10+）。

### P3-4 restart / 快速连点重启未串行化
- 位置：`src-tauri/src/sidecar.rs:383-393`（每次 `collector_restart` 都起一个线程 `shutdown→start`），`start()` 内部再 `shutdown(300ms,false)`（`:270-273`）。
- 影响：并发 restart 可短暂出现两个 collector、pid 文件后写覆盖先写（旧 pid 可能覆盖新 pid），虽由 Job close 兜底不至于孤儿，但状态事件抖动；`collector_restart` 的返回值只是「线程创建成功」，与协议 `boolean` 的语义不符（应在真正重启完成后返回）。
- 建议：`restart` 用 `AtomicBool/Mutex` 串行化；或在 `restart` 返回前 join/等待新 `starting`。

### P3-5 stdout 线程缺少 generation 防护，重启瞬间可能转发旧进程残留行
- 位置：`src-tauri/src/sidecar.rs:430-455`（`stdout_loop` 无 pid/gen 参数）、`:490-526`（`on_line` 不校验 pid），对比 `:588-663`（wait/exit 有 gen 校验）。
- 影响：旧 collector 退出前 buffer 中未读的 `hello/device` 可能在新进程启动后才被处理，短暂把 `collector-state` 推成 ready + 旧 session，前端可能先 reset 到旧会话；属瞬时抖动。
- 建议：stdout 读取闭包带上 `(pid, gen)`，`on_line` 校验当前 generation 再转发。

### P3-6 单实例：`Global\` 命名对象 + 永远走当前会话枚举，跨用户会静默失败
- 位置：`src-tauri/src/single_instance.rs:23`（`Global\PeripheralBattery.single`）、`:69-99`、`:102-113`；`platform.rs:216-237`（`EnumWindows` 仅当前会话）。
- 影响：多用户（Fast User Switching）下第二个用户启动时 mutex 已存在 → `OpenEventW` 很可能因跨会话默认 DACL 失败 → `ShowWindow` 兜底也找不到窗口 → 新进程直接退出且用户无任何提示；而应用数据本就是 per-user 的（`%LOCALAPPDATA%`），单实例语义应为 per-session。
- 建议：默认改用 `Local\PeripheralBattery.single`（测试可覆盖），或按用户 SID 生成名字；失败路径给一次 MessageBox/托盘提示。

### P3-7 进程退出/命令路径上的阻塞点
- `sidecar.rs:397-420` `send_line`：`write_all` 无超时；collector 若卡死导致 stdin 管道满，主线程上的 `collector_send`/`shutdown`（`main.rs:146-159` 在事件循环线程调用）可能无界阻塞 → UI/退出卡死。建议写入前检查 `length ≤ 1 MiB`、拒绝 `\r\n`，并考虑隔离到工作线程/加超时。
- `commands.rs:38-41`/`state.rs:193-212`：`request_quit` 依赖后台线程，正常；但 `RunEvent::ExitRequested` 在主线程同步做 2s 握手（+ 可能的 1.5s terminate），退出期间窗口冻结，属可接受但可优化（先隐藏+异步）。

### P3-8 硬编码开发者 Python 路径
- 位置：`src-tauri/src/sidecar.rs:859-861`（`C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe`）。
- 影响：资源目录缺失时回退链会尝试开发者本机路径；打包机/他人机器上不可能命中，最后落到 `python`（PATH）。属卫生问题（也是信息泄漏），建议只保留 `.venv` 与 `PB_PYTHON`/`python`。

### P3-9 `build` 恒为 `unknown`
- 位置：`src-tauri/build.rs:5-15`；本仓库不是 git 仓库（`git rev-parse` 失败）。
- 影响：`hello.build` / `app-state.build` 诊断字段永远是 `unknown`，P9 诊断与「版本握手/过期」排查能力下降（protocol §1.2 要求 `<git-sha|unknown>`，允许但无信息量）。
- 建议：用 `APP_BUILD` 环境变量/构建号/文件哈希兜底。

## 协议一致性

### P3-10 `ui_ready` 的 show 语义与 protocol 文本不一致；前端忽略返回值
- 位置：`protocol.md:124`「前端首帧完成；Rust 此时才显示窗口」；实现 `commands.rs:11-26` 默认 `show=false` 并调用 `mark_startup_hidden`（托盘常驻，见 `main.rs:124-126`）；前端 `bridge.ready()` 不消费 `{show}`（`ui/bridge.js:331-334`）。
- 实际：默认启动不显示面板（符合产品「只驻留托盘」的注释），但与协议文字相悖；若未来依赖 `show` 做逻辑会踩坑。建议改文档明确「ui_ready 返回是否需要显示」，或让前端根据返回值驱动首帧。
- 相关：`app-state.build` 是**应用** build，而前端把它存进 `state.build` 且 UI 标签是「采集器构建」（`ui/app.js:1017`、`index.html:126`），hello 到达后又被采集器 build 覆盖；建议两个字段分开（`collectorBuild`/`appBuild`）。

### P3-11 `collector-message` 在 JSON 校验前就转发（非 JSON 也会发给前端）
- 位置：`src-tauri/src/sidecar.rs:490-503`（先 `emit`，后 `serde_json::from_str`）。
- 协议 §4.1 写「string（原始 JSON 行）」；实际前端会收到任意文本行（非 JSON 时前端 `D.badEvents++` 并丢弃，见 `ui/app.js:919-925`）。当前无害；建议解析成功后再转发（或在协议里明确「原始行，含非 JSON」）。

### P3-12 `collector_send` 不校验行内容/长度
- 位置：`src-tauri/src/sidecar.rs:397-420`；`ui/commands.rs:30-34`。
- 协议 §0 要求「单行 ≤ 1 MiB」双向限长；出站方向无上限、且 `trim_end_matches` 只删尾部换行，**参数中的内嵌 `\n` 会被原样写入** → 一条 invoke 可注入多条 NDJSON 命令。当前前端只发 `refresge/get` 且无 XSS，属加固项；建议 `line.len() ≤ 1MiB && !line.contains(['\n','\r'])`，并尝试 `serde_json` 校验。
- 另：`commands.rs:96-98` `collector_status` 与 `sidecar.rs:247-249` `state()` 直接返回内部状态克隆，OK。

### P3-13 前端对异常字段的容错会产生 `NaN` 文案
- 位置：`ui/app.js:843-858`（`left_s: Math.max(0, Math.round(Number(raw.left_s)))` 可得到 `NaN`）、`:235-248`（`humanLeft(NaN)` 输出「约 NaN 天」）；`level` 的 `NaN` 会被 `levelOf` 兜底为 null（好）。
- 协议要求 number|null；collector 正常不会发错，但建议 `normalizeDevice` 对非有限数统一转 null。

### P3-14 session/seq 边界
- `ui/app.js:935`：同 session 的重复 hello 被忽略（合理），但若 hello 因监听注册晚而丢失、session 由 `tick` 学到，后到的 hello 会被当作重复丢弃，`helloReceived` 永远为 false；`build/capabilities` 丢失。当前靠 `get snapshot` 兜底（`app.js:990-998`）恢复数据，可接受；建议以 `(session, seq=0)` 或 `build` 判断「是否已应用 hello」。
- `ui/app.js:896-917`：`resetSession` 固定 `state.seq = 0`，未使用 `hello.seq`（协议固定 0，可接受）。
- `ui/app.js:860-883`：`replaces` 的目标 key 已存在时会把 `state.order` 写成重复 key（如 K1→K2 且 K2 已在列表），后续 `indexOf/splice` 只处理首个，残留幽灵顺序项；当前渲染无可见影响，建议去重。

## 前端渲染 / 性能 / 可访问性

### P3-15 主卡切换会重建行节点（与 §4.4「行只创建一次」有偏差）
- 位置：`ui/app.js:497-533`（`syncRows` 删除/创建）、`:786-818`（render 把 hero 从列表剔除、把旧 hero 加回列表）。
- 实际：普通 level 更新行节点复用（smoke G3 已证），但**hero 换人时**（例如另一设备掉电成为最低）旧 hero 行被新建、新 hero 行被删除；若这两行正处于 hover/focus，会丢失。当前等级更新频繁时可能出现焦点跳动（仅键盘用户明显）。建议 hero/list 共享同一行 DOM（用 class 切换主卡），或对 hero 行做显式 focus 迁移。

### P3-16 过渡/动画仍有非 transform/opacity 项（偏离 BUILD_PLAN §2.3）
- 位置：`ui/styles.css:457`（`.ring .fill { transition: stroke-dashoffset .7s, stroke .3s }` → 每帧 paint）、`:869-870`（`.all-btn` transition 含 `color/background-color/border-color/text-shadow`）、`:677`（`.banner-btn` background-color）、`.ctrl`/`.drawer` 的 color/opacity 类。
- 影响：圆环每 0.7s 一次小面积 paint、按钮悬停 text-shadow 重绘；相对 §2.3「动画只走 transform/opacity」是偏差，但面积小、非常驻，P3。建议圆环改用「静态 track + 旋转/缩放光层」或接受并在文档注明例外。
- 其余正项（已核）：生产 0 个 `backdrop-filter`（`styles.css:192-210` 全部在 `data-effects="css"` 下）、`.specular` 在 `.panel` 外（`index.html:13-15`）、无 `mix-blend-mode`、`will-change` 仅 tracking/intro、`contain: layout paint`、pulse/ripple/charge 均 transform/opacity、无 box-shadow transition、`prefers-reduced-motion`/`prefers-reduced-transparency` 均有降级（`styles.css:1186-1202`、`:220-224`）。

### P3-17 交互细节
- `index.html:85-88`「全部设备」按钮实际只把焦点移到第一行，或在无设备时打开设置（`ui/app.js:1316-1320`）——与标签不符，建议改名/实现展开视图/移除。
- 托盘阈值/配色与面板不一致：托盘 50/20 分档（`tray.rs:42-47`），面板 35/threshold（`ui/app.js:262-268`），同一设备可能托盘绿、面板白/黄；建议统一 token。
- 用户点击某行后 `heroPinnedByUser=true` 且无法回到「自动最低电量」（`ui/app.js:1146-1155`）；建议长按/再次点击或设置项恢复自动。
- `schedule_auto_hide` 270ms / 抑制 450ms 与 §3.2（250ms）/§5.2（300ms）参数略有出入，可接受。
- 未实现 §5.2「全屏游戏 `SHQueryUserNotificationState` 判定，全屏时不主动弹出」；也未处理 `<select>`/IME 弹层焦点豁免（当前 UI 无 select，属未来风险）。见 `panel.rs:215-261`。
- 定位优先使用上次点击记录的 `state.tray_rect`（`panel.rs:99-108`），任务栏移动后可能用到过期矩形；建议每次用 `tray.rect()` 或校验屏幕数/工作区后失效。
- 托盘图标尺寸使用应用窗口的 `scale_factor`（`tray.rs:164-168`），窗口在副屏（不同 DPI）时托盘（主屏）图标尺寸可能不对；建议用主显示器 scale。

### P3-18 可访问性
- `index.html:72` `#subtitle` 为 `aria-live="polite"` 且每次 render 都写入含时分的新文案 → 每次数据更新都会朗读一次（噪音大）；建议只对内容变化/关键状态用 live region。
- toast（`app.js:1049-1069`）是 `div` + click，不能键盘聚焦/关闭；`aria-live` 容器已够读屏，但键盘用户无法提前关闭。
- 抽屉打开时背景内容未 `inert`/`aria-hidden`（只有抽屉关闭时自身 inert，`app.js:1250-1269`），读屏仍可遍历背景。
- `#hero` 的键盘 focus（`app.js:1151-1153`）无专门的 `:focus-visible` 样式（全局规则可能覆盖，需实测）。
- 正项：抽屉 Tab 焦点环（`app.js:1274-1284`）、Esc 关闭、`aria-expanded/aria-pressed/aria-hidden` 同步、行 `role=button`+Enter/Space、`[hidden]` 处理、控制按钮 30px ≥ 24px 目标、对比度（白 78%/86% on 深烟熏底）静态估算 > 4.5:1，但**§7 要求的「白/黑/花色壁纸三张截图断言」未执行**（tests/design 中未见），建议补。

## 打包 / 配置 / 安全

### P3-19 打包其余项
- `bundle.resources` 映射**验证通过**：`target/release/collector/` 含 `collector.exe + _internal`（22 MB），生成的 NSIS 脚本含全部 131 个 `_internal` 文件 + `collector\collector.exe`（`installer.nsi:657-724`），安装后 `resource_dir()\collector\collector.exe` 与 `sidecar.rs:828-840` 启动路径一致；`cargo check` 通过；`release-build.log` 显示 NSIS 打包成功（9.66 MiB）。这是 §11-#2 的正确落地。
- **未配置 updater**：`Cargo.toml` 无 `tauri-plugin-updater`、`tauri.conf.json` 无 `plugins.updater`；BUILD_PLAN §6.2 / §11-#18 的「shutdown→等退出→updater→重启校验 build」链路在应用内不存在，P10「更新后孤儿」无法按设计验证。建议 M3 补，或明确推迟并记录。
- WebView2 安装模式未配置（默认 `downloadBootstrapper`）：离线/受限网络机器安装可能需要联网拉 WebView2；建议按分发场景明确 `embedBootstrapper`/`offlineInstaller`。
- `bundle.icon` 含 `icon.icns`（macOS 资产）多余但无害。
- `build.ps1`：`-SkipCollector` 时检查了 `dist\collector` 存在（会 throw）；但**默认路径**在 `collector\__main__.py` 缺失时只 WARNING 就继续 `tauri build`，未检查 `dist\collector` 是否存在/非空（`scripts/build.ps1:39-46`）——此时 Tauri 打包会缺 collector 或直接失败，建议补同样的存在性检查。
- 应用可执行名 `peripheral-battery.exe` 与单实例 `find_existing_window` 的 exe 匹配（`platform.rs:202-207`）一致；`MAINBINARYNAME` 也是 `peripheral-battery`，正项。

### P3-20 权限过宽 / 测试钩子暴露
- `capabilities/default.json:6-31` 授予了大量 `core:window:*`（含 `set-size/position/always-on-top` 等），但前端只通过自定义命令操作窗口；建议最小权限（保留 `core:default`/`core:event` 即可）。
- `ui/bridge.js:461-466` 在**生产 Tauri 页面**无条件暴露 `window.__pbMock`（可注入伪造 hello/device/gone）；`withGlobalTauri:true` 也把完整 Tauri API 暴露给页面（`tauri.conf.json:10`）。当前无 XSS 面（设备名/文案全走 `textContent`，`innerHTML` 只用于常量 SVG），风险低；建议 release 下 `delete window.__pbMock` 或仅在 `?mock` 时创建。

### P3-21 日志/文件
- `logging.rs:76-102` 轮转：`keep=1` 时（仅 fallback 场景）rename 分支不生效但会把 `size` 归零，文件可无界增长；正常路径 `keep=5` 正确。建议小修。
- `logging.rs:149-177` 极端兜底里有 `expect("temp log")`，启动期 panic 风险很低但存在。
- `APP_BUILD`/`app.log` 5×2MB、`collector.log` 5×2MB 均符合 §6.3；PID 文件小、退出删除；未发现日志无限增长（除上述 keep=1 分支）。
- collector stderr 线程与 app logger 各自打开同名轮转文件；双实例（测试）时可能竞争 rename（Windows 上 Rust `OpenOptions` 共享 DELETE，通常可 rename，但两进程各自计数会抖动）。
- 进程 kill 仅按 PID 文件 `creation`/exe 名后缀校验（`sidecar.rs:769-788`）；`creation==0` 时退化为只看名字，理论上可误杀同用户同名进程（同用户信任边界内，P3 提示）。

---

# 四、protocol.md §4 逐条一致性核对

## 4.1 Rust → 前端事件

| 事件名 | 契约 payload | 实现 | 结论 |
|---|---|---|---|
| `collector-message` | string（原始 JSON 行） | `sidecar.rs:495` `emit(line.to_string())` | ✅ 名称/类型一致；⚠️ 非 JSON 行也转发（P3-11），`trim()` 去掉首尾空白 |
| `collector-state` | `{state, pid?, code?, message?, protocol?, session?}` | `sidecar.rs:40-66` 结构与 `skip_serializing_if` 一致 | ✅ 全字段/类型一致；`starting/ready/exited/error` 四态均实际出现（运行日志） |
| `app-state` | `{pinned, platform:{windows11,build}, build, version}` | `state.rs:121-137` | ✅ 字段/类型一致；`build` 语义前端混用（P3-10） |
| `tray-action` | `{action:"refresh"|"settings"|"quit"|"toggle-pin"}` | `tray.rs:255-277` | ✅ 四种均会 emit；`settings` 隐藏态无效（P2-1），`toggle-pin` 依赖前端（P2-1 附注） |
| `window-visibility` | `{hidden:boolean}`，顺序 hide:IsVisible(F)→event→hide；show:show+IsVisible(T)→event | `panel.rs:181-186` / `:153-159`；`mark_startup_hidden` `:201-213` | ✅ 名称/类型/顺序与协议 §4.3 完全一致（运行日志 `IsVisible(true/false) -> S_OK` 佐证） |

## 4.2 前端 → Rust 命令

| 命令 | 契约 | 实现 | 结论 |
|---|---|---|---|
| `ui_ready` | 无参 / `{show:bool}` | `commands.rs:11-26` | ✅ 返回 `{show}`；⚠️ 默认 `show=false`（P3-10），前端不消费返回值 |
| `collector_send` | `{line:string}` / boolean | `commands.rs:30-34` | ✅ 返回写入结果；⚠️ 无长度/换行校验（P3-12） |
| `collector_restart` | 无参 / boolean | `commands.rs:38-41` | ✅；语义偏差：返回线程创建成功而非重启完成（P3-4） |
| `set_pinned` | `{pinned:bool}` / boolean | `commands.rs:45-48` + `state.rs:139-152` | ✅ 持久化到 config；⚠️ 保存失败仍返回 true（建议改返回值） |
| `set_tray_state` | `{level:number|null, charging:boolean, online:boolean, kind:string}` / 无 | `commands.rs:52-74` | ✅ `Option<i64>`/bool/String，返回 `()`→null；实际调用日志吻合 |
| `window_action` | `{action:"hide"|"minimize"|"quit"}` / 无 | `commands.rs:78-92` | ✅ 三种均实现；❌ `minimize` 导致 P1-1 |
| `collector_status` | 无参 / 当前 collector-state | `commands.rs:96-98` | ✅ |

前端调用覆盖：7 个命令全部有调用点（`bridge.start` 的 `collector_status`；`ui_ready`；`collector_send`（refresh/get）；`collector_restart`；`set_pinned`；`set_tray_state`；`window_action`）；5 个事件全部有 `listen` 且监听注册先于 `ui_ready`（`bridge.js:302-315`，`app.js:1301-1307`），`hello 早于监听` 的窗口由 `collector_status→ready→get snapshot` 兜底（`app.js:990-998`）。✅

## protocol §1 / §5 行为抽查（前端状态机）

| 契约 | 实现 | 结论 |
|---|---|---|
| `(session,seq)` 排序、乱序丢弃 | `app.js:941-951` 严格 `seq<=state.seq` 丢弃 | ✅（smoke G4） |
| session 变化全量重置、不重播入场 | `app.js:896-917`；`body.intro` 不重置 | ✅（smoke G4） |
| `replaces` 原地 morph | `app.js:860-883` 迁移 rowMap/order/heroKey + `morphFrom` | ✅（smoke G4） |
| `gone` 只由全局 tick 触发（collector 侧） | 前端按 `gone` 删除，tick 全量对齐 | ✅ |
| `level=null` 空心环 + approx/未知、不判红 | `app.js:220-224,260-269,286-290,544-563` | ✅（smoke G2） |
| `alert` 展示 | `app.js:972-975,1028-1045` | ✅；隐藏期排队、恢复补播 |
| 协议版本校验 | Rust `sidecar.rs:556-568` + 前端 `app.js:927-938` | ✅（Lead 日志 `got=2 expected=1` → error「采集器版本过期」） |
| 5s 未收到 hello → error | `sidecar.rs:593-613` | ✅ |
| sidecar 退出 → `collector-state.exited` | `sidecar.rs:615-663` | ✅（运行日志 code=0） |

---

# 五、BUILD_PLAN 关键条目对照（§2/§3/§5/§6/§11）

| 条目 | 实现 | 结论 |
|---|---|---|
| §2.1 生产 0 backdrop-filter | `styles.css:192-210` 全部限定 `html[data-effects="css"]` | ✅（smoke G5） |
| §2.2 specular 在面板外、无 blend、transform-only、will-change 按需 | `index.html:13-15`、`styles.css:90-116`、`app.js:1216-1230,1341-1347` | ✅ |
| §2.4 box-shadow 不参与 transition、pulse/涟漪/呼吸改造 | 全文无 box-shadow transition；PNG 涟漪/transform；呼吸只动 opacity | ✅（除 P3-16 少量 paint 过渡） |
| §3.1 隐藏四步 + IsVisible | `app.js:1096-1134`；`platform.rs:424-449`；顺序按协议 | ✅（smoke G6 + 运行日志 S_OK）；⚠️ 最小化路径未走四步（P1-1）；托盘动作会写 DOM（P2-3） |
| §3.2 托盘静态图标/位置/失焦抑制/GetForegroundWindow | `tray.rs:128-140,224-253`；`panel.rs:215-261` | ✅；⚠️ 托盘状态同步缺失（P1-2）、阈值不一致（P3-17）、隐藏时「设置」无效（P2-1） |
| §5.1 780×440、DWM 圆角、Win10 回退、scaleFactorChanged | `tauri.conf.json:15-16`；`panel.rs:32-57,283-298`；运行日志 975×550@125%=780×440 DIP、DWM hresult=0 | ✅ |
| §5.2 pinned 持久化、失焦弹层、全屏游戏 | pinned ✅（config）；全屏游戏/select 弹层 ⚠️ 未实现（P3-17） |
| §5.3 CSP 放行 IPC、无内联 style/script | `tauri.conf.json:33-35`；`index.html` 无内联；JS 仅 CSSOM 写入 | ✅（真实运行下 ui_ready/set_tray_state 均到达 Rust，IPC 未被 CSP 拦；smoke G7） |
| §6.1 resources（非 externalBin） | `tauri.conf.json:47-49`；安装脚本含完整 `_internal` | ✅ |
| §6.2 更新顺序 | 无 updater（P3-19）；shutdown 握手机制本身 ✅ | ⚠️ 未接线 |
| §6.3 路径/日志 | 路径 ✅（除 P2-2 与安装目录重合）；5×2MB 轮转 ✅ | ⚠️ |
| §11-#1/#2/#5/#6/#7/#8/#9/#10/#11/#15/#16 | 逐条核对：均已落地（详见上文与 smoke/运行日志） | ✅ |
| §11-#14/#17 | 进程树测量与三张壁纸对比度断言未在仓库测试中落地（Lead 侧人工测量除外） | 测试缺口 |
| §11-#18（更新/休眠/HID） | 更新未接线；休眠/HID 属 collector | 部分 |

---

# 六、必须修 / 建议修 / 可接受 清单

## 必须修（本轮）
1. **P1-1** 最小化后面板不可恢复（+ 最小化未走隐藏四步）。
2. **P1-2** 隐藏态托盘图标错误/陈旧（启动即「离线」，隐藏期间不更新 `set_tray_state`）。
3. **P2-1** 托盘「设置」在隐藏态无任何可见效果（Rust 需先 show_panel）。
4. **P2-2** 数据目录与安装目录重合（至少确认卸载/更新策略不会丢配置，并记录）。

## 建议修（本轮或紧邻里程碑）
5. P2-3 隐藏期托盘动作写 DOM/新建 WAAPI（补 hidden 守卫）。
6. P2-4 config.json RMW 竞态（写前重读合并）。
7. P3-1 `\\?\` 前缀导致 packaged collector 残留清理失效。
8. P3-3 Job Object 先 spawn 后 assign 的孤儿窗口（CREATE_SUSPENDED）。
9. P3-4 restart 串行化 + `collector_restart` 返回值语义。
10. P3-12 `collector_send` 换行/1MiB 校验。
11. P3-10/P3-11 协议文字与实现对齐（ui_ready.show、collector-message 非 JSON、build 字段语义）。
12. P3-15/P3-17 主卡切换行节点、托盘阈值、全屏/弹层豁免。
13. P3-19 updater 缺失 + build.ps1 缺 dist 检查。
14. P3-20 release 下移除 `__pbMock`、收敛 capabilities。

## 可接受（已核无问题，仅记录）
- 单实例互斥体+激活事件（正常运行日志 `signaled=true` → 面板显示）。
- Job Object `KILL_ON_JOB_CLOSE` + 退出握手（运行日志 `job active_processes=0`、`sidecar exit code=0` 顺序正确）。
- stdio 行限长 1 MiB、EOF 丢残片、UTF-8 lossy、stderr 独立线程 + 5×2MB 轮转。
- 托盘矩形为物理像素（运行日志 `(2164,1380 40x60)` 与 2560×1380 工作区吻合，无二次缩放）；缺矩形时 cursor 兜底 + clamp。
- `read_line_limited` 超限重新同步、`send_line` flush。
- CSP 下事件/命令真实可用；头像/涟漪 data URI 未被拦。
- 设备名/文案 XSS：全部 `textContent`/`setAttribute`；`innerHTML` 只拼接常量 SVG（`app.js:279-284`）。
- keyed diff 普通更新复用节点、hover/focus 保持（smoke G3）；level=null 不判红（G2）；隐藏期不渲染/不写数据 DOM、恢复渲染最新快照（G6）。
- 设置抽屉键盘导航/焦点回转/localStorage（smoke A2）。
- `prefers-reduced-motion` / `prefers-reduced-transparency` / `data-reduced-motion` 降级。
- 窗口 780×440 DIP、DWM 圆角、Win10 不透明回退、`scaleFactorChanged` 重设尺寸。
- 协议版本不匹配、spawn 失败、5s hello 超时、exited 四种错误态均能到达 UI（Lead `tauri-*.log` 已证）。
- 首帧时序：`boot render → bridge.start → ui_ready → show`，未见白闪；启动到托盘 ~216ms（P5 余量充足）。

---

# 七、测试覆盖缺口（建议补）

1. `tools/ui_smoke.js` 只测浏览器 mock + 模拟 CSP：**未覆盖**「隐藏态 tray 更新」（P1-2）、「最小化→显示」（P1-1）、「托盘 settings/pin」（P2-1）、「用户动作在 hidden 下的 DOM 写入」（P2-3）。
2. 未测真实 Tauri IPC（CSP 只看静态/HTTP 模拟）；建议增加一条 `tauri-driver`/WebDriver 或 `verify_runtime.py` 断言：hidden 时 `D.domWritesWhileHidden==0` 且 `set_tray_state` 仍更新。
3. 未测单实例激活、restart 连点、collector spawn 失败后重启恢复。
4. 未测打包产物：安装后从 `resource_dir/collector/collector.exe` 真实启动、卸载残留、升级后配置保留。
5. 未执行 §7 的对比度三壁纸断言与 §0 P1–P10 真机矩阵（Lead 侧另行测量）。

---

# 八、附：证据与运行记录

- `design/audit/evidence/run1-show-hide-quit.log`：`PB_SHOW_ON_START=1` 全流程（show → IsVisible S_OK → hello → device → hide(IsVisible false) → shutdown 握手 → job=0 → exit 0）。
- `design/audit/evidence/run2-hidden-start-single-instance.log`：默认隐藏启动 + 第二实例 `SetEvent` 激活；含 P1-2 关键证据（device 到达后无 `set_tray_state`）。
- `design/audit/evidence/run3-minimize-then-show.log`：P1-1 关键证据（`origin=(-32000,-32000) size=199x34`，show 后仍 iconic）。
- `design/audit/evidence/ui-smoke.log`：10 passed / 0 failed。
- `design/audit/evidence/cargo-check.log`：`Finished dev profile ... exit 0`。
- 打包：`design/verify/release-build.log`（release + NSIS 成功）；`src-tauri/target/release/nsis/x64/installer.nsi`（131 个 `_internal` 文件 + `collector.exe` 全量入包）。
- 说明：运行 `node tools/ui_smoke.js` 时，测试脚本按其自身逻辑刷新了 `design/verify/ui-*.png` 截图（测试产物）；`%TEMP%` 下的隔离数据目录与脚本未做清理（无副作用）。本次审计未改动任何源码/测试/配置。

---

*审计员：audit-shell；时间：2026-10-19 00:5x（本地）；工作目录 `D:\desktop\电量显示插件`。*
