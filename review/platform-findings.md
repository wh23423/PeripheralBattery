# 打包安装、更新与 Win11 部署矩阵审查（task-4 / platform-reviewer）

> 审查对象：`D:\desktop\电量显示插件` 当前工作树（HEAD=`a24f2fc`）
> 审查时间：2026-10-19 16:54–17:10（Asia/Shanghai）
> 环境：Windows 11 build 26300 x64 / WebView2 155.0.4283.62 / rustc 1.99.1 / node 24.20.0 / NSIS 安装包 `PeripheralBattery_0.1.0_x64-setup.exe`（9,358,346 B，13:12 构建）
> 审查方式：静态审阅 + 解包安装包（7-Zip）+ 实际静默安装/卸载 + 按安装布局实际启动 app。**未修改任何源码/配置/构建产物**；唯一新增文件为本报告。安装测试前导出并事后恢复了 HKCU 注册表，未触碰其他审查员进程/文件。

## 0. 结论摘要

- **P0：0 项**（未发现崩溃/数据错乱/完全不可用）。
- **P1：3 项**
  1. 默认安装目录与应用数据目录是同一个 `%LOCALAPPDATA%\PeripheralBattery` → 卸载后安装目录残留、`Delete app data` 删错目录；README「卸载后目录全清」在默认路径下不成立。
  2. WebView2 交付只有 `downloadBootstrapper`（安装时必须联网拉取），无离线/嵌入方案、无最低版本约束 → 离线/内网/企业精简单/Server/Sandbox 场景安装失败且无退路。
  3. setup/app/collector 全部未签名、无发布者/版权元数据、无校验和产物 → SmartScreen/Defender 拦截与供应链可信度风险（BUILD_PLAN/TECH_PLAN 已把「签名」列为分发前置，未落地）。
- **P2：10 项**（更新链路缺失、build 恒 unknown、无 CI/可复现性、卸载注册表残留、RDP/多会话、许可与语言、sidecar 完整性、部署参数文档、OS/架构矩阵、开机自启）。
- 旧审计中的资源映射（resource_dir→collector）、`\\?\` 路径归一化、安装/运行/卸载主链路**复核仍成立**；旧审计的「数据目录=安装目录」「WebView2 默认 bootstrapper」「updater 未接线」「build unknown」「单实例 Global 跨用户未测」「Job Object 建议 CREATE_SUSPENDED」**仍然存在**，其中数据目录问题本轮升级为 P1（见 P1-1）。

---

## P1 详情

### P1-1 默认安装目录 = 应用数据目录：卸载残留 + 数据删除勾选框删错目录
- **位置**
  - 默认安装路径：`src-tauri/target/release/nsis/x64/installer.nsi:499-514`（`currentUser` 分支 `StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"`），来源 `src-tauri/tauri.conf.json:50-53`（`installMode: currentUser`）。
  - 应用数据目录：`src-tauri/src/paths.rs:26,36`（`LOCALAPPDATA` + `"PeripheralBattery"`）。
  - 卸载器：`installer.nsi:912-914` 只做非递归 `RMDir "$INSTDIR\collector\_internal"`、`RMDir "$INSTDIR\collector"`、`RMDir "$INSTDIR"`；`installer.nsi:964-977` 的「删除应用数据」只在勾选时执行，且清理的是 `$LOCALAPPDATA\$BUNDLEID`（=`com.peripheralbattery.app`，实测 73MB 的 WebView2 profile），**不是**真正的数据目录 `%LOCALAPPDATA%\PeripheralBattery`；受 `installer.nsi:466-471` 的 `un.SkipIfPassive` 影响，静默/被动卸载永远取不到勾选。
