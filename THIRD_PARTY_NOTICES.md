# 第三方组件与许可声明

本项目的设备协议采集能力来自上游 **HaloBattery**（MIT License）：

- 项目：https://github.com/HeyOkay/HaloBattery
- 许可：MIT
- 使用方式：`collector/providers/` 与 `collector/history.py` 复制自上游（保持路径与文件名，便于同步）；
  采集器在其外层重写调度、状态合并、稳定 key 与 stdio 协议。上游的 `halo/`、`providers/bluetooth.py`
  等源码文件本身不被修改；本项目仅在运行时对 `WATCH_SCRIPT` 文本做轮询间隔替换（见 `collector/btconfig.py`）。
- 上游版权声明：Copyright (c) HaloBattery contributors，完整 MIT 文本见上游仓库 LICENSE。

## 运行时/构建依赖（未 vendored）

- Tauri 2 / wry / WebView2（Apache-2.0 / MIT，Microsoft WebView2 运行时按微软许可）
- Rust crates：见 `src-tauri/Cargo.lock`（各自 crate 的许可）
- Python：hidapi（MIT）
- 前端：无框架、无构建器；Playwright 仅用于开发验收
- 图标、UI 素材：本项目自绘

若再分发安装包，请保留本文件与 `LICENSE`。
