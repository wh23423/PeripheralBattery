# 第三方组件与许可声明

本项目以 **MIT License** 发布（见 [LICENSE](LICENSE)，安装包内置同一许可页）。
本文件随安装包分发（安装目录 `licenses\THIRD_PARTY_NOTICES.md`），再分发时请保留。

## 设备协议来源（上游 HaloBattery，MIT）

本项目的设备协议采集能力来自上游 **HaloBattery**（MIT License）：

- 项目：https://github.com/HeyOkay/HaloBattery
- 许可：MIT
- 使用方式：`collector/providers/` 与 `collector/history.py` 复制自上游（保持路径与文件名，便于同步）；
  采集器在其外层重写调度、状态合并、稳定 key 与 stdio 协议。上游的 `halo/`、`providers/bluetooth.py`
  等源码文件本身不被修改；本项目仅在运行时对 `WATCH_SCRIPT` 文本做轮询间隔替换（见 `collector/btconfig.py`）。
- 上游版权声明：Copyright (c) HaloBattery contributors，完整 MIT 文本见上游仓库 LICENSE。

## 协议逆向来源（上游 `hb/docs/credits.md` 的完整转述）

HaloBattery 按各厂商自家软件的方式读取设备；下列项目/作者逆向出了协议（上游文档原文索引）：

- WLmouse 协议：@len0c（[incconutwo/mouse-battery-tray](https://github.com/incconutwo/mouse-battery-tray)，MIT）。
- MCHOSE 协议：@alexfrih（[alexfrih/mchose-linux](https://github.com/alexfrih/mchose-linux)）；G7 来自 @kek353 的监视器与上游 issue #8。
- Hitscan Hyperlight 协议：@sopparus（[sopparus/hitscan-battery](https://github.com/sopparus/hitscan-battery)）。
- **AM Infinity 8K 协议（GPL-3.0 来源声明）**：AJAZZ Control Center 项目
  （[Aiacos/ajazz-control-center](https://github.com/Aiacos/ajazz-control-center)，**GPL-3.0**）读取同一 USB ID
  （AJ159 APEX）的公开实现；本项目 `collector/providers/am_infinity.py` 仅参考其**协议事实**
  （USB ID / 报文结构 / 电量字节语义），不复制其源代码，也不链接/分发其二进制。
  若后续需要直接复用其代码，必须按 GPL-3.0 另行评估本项目整体许可兼容性。
- BlackShark V2 Pro 2023：OpenRazer 驱动（[PR #2862](https://github.com/openrazer/openrazer/pull/2862)）；
  Razer PID / transaction id：OpenRazer、[RazerBatteryTaskbar](https://github.com/Tekk-Know/RazerBatteryTaskbar)。
- MCHOSE A5 Pro Max 协议：社区 web 驱动 [z750sasr/mchose-a5-pro-max-web-driver](https://github.com/z750sasr/mchose-a5-pro-max-web-driver)（MIT），
  与 [Klegus/mchose-macos](https://github.com/Klegus/mchose-macos) 的表一致；@Pekish992 的鼠标确认（上游 issue #149）。
- HyperX Cloud II Core Wireless：@dantifrice（上游 issue #155，NGENUITY USBPcap）。
- 其余参考实现（HeadsetControl、rivalcfg、Solaar、G-Helper、HyperHeadset、mouse.xyz、
  [@openmouse/protocol](https://github.com/OpenMouse-Project/openmouse)、keychron-battery-dkms、
  JBL_Baterry_Monitor 等）逐设备列在上游 `hb/docs/protocols.md`。

## 运行时 / 构建依赖（未 vendored）

- Tauri 2 / wry / WebView2（Apache-2.0 / MIT；Microsoft WebView2 Runtime 按微软许可，安装包通过
  `embedBootstrapper` 内置其引导程序，运行期由 Microsoft Edge WebView2 Runtime 提供）
- Rust crates：见 `src-tauri/Cargo.lock`（各自 crate 的许可）
- Python：hidapi（MIT）、PyInstaller（GPL-2.0-with-exception，仅构建期使用，不随采集器逻辑分发）
- 前端：无框架、无构建器；Playwright 仅用于开发验收
- 图标、UI 素材：本项目自绘

若再分发安装包，请保留本文件与 `LICENSE`。