- **触发场景（Win11）**：用户按默认路径安装（不选目录）→ 使用数天（产生 app.json/logs/history 等）→ 卸载。程序文件被删，但数据文件留在 `$INSTDIR`，非递归 `RMDir` 无法删除非空目录 → 安装目录整体残留；即使勾选「删除应用数据」，用户配置/历史仍在，而 WebView2 profile 反而被删。升级/迁移时用户数据与程序混放在安装目录，也破坏了「安装目录可整体删除」的部署假设。
- **证据（本轮实测，命令见 §5-6）**
  ```
  安装：setup.exe /S /D=C:\Users\28482\AppData\Local\PeripheralBattery  → exit=0
    exe/collector/uninstall.exe 均在；原有 app.json/config.json/... 保留
  卸载：uninstall.exe /S → exit=0
    INSTDIR exists after uninstall=True
    残留：aliases.json app.json collector.pid.json config.json history.json
          logs\app.log(1,623,689B) logs\collector.log(59,782B) diagnostics\
    exe/collector/uninstaller 已删除；UNINSTKEY 已删除；开始菜单/桌面快捷方式已删除
    HKCU\Software\peripheralbattery\PeripheralBattery 仍存在（默认值=安装目录）
  ```
  README 的相反声明：`README.md:117`、`README.md:131`「卸载后目录/进程/快捷方式全清」——其证据 `design/audit/final-runtime-evidence/test7-installer.log` / `design/audit/evidence/runtime/test7-installer.log` 全部使用 `/D=%TEMP%\PB-audit-final` 等**非默认路径**，因此从未覆盖该冲突。
