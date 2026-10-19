# PeripheralBattery v0.2.0

> 目标发布：2026-10（Windows 11 / x64 构建；实际发布日期以发布页为准）
> 本版本新增**设置抽屉**、**开机静默启动**、**面板不透明度**与**低电/充满系统通知**，
> 并把低电阈值从 UI 统一到采集器。

Windows 托盘常驻的外设电量面板：鼠标 / 键盘 / 耳机 / 手柄，覆盖 2.4G 接收器、USB、蓝牙三条链路。
液态玻璃浮动面板（720×380 DIP 窗口 = 面板尺寸 / 12px 圆角，acrylic 只铺满面板），Tauri 2 + WebView2 + Python collector 双进程架构。

## 安装包与校验

- `PeripheralBattery_0.2.0_x64-setup.exe`（NSIS，当前用户安装，**无需管理员权限**；内置 WebView2 引导程序）
  - 安装包大小与 SHA-256：**以构建产物的 `SHA256SUMS.txt` 为准**（本文件不预填数字，避免与最终产物不一致）
  - 同包 app `peripheral-battery.exe`、collector `collector.exe` 的 SHA-256 同样记录在 `SHA256SUMS.txt`
  - 构建来源：`build-info.json`（版本 / git commit / 构建时间 / 每个产物 SHA-256 / `signed`）
- 随包发布 `SHA256SUMS.txt` 与 `build-info.json`；校验：
  `certutil -hashfile PeripheralBattery_0.2.0_x64-setup.exe SHA256`，与 `SHA256SUMS.txt` 对比
- **代码签名状态说明**：构建脚本支持 Authenticode（signtool）签名，但**本仓库不包含证书**；
  若构建时未配置 `PB_SIGN_*` 环境变量，发布包为 **NotSigned**，SmartScreen 可能显示“未知发布者”，
  Windows 通知也可能显示通用应用名（见「已知限制」）。校验请以上述 SHA-256 为准。

## 新功能

### 1. 设置抽屉（S-01）

右上角齿轮打开设置抽屉，按「启动 / 外观 / 提醒 / 面板 / 关于」分组：

| 分组 | 内容 | v0.2.0 状态 |
|---|---|---|
| 启动 | 开机启动开关、系统启动项三态文案 | ✅ |
| 外观 | 面板不透明度滑块、降低动效 | ✅ |
| 提醒 | 低电通知开关、充满通知开关、低电阈值 | ✅ |
| 面板 | 固定面板；记住窗口位置 | 固定 ✅ / 记住位置 v0.2.1 |
| 关于 | 版本、构建号、数据目录、日志入口 | ✅ |

设置存储拆成三层，避免双写：

| 归属 | 存储 | v0.2.0 字段 |
|---|---|---|
| 系统 / 进程行为（Rust 外壳） | `%APPDATA%\PeripheralBattery\app.json` | `schema`、`pinned`、`opacity`、`autostart`、`notify_low`、`notify_full` |
| 采集行为（collector 独占） | 同目录 `config.json` | `low`、`full_alert`、`tick_interval` 等 |
| 仅 UI 偏好 | `localStorage` | 只保留 `reducedMotion` 一项 |

- `app.json` 升到 **schema=2**；旧版 schema=1 首次启动自动迁移（见「升级说明」）。
- UI 与外壳新增 IPC：`get_settings`（无参，返回 `{settings, autostart}`）与
  `update_settings {patch:{opacity?, autostart?, notifyLow?, notifyFull?}}`
  （白名单校验 + 数值 clamp；autostart 写注册表失败会回滚内存与 `app.json` 并返回错误，界面提示后恢复开关）。
- `app-state` 增量字段：`settings`、`autostart:{enabled, osDisabled, path}`、`autostartLaunch`；
  旧字段全部保留。

### 2. 开机静默启动（F-01）

- 开启后写入当前用户启动项：
  - 键 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值名 `PeripheralBattery`，类型 `REG_SZ`，
    数据为 `"<安装目录>\peripheral-battery.exe" --autostart`（路径带引号，兼容含空格的目录）；
  - 同时只读探测 `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run`
    同名值（REG_BINARY 12 字节；首字节 `0x02` / `0x06` = 启用，`0x03` / `0x07` = 被系统启动项管理禁用）。
