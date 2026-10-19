# 交接说明（环境 + 施工入口）

> **2026-10-19 更新**：M0/M1/M2 已施工完成，release + NSIS 安装包已构建并通过运行验收。
> 当前状态、运行方式、实测指标与剩余问题见 [README.md](README.md)；审计报告见 `design/audit/`。
> 本文是施工前的环境交接记录，保留作历史参考（其中"Python 未安装""从 M0 开始"等已过时）。

> 给接手的人：先读 [BUILD_PLAN.md](BUILD_PLAN.md)（构建方案 v1.1，含 18 条对抗性审查处置），再读 [TECH_PLAN.md](TECH_PLAN.md)（架构与视觉规范）。
> 本文只讲"这台机器上现在有什么、从哪开始"。

---

## 1. 环境现状（本机实测，2026-10-19）

| 依赖 | 状态 | 版本 / 路径 |
|---|---|---|
| **Rust** | ✅ 已装 | `rustc 1.99.1` / `cargo 1.99.1`，stable-x86_64-pc-windows-msvc，`%USERPROFILE%\.cargo\bin` 已入用户 PATH |
| **Tauri CLI** | ✅ 可用 | `npx @tauri-apps/cli --version` → `tauri-cli 2.12.3`（未全局安装，按需 npx） |
| Visual Studio 2022 | ✅ | `E:\VisualStudio\2022\Community`，MSVC 14.44 + Windows SDK 10.0.26100，`link.exe` 可用 |
| WebView2 运行时 | ✅ | `155.0.4283.62` |
| Node / npm | ✅ | Node 24.20.0 / npm 11.19.0 |
| Git | ✅ | 2.56.0 |
| **Python** | ❌ **未安装（系统级）** | 采集器需要 **Python 3.12**；当前只有 DSH 内置运行时，不可复用 |
| Playwright | ✅ 已装 | 项目内 `node_modules`，chromium-1248，用于截图/录屏 |

### 验证命令

```bash
rustc -vV            # host 应为 x86_64-pc-windows-msvc
cargo --version
npx @tauri-apps/cli --version
node -v
```

已用 `cargo new` + `cargo build` + 运行 hello world 验证过 MSVC 链接链路（不是只装了 rustup 就收工）。

### 还需要补的

```powershell
# Python 3.12（必装，采集器依赖）
# 方式一：官网安装包 https://www.python.org/downloads/release/python-3129/
# 方式二：winget install Python.Python.3.12（本机 winget 不可用）
# 安装时勾选 Add python.exe to PATH
```

---

## 2. 交接文件

| 文件 | 说明 |
|---|---|
| [BUILD_PLAN.md](BUILD_PLAN.md) | **主文档**。架构、性能预算、后台低占用、打包、验收指标、里程碑、18 条审查处置 |
| [TECH_PLAN.md](TECH_PLAN.md) | 架构决策（D1–D6）、协议稿、视觉规范、动效清单 |
| [FEATURE_PLAN.md](FEATURE_PLAN.md) | **项目总计划书 v1.3**（S-01/F-01…F-12 + 动效整合 + 透明度落地 + 低电提示两项）；排期/顺序/验收以它为准 |
| `design/motion/` | 「潮息」动效语言（MOTION.md + 可启用 tokens.css）与真机实验室 motion-lab |
| `ui/` | 已完成的静态前端（HTML/CSS/JS，无框架）。玻璃材质、动效、交互都在里面 |
| `tools/shot.js` | Playwright 截图（`?demo` 预览模式、支持悬停态） |
| `tools/record.js` | 操作演示录制 |
| `tools/perf.js` | 帧时间采样（需 `--use-angle=d3d11` 才有真实 GPU） |
| `design/` | 设计稿、演示 GIF/WebP、原始录屏 |
| `hb/` | HaloBattery 上游克隆（`providers/` 是要复用的资产，`docs/protocols.md` 是协议手册） |

> `hb/` 是参考实现，**不要直接改**；采集器是从它里面抽 `providers/` + 重写调度层。

---

## 3. 从哪开始（M0）

按 BUILD_PLAN §9 的 M0，第一件事不是写 UI，是数据层地基：

1. 新建 `collector/`，从 `hb/providers/` 复制（保持路径不变，便于上游同步）。
2. 实现 **StateStore**：
   - `pending_providers` 快照门控（所有 provider 上报过才合并）；
   - `last_seen` + 全局 tick 判 `gone`（不用"缺几轮"计数）；
   - 稳定 key：`vid:pid:serial|mac|unit_id|持久化序号`。
