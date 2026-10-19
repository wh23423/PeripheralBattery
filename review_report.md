# v0.1.0 复审报告与待修复计划

> **正式版位置：本文已并入 [review/REVIEW-REPORT.md](review/REVIEW-REPORT.md) §10，并在 §7 阶段 A 增加 A14 条目。**
> 本文件保留为独立摘要，实施时以 `review/REVIEW-REPORT.md` 为准。
> 版本：PeripheralBattery v0.1.0（commit `a24f2fc`）
> 日期：2026-10-19
> 状态：**只记录计划，本轮不修改代码**；等确认后与 §3 的其余问题一起修，发 v0.1.1。
>
> **2026-10-19 晚更新（v0.1.1 已实施）**：阶段 A 的 A1–A14 已全部修复并完成自动化/实机验收，见
> [review/FIX-VERIFICATION.md](review/FIX-VERIFICATION.md)。其中 **§1 灰框方案有变更**：实测
> `SetWindowRgn`+acrylic 会被 DWM 降级为纯灰 `#545454`，故采用 review 报告的**备选 B**
> （窗口尺寸=面板 720×380、去掉 30px 安全边、acrylic 只铺面板、CSS 圆角 12px），详见验收记录 §2 A14。

---

## 1. 问题：展开面板后四周出现灰色边框

### 1.1 现象

- 托盘点击/第二实例激活后，面板四周出现一圈灰色（约 30px）区域，灰色不随背后桌面内容变化。
- 关闭再打开系统“透明效果”、甚至重启应用后，部分环境仍是灰色。
- 用户截图：973×548，正好对应窗口 780×440 DIP @125% = 975×550 物理像素；灰区就是窗口比面板多出来的安全边。

### 1.2 根因

1. 窗口按 BUILD_PLAN §5.1 设计为 **780×440 DIP**，CSS 面板 **720×380 DIP**，四周各 **30px** 安全边用于阴影/圆角。
2. 毛玻璃由 Tauri `windowEffects: ["acrylic"]` 提供；**acrylic 作用于整个窗口矩形**，不是只作用于 `.panel`。所以 30px 安全边也被 acrylic 填充。
3. acrylic 的实际表现依赖 DWM/系统环境：
   - `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\EnableTransparency = 0` 时，Windows 把 acrylic 降级为纯色（灰）；
   - 系统设置改为开启后，**已存在的窗口不会重新应用**，必须退出应用重开；
   - 即使开启，在某些虚拟显示/远程串流/驱动环境（本机存在 `OrayIddDriver Device`、`GameViewer Virtual Display Adapter`）或 DWM 模糊被禁用时，仍会退化成不透明灰底。
4. 因此这不是 CSS 颜色问题，也不是面板背景问题，而是**窗口矩形大于可见面板 + acrylic 整窗填充**的组合缺陷。

### 1.3 诊断证据（已复现）

| 项 | 值/现象 |
|---|---|
| 窗口尺寸 | 975×550 物理 = 780×440 DIP @1.25 |
| 面板尺寸 | 720×380 DIP（四周 30px） |
| DWM backdrop | `DWMWA_SYSTEMBACKDROP_TYPE = 3`（acrylic） |
| 灰边像素 | 背景为亮粉/白云/深色时，灰边始终约 `#4D4E4F ~ #535354`，不随背景变化 |
| 改注册表开透明效果 + 不重启 | 仍灰 |
| 完全退出重开（本机测试） | 灰边变为磨砂玻璃（能透出桌面），但用户环境仍灰 → 不能只依赖 acrylic |
| 系统会话 | `SM_REMOTESESSION=0`，但安装有 Oray/GameViewer 虚拟显示驱动 |

### 1.4 解决方案（推荐方案 A：窗口区域裁剪）

**核心思路：让窗口的可见区域就是面板形状，把 30px 安全边从合成/命中测试中裁掉；acrylic 只作用于面板内部。这样无论 acrylic 是否可用，都不会再看到灰边。**

实现要点（Rust）：

1. `src-tauri/src/platform.rs` 新增：
   ```rust
   // panel: 30px 安全边 + 720x380 + 28px 圆角，按 scale 换算成物理像素
   pub fn set_panel_region(hwnd: HWND, scale: f64) {
       let x = (30.0 * scale).round() as i32;
       let y = (30.0 * scale).round() as i32;
       let w = (720.0 * scale).round() as i32;
       let h = (380.0 * scale).round() as i32;
       let d = (28.0 * 2.0 * scale).round() as i32;
       unsafe {
           let rgn = CreateRoundRectRgn(x, y, x + w + 1, y + h + 1, d, d);
           SetWindowRgn(hwnd, rgn, 1); // 1 = redraw
       }
   }
   ```
   需要的 feature：`Win32_Graphics_Gdi`（`CreateRoundRectRgn`/`SetWindowRgn`）。