- **修复方向**（任选，建议 1+2）
  1. 数据目录改为 `%LOCALAPPDATA%\com.peripheralbattery.app`（与 identifier 一致）或 `%APPDATA%\PeripheralBattery`，并迁移旧数据；或安装目录改为 `$LOCALAPPDATA\Programs\PeripheralBattery`。
  2. 卸载器对安装目录用 `RMDir /r`（或 `/REBOOTOK`），「删除应用数据」指向真实数据目录 + WebView2 profile；为静默卸载提供 `/DELETEAPPDATA` 参数。
  3. 回归测试必须覆盖「默认路径安装→运行→卸载」，不能只用 `/D=%TEMP%\`。

### P1-2 WebView2 交付策略：在线 bootstrapper 是唯一兜底，离线/内网安装无解
- **位置**：`src-tauri/tauri.conf.json` 未配置 `bundle.windows.webviewInstallMode`；生成的 `installer.nsi:56` `INSTALLWEBVIEW2MODE "downloadBootstrapper"`、`:60` `MINIMUMWEBVIEW2VERSION ""`；`installer.nsi:552-642`：仅当注册表（HKLM WOW6432Node/HKCU）查不到 WebView2 时执行 `NSISdl::download "https://go.microsoft.com/fwlink/p/?LinkId=2124703"`（`:571`），下载失败即 `Abort`（`:574-580`）。安装包内不含 bootstrapper/offline installer（7-Zip 解包 69 个条目：1 主 exe + 62 collector + 6 个 NSIS 插件）。
- **触发场景**：Win10 1809/21H2 LTSC、企业精简单、Windows Server 2016+/RDS、Windows Sandbox（默认无网络）、内网/代理阻断 `go.microsoft.com` 的机器：安装器在 WebView2 段直接失败；静默安装只往控制台写英文错误（UI 模式弹 `webview2AbortError`），无「先装 WebView2 再重试」的明确指引。旧版 WebView2 因 `MINIMUMWEBVIEW2VERSION` 为空**永远不会被升级**（`:614-615`），应用内也没有运行期检测/提示（运行时只会因 `ICoreWebView2_3/19` 不可用而静默降级，见 `src-tauri/src/platform.rs:477-522`，该降级本身是优雅的）。
- **证据**：上述配置行 + 7-Zip 文件清单；`npx tauri info` 显示本机 WebView2 155.0.4283.62（所以开发机永远触发不到该分支）。发行说明 `docs/release-notes-v0.1.0.md:8` 只写「NSIS，当前用户安装，无需管理员权限」，未写 WebView2 运行时依赖。
- **修复方向**：`bundle.windows.webviewInstallMode = "embedBootstrapper"` 或 `"offlineInstaller"`（+ 增大包体）；设置 `minimumWebview2Version`；安装器失败时给出可复制链接/离线包说明；应用启动时检测 WebView2 版本并给出可读对话框；README/发行说明补「系统要求」章节。

### P1-3 全部产物未签名、无发布者元数据、无校验和
- **位置/证据**：`Get-AuthenticodeSignature` 实测：
  ```
  PeripheralBattery_0.1.0_x64-setup.exe | Sig=NotSigned | Company=        | Copyright=
  src-tauri/target/release/peripheral-battery.exe | Sig=NotSigned | Company=peripheralbattery | Copyright=
  dist/collector/collector.exe | Sig=NotSigned | Company=        | Copyright=
  ```
  `tauri.conf.json:37-54` 无 `bundle.windows.signCommand`/证书配置；`installer.nsi:41` `LICENSE ""`、`:50` `COPYRIGHT ""`；`installer.nsi:473` 仅 `MUI_LANGUAGE "English"`。发行说明 `docs/release-notes-v0.1.0.md:9` 让用户「下载 `.sha256` 校验文件」，但 `scripts/build.ps1:64-67` 只打印安装包路径，**不生成 checksum**，bundle 目录实测只有 setup.exe。
- **触发场景**：任意用户从 GitHub Releases 首次下载 → SmartScreen「Windows 已保护你的电脑 / 未知发布者」；NSIS + PyInstaller one-dir 组合在部分 Defender/EDR 策略下易被启发式误报（`BUILD_PLAN.md:347` 自身的风险项「PyInstaller 杀软误报 → onedir + 签名 + 公开构建」，签名这半条未实现；`TECH_PLAN.md:344 R5` 同）；企业下载被拦截后无法自证来源。
- **修复方向**：对 setup.exe、uninstaller（`UNINSTALLERSIGNCOMMAND`）、app.exe、collector.exe 做 Authenticode 签名+时间戳（EV 证书更佳）；`build.ps1` 生成 `SHA256SUMS`/随包 `.sha256`；填写 `publisher/copyright/shortDescription/licenseFile`；CI 公开构建可提升信誉。

---

## P2 详情

### P2-1 `build` 字段恒为 unknown（构建号机制失效，且已发布包无法回溯 commit）
- `src-tauri/build.rs:4-15` 取 `git rev-parse --short HEAD`，但只声明 `cargo:rerun-if-env-changed=APP_BUILD`，**没有** `cargo:rerun-if-changed=../.git/HEAD`。仓库首次 commit 在 13:39:08（`.git/HEAD` mtime），而 release 的 build 脚本输出 mtime=13:12:11 → 打包时无 git 仓库 → `cargo:rustc-env=APP_BUILD=unknown` 被固化；之后（16:46/16:55/17:03 运行）release exe 一直打印 `build=unknown`（app.log 实测）。
- 对照证据：`.git` 就绪后，其他 cargo 增量构建产生的 build output 中已出现 `cargo:rustc-env=APP_BUILD=a24f2fc`（3 份 a24f2fc / 2 份 unknown），说明 git 本身可用，只是 release 从未重跑 build.rs。
- 影响：支持/日志/hello 无法把安装包映射到源码版本；README:164 已把它列为已知限制。
- 修复：build.rs 加 `println!("cargo:rerun-if-changed=../.git/HEAD")`，并支持 `PB_BUILD_SHA` 环境变量（CI 注入）；build.ps1 把 version+build+commit 写入安装包元数据/关于页。

### P2-2 安装器仅英文、无许可页，LICENSE/THIRD_PARTY_NOTICES 未随包分发
- `installer.nsi:473` 仅 English；`:41` `LICENSE ""` 导致 `:168-171` 的许可页被完全跳过；安装包（69 条目）与安装后目录（64 文件）均无 LICENSE/THIRD_PARTY_NOTICES（7-Zip 清单与实装核对）。
- `THIRD_PARTY_NOTICES.md:20` 明确要求「若再分发安装包，请保留本文件与 LICENSE」——当前发布违反自身声明；MIT 要求随副本保留版权/许可文本。另 `collector/providers/am_infinity.py:1-8` 注明协议来自 GPL-3.0 项目，但 THIRD_PARTY_NOTICES 未记录该来源，MIT 发行包存在许可一致性瑕疵。
- 修复：把 LICENSE/THIRD_PARTY_NOTICES 打进安装目录（含开始菜单快捷方式），加许可页；NSIS 增加 zh-CN 语言（`languages: ["SimpChinese","English"]`）；补 GPL 来源说明或改写该 provider 的协议来源。

### P2-3 构建可复现性/CI 缺失，发布产物未与构建输出强绑定
- 硬编码与隐式依赖：`scripts/build-collector.ps1:30` 写死 `C:\Users\28482\...\Python312\python.exe`（仅作 fallback）；`:46-51` 缺 PyInstaller 时自动 `pip install pyinstaller`（联网、版本漂移）；`requirements-dev.txt:3` 仅 `pyinstaller>=6.10`；`scripts/build.ps1:30` 写死 `$env:USERPROFILE\.cargo\bin`、`:59` 直接 `npx.cmd`（不校验 node/VS/WebView2）；无任何 CI 配置（`git ls-files` 无 `.github/`）。
- 版本三处手写：`tauri.conf.json:4`、`src-tauri/Cargo.toml:3`、`package.json:3` 均为 0.1.0，无版本提升校验，容易发布不一致。
- 产物一致性：解包 setup 得到的 `peripheral-battery.exe`（mtime 13:12:30，SHA256 `ef94b159…`）与 `target/release/peripheral-battery.exe`（mtime 13:12:38，`a90d4e2b…`）**同尺寸但 3 字节不同**；setup 文件 mtime 13:12:38.52 晚于包内 exe、早于当前 exe（.54），疑为并发/重链接；collector.exe 与 python312.dll 哈希则完全一致。说明构建无锁、发布前无「包内二进制=被测二进制」校验。
- `build.ps1:32-52` 的 `-SkipCollector` 只检查文件存在，不校验 dist/collector 是否比 collector 源码新 → 可能静默打包旧采集器。
- 修复：CI（Windows runner：VS2022+Python 3.12+Node+Rust 固定版本）串行构建一次、写入 commit/version、生成 SHA256SUMS 与 build-info.json；pin PyInstaller；去掉个人路径；校验 dist 新鲜度。

### P2-4 卸载残留：注册表 MANUPRODUCTKEY、WebView2 profile，且静默卸载无法删数据
- `installer.nsi:947-951` 只删 `UNINSTKEY`；`MANUPRODUCTKEY` 的删除在 `:964-968` 的勾选分支里。实测正常卸载后 `HKCU\Software\peripheralbattery\PeripheralBattery` 仍存在，默认值=安装目录，另含 `Installer Language=1033`。
- WebView2 用户数据 `%LOCALAPPDATA%\com.peripheralbattery.app\EBWebView` 实测 **73MB**，默认勾选为 0（`:464` 读取勾选、`:466-471` 静默跳过确认页）→ 静默/被动卸载永不清理；反而勾选时会删它，却不删真正的用户数据（P1-1）。
- 修复：无条件删除 MANUPRODUCTKEY；把数据删除目标改为真实数据目录；提供 `/DELETEAPPDATA` 静默参数；卸载完成后输出残留清单。

### P2-5 开机自启未实现（TECH_PLAN M3 已列入）
- 源码/前端 grep 无 `Software\Microsoft\Windows\CurrentVersion\Run`、启动文件夹、任务计划调用；安装器 `installer.nsi:954-959` 只是防御性删除同名的 HKCU Run 值。
- 影响：托盘常驻型工具每次开机需手动启动，与 `TECH_PLAN.md:322`「M3 产品化：打包、开机自启…」不符；README 未声明，属实现/文档缺口。
- 修复：实现 opt-in 开机自启（HKCU Run 或任务计划），卸载清理已具备；设置页暴露开关。

### P2-6 单实例用 Global 命名对象 + 共享 pid 文件：多会话/RDP 行为未定义
- `src-tauri/src/single_instance.rs:23` 默认 `Global\PeripheralBattery.single`；README:166 自认「跨用户场景未测」。
- 实测（受限令牌 `runas /trustlevel:0x20000`，仅 SeChangeNotifyPrivilege）：`CreateMutexW("Global\\…")` 返回句柄 err=0 —— 普通用户本身可以创建 Global 互斥体，因此单用户场景正常；但**同一账号的第二个 RDP/多会话**能看到该全局互斥体 → 第二会话进程直接退出（激活事件跨会话 SetEvent 也可能失败，窗口在另一会话），即同一账号不能在两会话各跑一份；不同账号的 DACL 行为未验证（可能 ERROR_ACCESS_DENIED → 走 Disabled 分支，单实例保护失效、可重复托盘）。
- 另：`collector.pid.json` 位于共享的 `%LOCALAPPDATA%\PeripheralBattery`，`sidecar.rs:833-870` 的 `cleanup_residual` 会按 pid 文件杀「python.exe/collector.exe」；本轮实测占用同一数据目录的另一个实例被本实例杀掉了其存活 collector（日志 `16:58:57 cleanup residual collector pid=27564 …`）。生产环境靠单实例互斥体避免，但互斥体一旦 Disabled（权限/跨用户）就会出现互杀。
- 修复：改 `Local\` 命名（每会话一实例）或名字内嵌用户 SID；pid 文件按 session/用户隔离；文档写明 RDP/多用户支持策略。

### P2-7 sidecar 完整性与 Job Object 竞态
- `sidecar.rs:889-890` 直接以 `resource_dir()\collector\collector.exe` 启动采集器，**无签名/哈希校验**；安装目录在用户可写的 `%LOCALAPPDATA%\` 且产物未签名（P1-3）→ 本机任意进程可替换 collector.exe，应用下次启动即执行；升级/装卸链也没有对采集器做完整性校验。
- Job：`sidecar.rs:334` spawn → `:353-356` 才 `AssignProcessToJobObject`，全工程无 `CREATE_SUSPENDED`（grep 为空）。spawn 与 assign 之间采集器若已派生 PowerShell/conhost，子进程不会进入 Job；而升级/卸载依赖 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`（`platform.rs:364`）清理，极端情况下可留孤儿（旧 `shell-audit` 已建议 CREATE_SUSPENDED，未落地；本轮运行日志未复现孤儿，P10 实测正常）。
- 修复：随包发布 collector SHA256/manifest 并在 spawn 前校验（或签名校验）；按旧审计建议 CREATE_SUSPENDED → Assign → ResumeThread，或用 `PROC_THREAD_ATTRIBUTE_JOB_LIST`。