3. stdio 硬化：UTF-8、flush、stderr 独立线程、`sys.stdout is None` 兜底、行长度限制。
4. `hello` 带 `session/build/capabilities`。
5. `--probe` 控制台模式验证：插拔设备、休眠唤醒，观察 key 是否抖动。

M0 的验收标准：**24 小时内不出现非物理原因的 key 变化，不出现 gone 误报。**

---

## 4. 五条不可违反的约束（来自对抗性审查，违反必返工）

1. **窗口 720×380 = 面板尺寸**（v0.1.1 起，备选 B；原 780×440+30px 安全边方案因 acrylic 整窗填充在降级环境出现灰框而废弃，SetWindowRgn 又与 acrylic 冲突）。面板圆角 12px，acrylic 只覆盖面板；Win10/透明效果关闭走 CSS 实底。
2. **生产环境删掉所有 CSS `backdrop-filter`**。透明窗口下它模糊的是"透明"，成本照付；毛玻璃交给窗口 acrylic。
3. **设备 key 不能沿用上游**。PS 手柄插第二只同型号 key 会变，XInput 用槽位，Keychron/HyperX 等用 PID——同型号两台会合并。
4. **`externalBin` 不能带 PyInstaller 的 `_internal`**。用 `bundle.resources`，从 `resource_dir()` 启动。
5. **隐藏状态要做四件事**：`getAnimations().pause()`、停 rAF、不写 DOM、`put_IsVisible(FALSE)`。只加一个 CSS 类没有任何作用。

---

## 5. 尚未验证的项（接手后第一批要测）

- WebView2 在 `window.hide()` 后是否仍出帧（决定 P2 怎么达标）。
- acrylic + 透明窗口在 Win10 的实际表现（Win11 已确认可用）。
- 托盘图标矩形获取（`tray-icon` 的 `rect()` 是否够用）。
- 集显上的合成开销（当前只有 RTX 5070 Ti 的 headless 数据：16.7ms 满帧，但不能替代真机）。
- PyInstaller onedir 的实际体积与杀软误报率。

---

## 6. v0.2.0 更新（2026-10-20）

> 已并入 FEATURE_PLAN v1.3 的 v0.2.0 范围（设置抽屉 / 开机静默启动 / 面板不透明度 / 系统通知 / 低电阈值统一）。
> 契约见 [TECH_PLAN.md](TECH_PLAN.md) §14，发布说明见 [docs/release-notes-v0.2.0.md](docs/release-notes-v0.2.0.md)。

### 6.1 环境（2026-10-20 复测）

| 依赖 | 状态 / 版本 |
|---|---|
| Rust / cargo | 1.99.1（stable-x86_64-pc-windows-msvc） |
| Tauri CLI | 2.12.3（npx） |
| Visual Studio 2022 | `E:\VisualStudio\2022\Community`（MSVC 14.44 + Windows SDK 10.0.26100） |
| WebView2 Runtime | 155.0.4283.62（应用最低要求 118+） |
| Node / npm | 24.20.0 / 11.19.0 |
| Git | 2.56.0 |
| Python | 项目 `.venv`（系统级未安装）；pytest / PyInstaller 走 `.venv` |
| PowerShell | 7.6.7（`tools/verify_autostart.ps1`、`tools/verify_install_uninstall.ps1` 需 pwsh 7） |

### 6.2 从 v0.1.2 升级 / 验收入口

- 构建：`pwsh -File scripts/build.ps1 -NoSign`（签名配置见 README §4）。
- 设置 / 启动验收：`pwsh -File tools/verify_autostart.ps1`、`pwsh -File tools/verify_install_uninstall.ps1`；
  两个脚本都会快照并恢复 HKCU 启动项，不弹 UAC。
- 数据迁移：`app.json` schema 1→2 自动补默认值；旧 `localStorage.lowThreshold` 首次启动迁移到 collector 后删除。
- 回滚：旧版忽略 `app.json` 新字段；若已开启开机自启，需先卸载 v0.2.0（或手动删除 `Run` 与
  `StartupApproved\Run\PeripheralBattery`）再装 v0.1.2。

### 6.3 后续 v0.2.1 候选

- 通知点击打开面板；记住面板窗口位置。
- FEATURE_PLAN 余项：F-05 设备分组（详情 / 重命名 / 隐藏）等。
- Win10 / 无 acrylic 环境的透明度策略复验；RDP / 快速用户切换下的单实例与开机启动未实测。
- 自动更新（updater）接线；整链签名（`bundle.windows.signCommand`）。