2. `panel::apply_platform()` 创建窗口后调用一次；`attach_window_events()` 的 `scaleFactorChanged` 在重设尺寸后再次调用（先 `SetWindowRgn(None)` 再重建）。
3. **定位补偿**：`position_panel()` 目前按窗口外框（780×440）对齐托盘。裁剪后可见面板左上角相对窗口偏移 `30*scale`，需要把窗口位置整体减去该偏移，保证面板仍贴着托盘/光标：
   ```rust
   let off = (30.0 * scale).round() as i32;
   x -= off; y -= off; // clamp 到工作区时用面板矩形重新算
   ```
4. **acrylic 降级检测（防止面板内部也变灰）**：
   - 启动时读 `EnableTransparency`；为 0 时调用 `win.set_effects(None)`，日志 `acrylic disabled: transparency off`；
   - 增加环境变量逃生口 `PB_DISABLE_ACRYLIC=1`（便于远程/虚拟显示用户手动降级）；
   - 可选：`SM_REMOTESESSION` 或检测 Oray/GameViewer 驱动时不启用 acrylic（避免纯灰）；不硬编码驱动名，先以注册表+环境变量为准。
   - 给前端 `app-state` 增加 `acrylic: bool`，前端在 `html[data-acrylic="off"]` 时把 `.panel` 底色调得更实（例如 `background-color` 叠一层 `rgba(10,24,52,.85)`），保证无模糊时对比度。
5. **保留 30px 布局与 CSS 不变**，只裁窗口区域；CSS 阴影会被裁掉（本来 70px 阴影也基本被窗口裁切），视觉上以面板边框/内阴影为主。
6. **风险与回滚**：
   - `SetWindowRgn` 与 acrylic 组合需要实机验证是否出现黑边/不刷新；
   - 提供 `PB_DISABLE_PANEL_REGION=1`，关闭后回到当前行为；
   - 若区域裁剪在 DWM 下不稳定，走备选方案 B。

**备选方案 B（更简单、但改动布局）**：窗口直接改为 **720×380**，CSS `body` padding 30px 改为 0，`.stage/.panel` 填满窗口。acrylic 只在面板内；DWM 圆角与面板圆角不一致的问题用 `SetWindowRgn` 或面板 12px 圆角兼容。代价：面板四周没有阴影空间，拖拽/定位代码需按 720×380 重算。

**备选方案 C（最简、但失去真毛玻璃）**：移除 `windowEffects` acrylic，窗口保留透明；30px 区域直接透出桌面（无模糊）。风险：某些远程/虚拟显示下整窗透明也可能异常；不作为首选。

### 1.5 验收标准（修完后必须全过）

- 在 3 种背景（亮色、深色、彩色）下截图：**面板外不再有均匀灰色矩形**；窗口可见外轮廓与面板 720×380 圆角一致。
- 透明效果开/关、重启、远程/虚拟显示下行为一致（关闭时面板为 CSS 烟熏底，无灰框）。
- 托盘点击/第二实例 show、失焦隐藏、最小化恢复、拖动、按钮/设置抽屉、125% DPI 全部正常。
- 裁剪后窗口命中测试正确：面板外不再拦截点击；托盘定位不偏移（面板中心/底部指向托盘）。
- 隐藏态 P2/P4 不回归（`tools/verify_runtime.py hidden`，P2 <0.5%、稳态 RSS <320MB）。
- `node tools/ui_smoke.js`、`pytest`、`cargo test` 全绿；20 次可见启动压测 0 hang。
- 发布 v0.1.1：NSIS 重新打包、GitHub Release 附新 exe + sha256、README/Release Notes 说明修复。

---

## 2. 灰色问题之外的待修复/待验证项

> 以下一并列入 v0.1.1 候选批次，按优先级排列；可在同一轮一起改。

### P1（建议本轮修）

