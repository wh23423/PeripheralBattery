# PeripheralBattery v0.1.1

> 发布日期：2026-10-19（Windows 11 / x64 构建）
> 本版本是评审报告 `review/REVIEW-REPORT.md` 的 A1–A14 修复版，重点在**安装/卸载、WebView2 交付、发布安全**与窗口显示问题。

Windows 托盘常驻的外设电量面板：鼠标 / 键盘 / 耳机 / 手柄，覆盖 2.4G 接收器、USB、蓝牙三条链路。
液态玻璃浮动面板（720×380 DIP 窗口 = 面板尺寸 / 12px 圆角，acrylic 只铺满面板），Tauri 2 + WebView2 + Python collector 双进程架构。

## 安装包与校验

- `PeripheralBattery_0.1.1_x64-setup.exe`（NSIS，当前用户安装，**无需管理员权限**；内置 WebView2 引导程序，比 0.1.0 大约 +1.8 MB）
- 随包发布 `SHA256SUMS.txt` 与 `build-info.json`（版本 / git commit / 构建时间 / 每个产物的 SHA-256）
  - 校验：`certutil -hashfile PeripheralBattery_0.1.1_x64-setup.exe SHA256`，与 `SHA256SUMS.txt` 对比
- **代码签名状态说明**：构建脚本支持 Authenticode（signtool）签名 setup/app/collector，
  但**本仓库不包含证书**；若构建时未配置 `PB_SIGN_*` 环境变量，发布包为 **NotSigned**，
  SmartScreen 可能显示“未知发布者”。校验请以上述 SHA-256 为准。签名方法见 README「发布与签名」。

## 系统要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 1809（build 17763）+ / Windows 11，**x64** |
| WebView2 | Microsoft Edge WebView2 Runtime **118.0.0.0 或更高**（安装器会检测并在需要时安装/升级；启动时再次预检） |
| 磁盘 | 安装约 120 MB（含 collector 运行库；WebView2 Runtime 另计） |
| 其它 | 蓝牙设备需要系统蓝牙支持；HID 设备建议使用官方驱动并避免被厂商软件独占 |

首次启动若 WebView2 缺失或过旧，应用会弹**中文错误框**（含下载地址）并退出，不再出现“双击没反应”。

## 本版本修复（A1–A14）

### 安装 / 卸载 / 发布（A1–A3，评审 P1-01/P1-02/P1-03、P2-02/P2-03/P2-05/P2-08）

- **A1 安装目录与用户数据分离**：用户数据迁至 `%APPDATA%\PeripheralBattery`
  （`PB_DATA_DIR` 可覆盖），安装目录（默认 `%LOCALAPPDATA%\PeripheralBattery`）只放程序文件。
  首次启动自动迁移 0.1.0 旧数据（config/app/aliases/history/logs/diagnostics，只补缺失文件，不覆盖新数据）。
- **A1 卸载清理**：卸载时无条件删除 `HKCU\Software\peripheralbattery\PeripheralBattery`
  （publisher 与 0.1.0 保持一致，注册表键不变；若未来 publisher 变化会额外清理旧 vendor 键）、
  清理 WebView2 profile（`%LOCALAPPDATA%\com.peripheralbattery.app`）、
  递归清理安装目录；“删除应用数据”勾选或静默 `/DELETEAPPDATA` 时才删除真实数据目录。
- **A2 WebView2 交付**：安装包改为 `embedBootstrapper`（内置引导程序，不依赖在线 NSISdl 下载步骤）+
  `minimumWebview2Version=118.0.0.0`；新增运行期预检（缺失/过旧 → 中文提示并退出）。
- **A3 发布安全与元数据**：安装器/应用补齐 publisher、copyright、MIT 许可页、short/long description、
  homepage；`LICENSE` / `THIRD_PARTY_NOTICES.md` 随包安装到 `licenses\`；构建生成
  `SHA256SUMS.txt` + `build-info.json`；构建号不再是 `unknown`（`PB_BUILD_SHA` 可注入，
  release 日志/诊断显示 git commit）。
- **A3 安装器语言**：NSIS 同时编入 English + 简体中文（跟随系统语言，无需选择框）。

### 窗口 / 生命周期（A4–A7、A12、A13）

- **A4** 系统级最小化（Win+↓ / Win+D / 显示桌面）后第一次点托盘即可正确显示/隐藏（状态以
  `IsWindowVisible + !IsIconic` 校准）。
- **A5** 单实例改用会话级命名对象：RDP / 快速用户切换 / 混合权限场景不再静默双实例；
  权限冲突时给中文提示。
- **A6** 残留 collector 清理增加归属判定（data_dir / exe / 实例 token），不再误杀其他并行实例。
- **A7** 虚拟桌面：面板在当前虚拟桌面显示；不可判定时降级为只警告不阻塞。
- **A12** 隐藏态托盘“固定面板”由 Rust 直接执行并同步菜单勾选，不再丢失/迟到触发。
- **A13** collector 崩溃/退出后指数退避自动重启（1s/3s/10s，最多 5 次），并立即发布离线托盘状态。

### 交互 / Win11 集成 / 采集器（A8–A11、A14）

- **A8** 拖拽区扩展到整个标题/空白 header（`data-tauri-drag-region="deep"`），标题也可拖动。
- **A9** 生产面板禁用 WebView2 右键菜单与 F5/Ctrl+R/Ctrl+P 等加速键，避免误重载/窗口位置重置。
- **A10** 睡眠/唤醒时钟跳变抑制批量 `gone`，设备保留，不再整批重加。
- **A11** 蓝牙 watcher 改为按最早 due 时间调度，连接后 +3/+8/+15s 补采不再被 30s 轮询吞掉。
- **A14** 修复“展开后四周灰色边框”：采用评审报告**备选 B**——窗口尺寸改为等于面板尺寸 **720×380**（去掉 780×440 外框四周的 30px 安全边），acrylic 只铺满面板内部；
  Windows“透明效果”关闭/远程/虚拟显示时 acrylic 自动降级为 CSS 烟熏底，并下发 `acrylic:false` 给前端。
  （原方案 A 的 `SetWindowRgn` 区域裁剪 + acrylic 组合经 4 组变体实拍会被 DWM 降级为纯灰 `#545454`，故不采用区域裁剪。）
  验收口径：面板外不再出现灰色矩形，可见外轮廓与面板 12px 圆角一致。