- 三态文案：**已启用** / **未启用** / **被系统启动项管理禁用**（后者只提示，不改写系统记录）。
- 启动行为：
  - `--autostart`（或 `--hidden`）为静默启动：不弹面板，只驻留托盘，日志记录 `autostart silent launch`；
  - 已有实例在运行时，开机启动的新进程**直接退出（exit 0）**，绝不激活/唤起已有窗口；
  - 手动双击（无 `--autostart`）行为不变：正常显示面板。
- 关闭开关会删除 `Run` 与 `StartupApproved\Run` 两处值；卸载时 NSIS 安装钩子
  （`NSIS_HOOK_POSTUNINSTALL`）也会清理这两处值，不留失效启动项。

### 3. 面板不透明度（F-02）

- 「外观」分组新增不透明度滑块：**50%–100%，步进 1，默认 60%**，实时生效并写入 `app.json`。
- 实现：面板深蓝渐变三档 alpha 改为 CSS 变量 `--panel-a1/--panel-a2/--panel-a3`，
  由设置值映射；参考取值：

| 滑块 | a1 | a2 | a3 |
|---:|---:|---:|---:|
| 50% | 0.500 | 0.433 | 0.517 |
| 60%（默认） | 0.600 | 0.520 | 0.620 |
| 80% | 0.800 | 0.760 | 0.810 |
| 100% | 1.000 | 1.000 | 1.000 |

- 当前环境不支持 acrylic（Windows 10、系统「透明效果」关闭、远程桌面、`PB_DISABLE_ACRYLIC=1`、
  `prefers-reduced-transparency`）时，滑块置灰并提示
  「当前系统不支持玻璃模糊，已锁定为不透明背景」，保留当前值不强行改写。

### 4. 低电 / 充满系统通知（F-03）

- 接入 `tauri-plugin-notification`，低电与充满时弹 Windows 系统通知；开关存 `app.json`：
  `notify_low`、`notify_full`（默认均开启），在「提醒」分组切换。
- 通知文案：`设备名 电量仅剩 12%，建议充电` / `设备名 已充满`（保留采集器给出的设备名与电量）。
- **10 分钟节流**：同一设备 + 同一类型（低电 / 充满）10 分钟内最多通知一次，不反复打扰。
- 面板内的 toast 提示保留，系统通知与面板提示可同时出现。
- 点击通知打开面板：**v0.2.1 候选**，本版本不承诺。

### 5. 低电阈值统一到采集器（F-04）

- 低电阈值不再由 UI 的 `localStorage` 单独保存，而是存采集器 `config.json` 的 `low`（0–100 整数，默认 20），
  采集器通过 `hello.settings.low` 同步给 UI；主卡告警与系统通知使用同一个阈值。
- 充满提醒开关对应 `config.json` 的 `full_alert`（bool）。
- 首次升级的阈值迁移与 UI 实现一致：仅当 UI 收到 `hello`、`hello.settings.low === 20`、
  旧版 `localStorage.lowThreshold` 存在且不等于 15 时，读取一次并向采集器推送
  `{"t":"set","field":"low","value":旧值}`，随后删除该键；其余情况（采集器已是非默认阈值 /
  旧值等于 15 / 无旧键）不推送，旧键在读取时即删除，之后以采集器配置为唯一来源。
- 协议版本仍为 **1**；`hello.settings` 增加 `low` / `full_alert` / `tick_interval` 为增量字段。

## 升级说明

- 从 v0.1.2 **直接覆盖安装**即可：安装目录、卸载注册表键、数据目录 `%APPDATA%\PeripheralBattery` 均不变，
  pinned / 别名 / 历史样本保留；无需先卸载。
- **`app.json` schema 1 → 2 自动迁移**：首次启动时补默认值
  （`opacity=60`、`autostart=false`、`notify_low=true`、`notify_full=true`），旧 `config.json`
  只迁移 `pinned`，迁移后写回 `app.json`；迁移失败不阻塞启动。
- **旧阈值迁移**：仅当采集器 `low` 仍为默认 20、旧 `localStorage.lowThreshold` 存在且不等于 15 时，
  推送一次 `set low` 后删除该键（见 F-04）；其余情况不推送，以采集器 `config.json` 为准。