### P2-8 安装/部署选项与文档缺口
- 静默安装参数存在但 README/发行说明完全未写：`/S`、`/D=`（必须最后且不加引号）、`/NS`、`/R`、`/ARGS`、`/P`、`/UPDATE`（见 `installer.nsi:771-820` 等）；静默安装会**无条件创建桌面快捷方式**（`installer.nsi:786-790`，需 `/NS` 抑制）。
- `tauri.conf.json:39` 仅 `"targets": ["nsis"]`、`:50-53` 固定 `currentUser`：无 MSI/per-machine 选项，GPO/SCCM「为所有用户安装」不可用；非管理员安装器允许用户在目录页选 `C:\Program Files`（会因无写权限失败）；`installer.nsi:54` `ALLOWDOWNGRADES "true"` 允许静默降级安装旧版本。
- 修复：README 增加「部署矩阵/静默参数」；建议 `installMode: "both"` 或同时产出 MSI；默认静默不建桌面快捷方式；`allowDowngrades: false`。

### P2-9 无 OS/架构下限声明，部署矩阵未验证
- 解包 app.exe 的内嵌 manifest 只有 Common-Controls 依赖，**无 `<compatibility><supportedOS>`、无 dpiAware/longPathAware**；安装器也没有 OS 版本检查 → Win7/8.1 也能装，但 WebView2 现代版本已不支持；Win10 1809/21H2、Server、Sandbox 均无实测（README:163 自认 Win10 仅代码路径与模拟）。
- 仅 x64：`tauri.conf.json:39` + `npx tauri info`（target x86_64-pc-windows-msvc），无 arm64 bundle。ARM64 Win11 上 x64 模拟大概率可跑，但 NSIS 的 `RunningX64` 与 WebView2 注册表探测（`installer.nsi:552-566`）在 ARM64 模拟下的行为未验证；Server 2016+/RDS 需先满足 P1-2 的 WebView2 前置，且受 P2-6 多会话限制；Windows Sandbox 默认无网络 → 命中 P1-2。
- 修复：manifest 声明 supportedOS、安装器加最低 OS 检查；文档写最低 Win10 版本；按需提供 arm64 构建；建立 Win10/Server/Sandbox 冒烟矩阵。