## 卸载行为

| 操作 | 安装目录 | 用户数据 `%APPDATA%\PeripheralBattery` | WebView2 profile | HKCU 注册表 |
|---|---|---|---|---|
| 控制面板/设置卸载（不勾选“删除应用数据”） | 删除 | **保留** | 删除 | 删除 |
| 卸载勾选“删除应用数据” | 删除 | 删除 | 删除 | 删除 |
| 静默 `uninstall.exe /S` | 删除 | **保留** | 删除 | 删除 |
| 静默 `uninstall.exe /S /DELETEAPPDATA` | 删除 | 删除 | 删除 | 删除 |
| 更新/覆盖安装（`/UPDATE` 路径） | 保留 | 保留 | 保留 | 保留 |

> 0.1.0 的旧数据位于 `%LOCALAPPDATA%\PeripheralBattery`；卸载 0.1.0 时安装器会把
> `app.json/config.json/aliases.json/history.json` 复制到新数据目录，0.1.1 首次启动再迁移其余文件。

## 从 0.1.0 升级

- 直接运行 `PeripheralBattery_0.1.1_x64-setup.exe`（默认路径与 0.1.0 相同）：
  - 静默/覆盖安装：旧数据留在安装目录，首次启动自动迁移到 `%APPDATA%\PeripheralBattery`；
  - 交互式升级若选择“先卸载旧版”：卸载器会在删除前把 4 个配置文件复制到新数据目录，不丢 pinned/别名/历史。
- **自定义安装目录的用户**：0.1.1 的 publisher 与注册表布局与 0.1.0 保持一致
  （`HKCU\Software\peripheralbattery\PeripheralBattery`），升级会沿用原安装目录；升级后用户数据仍会迁移到
  `%APPDATA%\PeripheralBattery`。若想换到默认安装目录，请先卸载 0.1.0（数据保留）再安装。
- 0.1.0 的数据目录里 `collector.pid.json` 是过期 pid，不迁移。

## 部署 / 静默安装参数（NSIS）

| 参数 | 说明 |
|---|---|
| `/S` | 静默安装（默认会创建桌面快捷方式；加 `/NS` 可跳过） |
| `/D=<路径>` | 自定义安装目录，**必须是最后一个参数且不加引号**（默认 `%LOCALAPPDATA%\PeripheralBattery`） |
| `/NS` | 不创建快捷方式 |
| `/R` | 安装完成后运行应用；`/ARGS` 传参给应用 |
| `/P` | 被动模式（显示进度条，不显示选项页） |
| `/UPDATE` | 更新模式（保留数据/快捷方式，供后续 updater 调用） |
| `uninstall.exe /S` | 静默卸载（保留用户数据） |
| `uninstall.exe /S /DELETEAPPDATA` | 静默卸载并删除用户数据 |

## 已知限制 / 未在本次修复中解决

- **WebView2 离线环境**：`embedBootstrapper` 已避免在线下载 NSIS 插件，但引导程序本身仍需要网络从
  Microsoft 下载/安装 Runtime；完全离线机器请预装 WebView2 Runtime（≥118）或使用 offlineInstaller 发行。
- **签名需证书**：默认发布包 NotSigned；发布方需配置证书环境变量重新构建才会是 Valid。
- 自动更新（updater）尚未接线；只能手动下载安装包升级。
- P7（24h 内存增长）、P9（24h key 抖动）、真实 HID 外设矩阵、Win10 / 多屏 / 非 125% DPI / ARM64 未实测。
- 蓝牙连接/断开检测上限约 30s（降低常驻 PowerShell 成本的产品折衷，可用 `PB_BT_WATCH_MS` 调整）。
- 32 位系统与 Windows 7/8.1 不支持（Tauri 2 + WebView2 需要 Win10 1809+ / Win11 x64；应用启动时按系统版本降级 acrylic，但不受支持）。

## 校验与排障

- 日志：`%APPDATA%\PeripheralBattery\logs\app.log`（含 `build=<git sha>`、`data_dir=`）、`collector.log`
- 数据：`%APPDATA%\PeripheralBattery\{app.json,config.json,aliases.json,history.json,logs\}`；
  迁移标记 `migration-v0.1.1.json`
- 运行期环境变量：`PB_DATA_DIR`（覆盖数据目录）、`PB_LOG_LEVEL`、`PB_SKIP_WEBVIEW2_CHECK=1`（测试用跳过预检）、
  `PB_DISABLE_ACRYLIC=1`（强制 CSS 实底、关闭 acrylic）、`PB_DISABLE_WEBVIEW2_HARDENING=1`（跳过右键菜单/加速键加固）、
  `PB_DISABLE_VD=1`（跳过虚拟桌面处理）、`PB_COLLECTOR_RESTART_MAX=<n>`（collector 自动重启上限，0=禁用）、
  `PB_BT_WATCH_MS`（蓝牙连接检测间隔，collector 用）等，完整列表见 README §8。

完整说明见仓库 [README.md](../README.md)、[PROGRESS.md](../PROGRESS.md)、[protocol.md](../protocol.md)。