- 系统要求不变：Windows 10 1809+ / Windows 11 x64，WebView2 Runtime ≥ 118。
- 不兼容点：无。协议、安装路径、注册表键、数据目录均向后兼容。

## 回滚到 v0.1.2

- v0.1.2 会忽略 `app.json` 中不认识的新字段（`schema`、`opacity`、`autostart`、`notify_low`、
  `notify_full`），回滚后功能退回旧行为；旧版不会重写 `schema`，再次升级 v0.2.0 仍会走迁移/读取。
- **若已在 v0.2.0 里开启「开机启动」**：回滚并覆盖安装 v0.1.2 后，Run 启动值仍然存在且路径不变，
  系统开机仍会静默启动（旧版没有 `--autostart` 语义时会按普通启动处理）。彻底回滚需：
  1. 先卸载 v0.2.0（卸载钩子会删除 `Run` 与 `StartupApproved\Run` 值），再安装 v0.1.2；或
  2. 手动删除 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\PeripheralBattery`
     与 `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run\PeripheralBattery`。
- 采集器 `config.json` 新增的 `low` / `full_alert` 会被旧版忽略；若旧版对未知字段报错，
  删除这两个键后重启采集器即可。
- `localStorage` 仅涉及 `reducedMotion`，回滚无影响；旧版不会恢复被删除的 `lowThreshold`（若不放心，
  回滚前记下滑块百分比并在旧版重新设置）。

## 已知限制

- **未签名应用的通知标题**：Windows Toast 依赖已注册的 AppUserModelID/开始菜单快捷方式，
  NotSigned 构建下通知标题可能显示通用名称（如应用显示名），正文与设备信息正常；签名后改善。
- **Windows 10 无 acrylic**：面板不透明度滑块锁定为不透明背景（文案提示），这是系统能力限制；
  Windows 11 且开启「透明效果」时可正常调节。
- 通知点击打开面板、记住窗口位置、「设备」分组（F-05）为 v0.2.1 候选，本版本未实现。
- 自动更新（updater）仍未接线；只能手动下载安装包升级。
- 其余与 v0.1.2 相同的限制（P7 24h 内存、P9 24h key 抖动、Win10/多屏/非 125% DPI、签名需证书、
  WebView2 离线安装等）见 README §7。

## 校验与排障

- 日志：`%APPDATA%\PeripheralBattery\logs\app.log`（含 `build=<git sha>`、`data_dir=`）、`collector.log`；
  开机静默启动相关日志关键字：`autostart silent launch`、`autostart apply`。
- 数据：`%APPDATA%\PeripheralBattery\{app.json,config.json,aliases.json,history.json,logs\}`；
  `app.json` 可手工检查 `schema` / `opacity` / `autostart` / `notify_low` / `notify_full`，
  低电阈值在 `config.json` 的 `low`。
- 自检启动项：
  `Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PeripheralBattery`；
  关闭「开机启动」后应报「不存在」。
- 本版本回归命令（2026-10-20 Lead 实测：**cargo test 70 passed / pytest 142 passed / ui_smoke 28 passed，
  0 失败；F-01 端到端 14/14 PASS**）：
  - `cd src-tauri; cargo test`（Rust 外壳 / 设置 / autostart reconcile / 通知节流）
  - `.venv\Scripts\python.exe -m pytest tests/collector -q`（采集器，含 F-04 阈值）
  - `node tools/ui_smoke.js`（UI smoke）
  - `python tools/verify_runtime.py`（运行时事件流）
  - `pwsh -File tools/verify_autostart.ps1`（F-01 开机静默启动验收：三态/静默/单实例/应用侧写删/清理）
  - `pwsh -File tools/verify_install_uninstall.ps1`（安装→校验→卸载→校验，含 F-01 卸载清理断言）
- 安装/卸载行为与静默参数见 README §4；卸载可加 `/DELETEAPPDATA` 删除用户数据。

完整说明见仓库 [README.md](../README.md)、[PROGRESS.md](../PROGRESS.md)、[TECH_PLAN.md](../TECH_PLAN.md)、[protocol.md](../protocol.md)。