### P2-10 自动更新链路完全缺失（仅手动重装）
- `src-tauri/Cargo.toml:12-15` 无 `tauri-plugin-updater`，`tauri.conf.json` 无 `plugins`/`updater`，`npx tauri info` 的 Plugins 为空；`BUILD_PLAN.md:62,303-304` 设计的「shutdown→等退出→updater→重启校验 build/hello」无法执行；README:164 自认未接线。
- README 顶部仅 `releases/latest` 手动下载链接（本轮因网络策略 github.com 解析到非公网 IP 无法验证该链接/资产是否存在）；无更新签名/回滚策略。
- 修复：接线 updater（签名 manifest + 公钥），或明确「手动更新」策略并在应用内展示版本/检查入口、发布 SHA256SUMS；至少让 build/version 可见（配合 P2-1）。

---

## 3. 旧审计结论复核（本轮验证）

| 旧项 | 位置 | 本轮结论 |
|---|---|---|
| `bundle.resources` `../dist/collector/`→`collector/` 映射正确 | tauri.conf.json:47-49 | **成立**：解包 setup 的 `collector/collector.exe`、`_internal/python312.dll` 与 `dist/collector` 哈希完全一致（`308d7e1e…`、`b6227a50…`） |
| `\\?\` verbatim 路径归一化 | sidecar.rs:180,807-812,889-890 | **成立**：从 `D:\Temp\PB 安装 测试`（含空格/中文/非系统盘）以 packaged 布局运行，日志 `sidecar spawn path=\\?\D:\Temp\PB 安装 测试\collector\collector.exe … kind=packaged`、`job assigned=true`、hello ready、exit 0、Job active=0 |
| NSIS 安装/运行/卸载主链路 | final-runtime test7 | **在非默认路径下仍成立**（历史证据）；本轮默认路径安装/卸载也 exit=0，但暴露 P1-1 残留 |
| 数据目录与安装目录重合（旧 P2-2） | paths.rs:26,36 + installer.nsi:514 | **仍存在且更严重**：实测默认路径卸载残留（P1-1） |
| WebView2 未配置安装模式（旧） | installer.nsi:56 | **仍存在**（P1-2） |
| updater 未接线 | Cargo.toml/tauri.conf | **仍存在**（P2-10） |
| build 恒 unknown | build.rs | **仍存在**（P2-1） |
| Global 单实例跨用户未测 | single_instance.rs:23 | **仍未测**；新增 pid 文件互杀证据（P2-6） |
| Job Object 建议 CREATE_SUSPENDED | sidecar.rs:334-356 | **未实现**（P2-7） |
| 「Delete app data」删不到真实数据 | installer.nsi:976-977 | **确认**（P1-1/P2-4） |

## 4. 未验证 / 限制

- GitHub Releases 资产与 README 顶部下载链接未能验证：网络策略下 `github.com`/`api.github.com`「解析到非公网 IP」，`web_fetch` 失败。
- 未能实测「WebView2 缺失时应用启动行为」：用 `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=C:\nonexistent…` 模拟无效（应用照常启动，该 env 在新版 WebView2 被忽略），且不能为测试卸载系统 WebView2。
- 未实测 Defender/SmartScreen 具体拦截弹窗（仅由「NotSigned + 无发布者」推断）；未在干净机/ARM64/Win10/Server/Sandbox/RDP 环境实测（本机仅 Win11 26300 单会话）。
- 未运行完整 `scripts\build.ps1`（会重写 release/NSIS 产物，且与其他审查员并行 cargo/tauri 构建有竞态与锁风险）；改为 `cargo check`（exit=0）+ 审阅构建脚本 + 解包现有安装包。`npx tauri info` 仅执行只读环境探测。
- 安装测试只在默认路径做了一轮静默安装/卸载；测试前导出、测试后导入恢复 HKCU 注册表（恢复后 `Uninstall\PeripheralBattery` 仍指向测试前状态），未卸载任何用户安装的软件，未触碰其他审查员进程。

## 5. 实际运行过的命令与结果（关键摘录）

1. `git rev-parse --short HEAD` → `a24f2fc`；`git log -3` → 首次 commit `2026-10-19 13:39:17`；`.git/HEAD` mtime `13:39:08`。
2. `grep APP_BUILD src-tauri/target/{release,debug}/build/peripheral-battery-*/output` → `3× cargo:rustc-env=APP_BUILD=a24f2fc`、`2× APP_BUILD=unknown`；release build output mtime `13:12:11`。
3. `grep -h "app start version" %LOCALAPPDATA%\PeripheralBattery\logs\app.log` → `version=0.1.0 build=unknown`（16:46/16:55/17:03 多次）。
4. `7z l setup.exe` → `Type=Nsis`、`SubType=NSIS-3 Unicode`、`Physical Size=9358346`、`69 files`（1 主 exe+62 collector+6 插件，无 LICENSE/pdb/.py/hb）。
5. `7z x setup.exe` 后 SHA256：`collector/collector.exe` 与 `dist/collector/collector.exe` 相同（`308d7e1ec3ca…`）；`_internal/python312.dll` 相同（`b6227a506a99…`）；主 exe `ef94b159…` vs `target/release` `a90d4e2b…`，`cmp -l` 仅 3 字节差异（偏移 8441491-3）。
6. `Get-AuthenticodeSignature`：setup/app/collector 全部 `NotSigned`；setup 与 collector 的 `CompanyName/LegalCopyright` 为空，app 的 Copyright 为空。
7. 受限令牌互斥体探针（`runas /trustlevel:0x20000` + `CreateMutexW`）：`handle=2196 err=0`（Global），`local handle=2300 err=0` → 普通用户可创建 Global 互斥体。
8. 安装布局实跑：复制 release exe + collector 到 `D:\Temp\PB 安装 测试`，设 `PB_SINGLE_INSTANCE_NAME=Global\PBReviewPlatformLayout PB_QUIT_AFTER_MS=9000`；结果：`sidecar spawn … kind=packaged`、`hello protocol=1 … build=unknown`、`job object released active_processes=Some(0)`、`app exit`，exit=0（路径含空格/中文/非系统盘均正常）。
9. 默认路径静默安装/卸载（PowerShell `Start-Process -Wait`）：install `/S /D=%LOCALAPPDATA%\PeripheralBattery` → exit=0；uninstall `/S` → exit=0；残留清单见 P1-1；随后 `reg import` 恢复两份备份（exit=0）。
10. `reg query HKCU\Software\peripheralbattery\PeripheralBattery` → 卸载后仍存在（默认值=安装目录、Installer Language=1033）；`du -sh %LOCALAPPDATA%\com.peripheralbattery.app` → **73M**（卸载后仍在）。
11. `cargo check`（src-tauri）→ `Finished dev profile … in 0.96s`，exit=0；`npx tauri info` → WebView2 155.0.4283.62、MSVC VS2022、rustc/cargo 1.99.1、node 24.20.0、tauri 2.12.4、**Plugins 为空**。
12. `grep -c '^ *File ' installer.nsi` → 65；`grep -c 'oname=collector' installer.nsi` → 62；`find dist/collector -type f | wc -l` → 62（一致）；历史 test7 日志「installed file count=64 / totalMB=29.8-29.9」与 62+主 exe+uninstall.exe 一致。

## 6. TOP 待修复清单（按优先级）

1. **P1-1** 拆分安装目录与数据目录；卸载器 `RMDir /r`；「删除应用数据」指向真实数据目录；回归测试覆盖默认路径。
2. **P1-2** WebView2 改 `embedBootstrapper/offlineInstaller` + `minimumWebview2Version` + 运行期检测与可读提示；发行说明补系统要求。
3. **P1-3** 对 setup/uninstaller/app/collector 做 Authenticode 签名（含时间戳），生成并发布 SHA256SUMS；填入 publisher/copyright。
4. **P2-10** 接线 updater（签名 manifest）或明确手动更新策略；应用内显示版本/build。
5. **P2-4** 卸载无条件清理 MANUPRODUCTKEY；静默卸载支持删数据；清理 WebView2 profile（73MB）。
6. **P2-1** build.rs 加 `.git/HEAD` 依赖 + CI 注入 build sha，让安装包可回溯 commit。
7. **P2-3** 引入 Windows CI 串行构建、pin PyInstaller、产物写 build-info/SHA256SUMS、校验 dist 新鲜度。
8. **P2-6** 单实例改 Local/按会话或按用户命名，隔离 pid 文件；补 RDP/多用户策略。
9. **P2-2** 安装包内置 LICENSE/THIRD_PARTY_NOTICES、加许可页与 zh-CN 语言；补 GPL 来源声明。
10. **P2-7** spawn 前校验 collector 哈希/签名；CREATE_SUSPENDED→Assign→Resume 修 Job 竞态。
11. **P2-5** 实现 opt-in 开机自启（与 TECH_PLAN M3 对齐）。
12. **P2-8** README 补部署矩阵/静默参数；静默默认不建桌面快捷方式；评估 perMachine/MSI 与 `allowDowngrades:false`。
13. **P2-9** manifest 声明 supportedOS、安装器加最低 OS 检查；建立 Win10/Server/Sandbox/ARM64 冒烟矩阵。