| # | 问题 | 计划 |
|---|---|---|
| 2.1 | 灰色边框（本文 §1） | 方案 A（窗口区域裁剪 + acrylic 降级检测） |
| 2.2 | Bluetooth 连接/断开检测上限 30s（为 P2 从 2s 降到 30s） | 短期：保留 30s 默认，暴露 `PB_BT_WATCH_MS` / 设置项；长期：`CfgMgr32 CM_Get_DevNode_PropertyW` 直读 `DEVPKEY_Bluetooth_Battery` + WinRT 仅查连接状态，去掉常驻 PowerShell |
| 2.3 | 启动峰值 RSS 449MB（前 ~20s TrySuspend 前） | 可选：显示前先隐藏 WebView2 或延迟初始化数据；或把 P4 验收口径明确为“隐藏稳态”。至少写入文档/Release Notes |

### P2（建议同批）

| # | 问题 | 计划 |
|---|---|---|
| 2.4 | P7 24h 内存增长未验证（5min +21MB） | 增加 24h soak 脚本（每小时采样 RSS/句柄），定位 PowerShell watcher 增长；必要时定时重启 watcher 或改 CfgMgr32 |
| 2.5 | P9 24h key 抖动未验证、真实 HID 设备少 | 24h 插拔/休眠恢复循环 + aliases 迁移日志统计；有真机后补 logitech/PS/Steam 路径 |
| 2.6 | P6 托盘点击到 first-paint 未精确打点 | Rust 记录托盘点击时间戳，前端首帧 `requestAnimationFrame` 通过 `report_fps` 同款命令回报；验收 <200ms |
| 2.7 | 数据目录与 NSIS 安装目录重合（`%LOCALAPPDATA%\PeripheralBattery`） | 改数据目录为 `%LOCALAPPDATA%\PeripheralBattery\data`（迁移旧文件），或定制 NSIS 安装目录；确保卸载不误删配置 |
| 2.8 | 自动更新未接线、`build` 恒 `unknown` | 接入 Tauri updater（或至少签名/版本握手），build 写入 git sha |
| 2.9 | 托盘右键菜单未用真实鼠标点击验证；Win10/多屏/DPI 矩阵未测 | 人工/自动化补测矩阵；记录到 README |

### P3（可排期）

- WGI 常驻 helper（替代每 8s spawn PowerShell）。
- `WM_POWERBROADCAST` suspend/resume → `new_session()` 全量重发。
- offline 告警（设备离线提示）。
- 测试矩阵：Win10 实机、多屏/负坐标、100%/150%/200% DPI、三张壁纸对比度。
- `collector stat`/日志进一步收敛；`build` 注入。

---

## 3. v0.1.1 建议提交/发布顺序（等确认后执行）

1. 按 §1 方案 A 修改 `src-tauri/src/platform.rs`、`panel.rs`、`commands.rs`/`state.rs`、`tauri.conf.json`（如需 acrylic 开关）、`ui/app.js`/`styles.css`（acrylic off 样式）。
2. 本机验收：灰色问题三背景截图、透明效果 0/1、远程/虚拟显示、DPI、20 次启动压测、隐藏 P2/P4、ui_smoke/pytest/cargo test。
3. 版本号 `0.1.1`；更新 `README.md`、`docs/release-notes-v0.1.1.md`、`PROGRESS.md`。
4. `scripts/build.ps1` 生成新 NSIS，计算 sha256。
5. 提交并推送 `wh23423/PeripheralBattery`，创建 GitHub Release `v0.1.1`（exe + sha256）。
6. 若用户希望保留“旧版灰框行为”用于对比，可加环境变量开关，但默认启用新行为。

---

## 4. 诊断命令备忘

```powershell
# 透明效果
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' |
  Select-Object EnableTransparency

# 窗口 DWM backdrop（3=acrylic）
# 可用 tools/ 下的 Win32 探针或 PowerShell Add-Type DwmGetWindowAttribute(hwnd, 38, ...)

# 复现：启动后抓整屏并采样窗口四周像素（应随背景变化，而不是固定灰）
& 'C:\Program Files\PowerShell\7\pwsh.exe' -File tools\capture_screen.ps1 -Out <png>
```

---

## 5. 结论

- 灰色区域的直接原因已定位：**780×440 窗口的 30px 安全边被 acrylic 整窗填充**，而 acrylic 在部分环境（透明效果关闭/远程或虚拟显示/驱动）退化为纯灰。
- 修复不应依赖“用户去开透明效果”，而应让可见窗口区域等于面板形状，并在 acrylic 不可用时明确降级。推荐 **SetWindowRgn 裁剪 + acrylic 检测关闭 + 前端无 acrylic 样式**。
- 本文只记录计划；未修改任何源码/构建产物。确认后按 §3 与其余待修项一起实施，发布 v0.1.1。
