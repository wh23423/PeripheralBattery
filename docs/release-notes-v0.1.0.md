# PeripheralBattery v0.1.0

Windows 托盘常驻的外设电量面板：鼠标 / 键盘 / 耳机 / 手柄，覆盖 2.4G 接收器、USB、蓝牙三条链路。
液态玻璃浮动面板（780×440 DIP 窗口 / 720×380 面板），Tauri 2 + WebView2 + Python collector 双进程架构。

## 安装包

- `PeripheralBattery_0.1.0_x64-setup.exe`（约 8.9 MB，NSIS，当前用户安装，无需管理员权限）
- 可选：下载 `.sha256` 校验文件后执行 `certutil -hashfile <安装包> SHA256` 对照

## 本次版本要点

- collector：完整快照门控、稳定 key（`provider:vid:pid:serial|mac|unit|uN` + `replaces`）、
  stdio 五条硬化、30 个厂商 provider、蓝牙 watcher（默认 30s 连接检测）、
  Python 子进程 Job Object、`--probe/--once/--fake/--diag`
- 外壳：780×440 透明窗 + acrylic + DWM 圆角、托盘状态色图标、单实例、
  失焦 270ms 隐藏、Job Object、隐藏四步（`IsVisible(false)` + 前端暂停 + `TrySuspend`）
- UI：无框架静态页、keyed DOM diff、`level=null` 未知电量、transport 映射、
  生产 0 `backdrop-filter`、SVG/PNG 动效合成器化、设置抽屉
- 稳定性：Rust 侧托盘镜像（隐藏期间仍更新）、最小化可恢复、shutdown 超时
  `TerminateJobObject`、配置拆分为 `app.json` / `config.json`
- 最终验收（本机 Win11 26300 / 2560×1440@125% / 250Hz）：
  P1 250fps p99 4.2ms；P2 隐藏 0.318%；P4 稳态 247.7MB；pytest 94 / ui_smoke 15 / cargo test 10

## 已知限制

- 仅在本机验证过 1 台可读蓝牙设备（FlyDigi BS2 PRO）；真实 HID 外设、P9 24h key 稳定性、
  P7 24h 内存增长未验证。
- 蓝牙连接/断开检测上限 30s（为降低常驻 PowerShell 成本）；可用 `PB_BT_WATCH_MS` 调整。
- Win10 / 多屏 / 非 125% DPI / 三壁纸对比度未实测。
- 自动更新尚未接线；`build` 字段仍为 `unknown`。

完整说明见仓库 [README.md](../README.md)、[PROGRESS.md](../PROGRESS.md)、[protocol.md](../protocol.md)。
