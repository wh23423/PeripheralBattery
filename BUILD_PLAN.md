# 外设电量显示器 — 完整构建方案 v1.1

> v1.0 经对抗性审查后修订。18 条发现逐条处置见 §11。
> 目标不变：常驻后台几乎不耗电，面板弹出时稳定 60fps。

---

## 0. 验收指标（修订：按进程树统计）

| # | 指标 | 目标 | 测量方式 |
|---|---|---|---|
| P1 | 面板可见帧率 | 58–60fps，**p99 帧时间 ≤ 20ms** | CDP `Tracing`（`devtools.timeline.frame`）+ `FrameTiming`，rAF 只作辅助 |
| P2 | 隐藏时 **进程树** CPU | **< 0.5%**（单核，5 分钟均值） | app.exe + 全部 `msedgewebview2.exe` + `powershell.exe` 子进程 |
| P3 | 采集进程 CPU | **< 0.8%**（含其 PowerShell 子进程） | 进程树求和 |
| P4 | 进程树内存合计 | **< 320MB** | Working Set 求和 |
| P5 | 冷启动到托盘可用 | **< 1.5s** | 进程启动打点（不含"出数"，出数另有指标） |
| P6 | 点托盘到面板首帧 | **< 200ms** | 前后端时间戳 |
| P7 | 24h 常驻内存增长 | **< 15MB** | 每小时采样 |
| P8 | 定时器唤醒 | **≤ 1 次/秒**（采集器自报计数） | 采集器内部计数器 + ETW 校验 |
| **P9** | **设备 key 抖动** | 24h 内非物理变更导致的 key 变化 **< 2 次** | 采集器记 key 迁移日志 |
| **P10** | **更新/退出后孤儿进程** | **0** | 更新后扫描进程树 |

> 修订原因：v1.0 只统计 `app.exe` 与 `collector.exe`，漏掉 WebView2 的 browser/GPU/renderer 四个进程和常驻 PowerShell——那是真实占用的大头。

---

## 1. 进程与 IPC

```
app.exe (Tauri 2)  ──stdio NDJSON──  collector.exe (Python)
```

### 1.1 stdio 的五条硬性要求（v1.0 未写）

1. `--noconsole` 下 `sys.stdout` 可能是 `None`：入口处
   `if sys.stdout is None: sys.stdout = os.fdopen(1, "w", encoding="utf-8", newline="\n")`，stderr 同理。
2. 两端统一 UTF-8：`reconfigure(encoding="utf-8", newline="\n", line_buffering=True)`。设备名有中文。
3. 每条消息写完必须 `flush()`；块缓冲会等到 4KB 才输出。
4. **stderr 必须单独线程排空**（写日志文件并轮转）。上游 `bluetooth.py` 每轮每设备 `log.info`，写满 64KB 管道会让采集器卡死在 logging 里。
5. 行协议两端都要限长（Rust 用 `read_until(b'\n')` + 长度检查，超限丢行并重新同步）；EOF 丢弃未成行残片。

### 1.2 消息信封（v1.0 缺 session，重启后 seq 归零会冻结 UI）

```jsonc
{"t":"hello","protocol":1,"session":"<uuid>","build":"<git-sha>",
 "capabilities":["approx","left_s","via","transport"],"devices":[...]}

{"t":"device","session":"...","seq":12,"replaces":"旧key(可选)","device":{...}}
{"t":"gone","session":"...","seq":13,"key":"..."}
{"t":"tick","session":"...","seq":40,"devices":[...]}
{"t":"alert","session":"...","kind":"low","key":"...","level":12}
```

- UI 以 `(session, seq)` 排序；**session 变化 = 全量重置，但不重播入场动画**。
- `build` 不匹配时 UI 显示"采集器版本过期"并请求其退出重启。
- 命令方向：`{"t":"shutdown"}`、`{"t":"refresh"}`、`{"t":"set",...}`。

### 1.3 生命周期（v1.0 缺）

- Rust 用 **Job Object**（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）绑定 sidecar：app 崩溃/被结束，采集器一起死。
- 启动时用命名互斥体 + 版本握手；发现残留实例**先杀再起**，避免新 UI 连上旧 sidecar。
- 更新流程：`shutdown` → 等退出（超时 `TerminateProcess`）→ 才调 updater。运行中的 exe 和 `_internal\*.dll` 不可替换。

---

## 2. 60fps 渲染（修订）

### 2.1 生产环境删除全部 CSS `backdrop-filter`

**v1.0 的错误**：TECH_PLAN 自己写了"透明窗口下 CSS backdrop-filter 不会模糊桌面"，v1.0 却仍要保留一层并计入预算。

生产版事实：WebView 背景透明 → 38px 模糊作用在全透明图上 → **视觉贡献 ≈ 0，成本照付**；而且它让 `.panel` 成为 backdrop root，导致光斑无法独立合成。当前代码有 4 处（面板 38 / 控件簇 18 / 主卡 10 / 列表行 8）。

处置：
- 毛玻璃由 **Tauri 窗口 acrylic** 提供；
- CSS 只负责烟熏 alpha、噪点、镜面边；
- 保留一个 `data-effects="css"` 开发模式用于浏览器预览；
- 用 `@media (prefers-reduced-transparency: reduce)` 自动降级为不透明底。

### 2.2 指针柔光：去掉 `mix-blend-mode`

**v1.0 的错误**：方案说"只走合成器、不触发 paint"，但同一段 CSS 里有 `mix-blend-mode: screen`，且 `.panel` 有 `backdrop-filter` + `isolation: isolate` —— 光斑会被卷进面板的 backdrop group，每帧重新混合。

修正版：

```css
/* 放在 .panel 外层容器里，不在 backdrop-filter 祖先内 */
.specular {
  position: absolute; left: 0; top: 0;
  width: 560px; height: 560px;
  margin: -280px 0 0 -280px;
  background: radial-gradient(closest-side, rgba(255,255,255,.14), transparent 70%);
  pointer-events: none;
  will-change: transform;       /* 仅 pointerenter 时加，pointerleave 移除 */
}
```

- 无 `mix-blend-mode`，普通 alpha 叠加；
- `transform: translate3d(x,y,0)`，rAF 合并同一帧多次 `pointermove`；
- `will-change` 生命周期与指针进入/离开绑定，不常驻。

### 2.3 重新分配帧预算

| 阶段 | 预算 | 依据 |
|---|---|---|
| JS | ≤ 2ms | 无长任务（实测 0） |
| Style/Layout | ≤ 1ms | 动画只走 transform/opacity；`contain: layout paint` |
| Paint | ≤ 2ms | 静态噪点/渐变已成图；无实时 filter |
| Composite | ≤ 11ms | 唯一大面积动效是光斑层 |

### 2.4 其余合成器化改造

| 现状 | 问题 | 改法 |
|---|---|---|
| 4 处 `box-shadow` 参与 transition | 每帧重绘 | 伪元素预烘焙阴影 + 只动 `opacity` |
| `@keyframes pulse` 动 `box-shadow` | 每帧重绘且 forever | 缩放 + 透明度两条伪元素 |
| 涟漪用 `radial-gradient` + `mix-blend-mode` | 每帧重绘 | 预渲染 PNG 纹理 + `transform` 缩放 |
| 充电呼吸对带 `drop-shadow` 的环动 opacity | 滤镜重栅格化 | 拆"静态环 + 只动 opacity 的光层" |
| 面板入场 `filter: blur()` | 一次性可接受 | 保留，结束后移除 will-change |

---

## 3. 后台低占用（修订）

### 3.1 隐藏状态必须做四件事，而不是加一个 class

v1.0 的 `body.paused` 在 CSS 里**没有任何规则**。修正：

| # | 动作 | 说明 |
|---|---|---|
| 1 | `document.getAnimations().forEach(a => a.pause())` | 管住 Web Animations（按钮旋转、JS 动画） |
| 2 | 停 rAF 入口 + 清 `setTimeout` | `transitionHero` 数字滚动、涟漪回收、`.intro` 清理 |
| 3 | 隐藏期间**不写 DOM**：事件只存最后快照，显示时渲染一帧 | 从根上避免隐藏态样式计算 |
| 4 | `with_webview` → `ICoreWebView2Controller::put_IsVisible(FALSE)` | 真正让 Chromium 停止出帧；`TrySuspendAsync` 作备选 |

CSS 侧仍需 `body.is-hidden *, body.is-hidden *::before { animation-play-state: paused; transition: none }`（兜住 hover 撤销的过渡），但**不能只靠它**。

> 执行顺序：隐藏时先 `put_IsVisible(FALSE)` → 再暂停 JS；显示时先 `put_IsVisible(TRUE)` → 用最后快照渲染一帧 → 恢复动画。

### 3.2 托盘

- 静态图标，状态变化时才重绘（Rust `resvg`），**不做动画**。
- 位置：`tray-icon` crate 的 `rect()`；拿不到时退回"光标附近"，并夹在工作区内。
- 点击切换 + 失焦隐藏的抖动：失焦后 **250ms 抑制**，且用 `GetForegroundWindow()` 校验是否自己引起的。

### 3.3 采集调度（修订）

| provider | TTL | 说明 |
|---|---|---|
| 鼠标 | 5s | |
| 键盘 | 15s | |
| 耳机 | 15s | |
| 手柄（XInput/WGI） | 10s | **必须 ≥ provider 自身阻塞窗口** |
| Steam 手柄 | 30s（存在时才 8s） | 上游单次最多 5s 且内部 200Hz `sleep(5ms)`；3s TTL 等于全天候轮询 |
| 蓝牙 | 事件驱动 | 仅在连接状态变化时读 |

规则：**TTL ≥ 2 × provider 最坏阻塞时间**；provider 内部禁止 `sleep` 轮询，改用 `read(timeout_ms)`。

### 3.4 蓝牙与 WGI 的真实成本（v1.0 低估）

- WGI：上游每 8s `spawn` 一个 PowerShell（进程启动 60–100MB 峰值）。改为**一个常驻 helper**，批量查询 XInput 状态，8s 一次 stdin 命令。
- 蓝牙电量：不是 WinRT 给的，是 PnP 属性 `DEVPKEY_Bluetooth_Battery`。用 `ctypes` + `CfgMgr32`（`CM_Get_DevNode_PropertyW`）直接读；WinRT 只负责 `ConnectionStatus`。
- 上游的常驻 PowerShell watcher 保留为兜底，但要计入 P3 预算。

---

## 4. 数据层（重大修订）

### 4.1 设备 key 不稳定（v1.0 的错误前提）

抽查上游代码确认：

- `playstation.py:292`：`ident = mac or (usb if usb_count[pid] > 1 else "")` —— **插第二只同型号手柄，第一只的 key 会变**。
- `xinput.py:387`：`xinput:{slot}` —— 槽位复用，换手柄指向另一台设备。
- `logitech.py:400`：注释明写 "the key of a slot can change"。
- `keychron/hyperx/steelseries/corsair/jbl/...`：`{brand}:{pid}` —— 同型号两台合并成一行。

处置：在 Collector 层重建 key。

```
key = provider : vid : pid : (serial | mac | unit_id | stable_ordinal)
```

- `stable_ordinal` 来自持久化映射表：`(provider, vid, pid, name)` → 首次出现时分配的序号，存 `aliases.json`。
- 禁止使用 slot / idx / usb_count / 枚举顺序作为 key 组成。
- key 迁移时协议发 `replaces: 旧key`，UI 做 morph（保留位置与动画）而不是 delete+insert。

### 4.2 并发会打破上游三个合并函数（v1.0 的严重漏洞）

`drop_bluetooth_duplicates(results)` 与 `dedupe_controllers(results, bt)` 都要求**本轮完整的 HID 集合**；`apply()` 的 `missing[key] += 1`、连续 2 次才 gone，也是"整轮 poll"语义。

按 v1.0"哪个 provider 先出结果就先推"实现，其余 29 个设备在每次增量推送里都"没被看见"——**任意设备会在毫秒级被判 gone**。

修正后的 StateStore：

```
snapshot = {}
pending = set(所有启用 provider)
on_provider_result(p, devices):
    snapshot[p] = devices; pending.discard(p)
    if pending: return              # 不完整，不合并
    merged = dedupe_controllers(...) → drop_bluetooth_duplicates(...)
    diff_and_emit(merged)

on_scheduler_tick():                # 每秒一次
    for key, last_seen in last_seen_map:
        if now - last_seen > max(2*TTL, 10s): emit gone
```

- `gone` 只能由全局 tick 触发，且用 `last_seen` 时间判定，**不再用"缺几轮"计数**。
- 合并函数只在完整快照上运行。
- `DeviceStatus` 必须保留 `via` 字段（TECH_PLAN §4.2 的模型漏了，导致控制器去重静默失效）。

### 4.3 协议字段补齐（v1.0 缺）

```jsonc
{
  "key": "logi:046d:c547:unit-12",
  "name": "G502 X PLUS",
  "level": 68,              // int | null；null = 未知
  "approx": "",             // "约 55%（中）"，档位设备
  "left_s": 6480,           // 剩余秒数，null = 不估算
  "charging": false,
  "online": true,
  "kind": "mouse",          // headset|mouse|keyboard|gamepad|bluetooth
  "transport": "2.4g",      // 2.4g|usb|bluetooth|unknown（枚举，不传展示文案）
  "via": "",                // 控制器蓝牙
  "updated_unix": 1760000000
}
```

UI 必须处理 `level === null`：空心环 + `approx || "未知"`，**不能**走 `level <= 15` 判红（当前 `Math.min(100, null)` 会得到 0）。

### 4.4 UI 侧 keyed DOM diff（v1.0 声称有，实际没有）

当前 `render()` 每次 `innerHTML` 重建整个列表，并按电量排序。后果：悬停丢失、焦点被踢、圆环过渡重播、正在点击的行可能被换走。

改为：

- `Map<key, HTMLElement>`，行只创建一次；
- 后续只 patch `textContent` / `style.stroke` / `stroke-dashoffset`；
- 排序用稳定序（用户自定义顺序 → 首次出现顺序），按电量排时用 FLIP。

---

## 5. Tauri 配置（修订）

### 5.1 窗口尺寸：窗口 = 面板（v0.1.1 修订，备选 B）

**v0.1.0 的 P1**：窗口取 780×440（面板 720×380 + 四周 30px 安全边），acrylic 作用于整个窗口矩形，于是 30px 安全边也被 acrylic 填充；当 DWM 把 acrylic 降级（透明效果关闭 / 远程会话 / 虚拟显示驱动）时，四周出现不随桌面变化的固定灰框（#4D4E4F~#545454）。用户实际复现。

**v0.1.1 实测结论（决定放弃“区域裁剪”方案）**：`SetWindowRgn` 裁出面板形状后，DWM 会把 acrylic 降级成纯灰 #545454（4 组变体实拍：region+acrylic=灰边；no-region+acrylic=真磨砂；region+no-acrylic=透桌面；no-region+no-acrylic=透桌面）。即“窗口区域裁剪”与“真 acrylic”不可兼得，review §10 备选 B 胜出：

```jsonc
"width": 720, "height": 380,           // 窗口 = 面板，无安全边
"transparent": true, "decorations": false, "shadow": false,
"windowEffects": { "effects": ["acrylic"] }
```

- CSS `body { padding: 0 }`，`.stage/.panel` 填满窗口；面板圆角 12px 贴近 Win11 DWM 圆角（8px），角落 acrylic 外露 ≤4px。
- acrylic 只覆盖面板内部：结构上不可能再出现“面板外灰框”；DWM 仍在窗口外绘制柔和阴影（实测 ~40–60px 衰减），面板外无任何均匀色块。
- Win11：保留 `DwmSetWindowAttribute(DWMWA_WINDOW_CORNER_PREFERENCE, ROUND)`。Win10：没有 DWM 圆角，acrylic 会露出方角 → 该平台回退为不透明深色底（`data-windows11=false` / `data-acrylic=off` CSS 实底）。
- acrylic 降级检测：`EnableTransparency=0` / 远程会话 / `PB_DISABLE_ACRYLIC=1` / Win10 → `set_effects(None)` + `app-state.acrylic=false`，前端 `html[data-acrylic="off"]` 用不透明烟熏底（#0a1830），不存在灰框。
- 监听 `scaleFactorChanged` 重设窗口尺寸（720×380 DIP）并重定位；面板按 DIP 计算，避免 1px 半像素偏移。
- 已知取舍：窗口=面板后 P2-19“透明安全边吞点击”只剩 CSS 12px 圆角外的极小角落；外阴影由 DWM 提供、CSS box-shadow 仅浏览器预览可见。
- 回滚：`PB_DISABLE_ACRYLIC=1` 回到“无 acrylic + CSS 实底”，仍是窗口=面板。

### 5.2 焦点与弹层

- `pinned` 状态**存在 Rust 配置里**，不是只 toggle CSS 类（当前 `#btn-pin` 只改类，重启即丢）。
- 失焦隐藏要处理：`<select>` 弹层、输入法候选窗是独立 HWND，会触发 `Focused(false)`；延迟 300ms 隐藏，若焦点回到自身/弹层则取消。
- 全屏游戏：沿用上游 `SHQueryUserNotificationState` 判定，全屏时面板不主动弹出。

### 5.3 CSP 必须放行 IPC

```jsonc
"csp": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src ipc: http://ipc.localhost"
```

- Tauri v2 在 Windows 上的 IPC 入口是 `http://ipc.localhost`，没有 `connect-src` 会导致"界面正常但永远没数据"。
- 当前代码依赖内联样式（`style="stroke:..."`、`el.style.width`），要改成 CSS 变量/类，才能把 `style-src 'unsafe-inline'` 去掉。

---

## 6. 打包与更新（修订）

### 6.1 `--onedir` 不能只塞 exe

`bundle.externalBin` 只复制一个文件，PyInstaller `--onedir` 的 `_internal\`（python312.dll、hidapi.dll、pyd）不会进包 → 安装后必然 "Failed to load Python DLL"。

正确做法：

```jsonc
"bundle": {
  "resources": { "../dist/collector/": "collector/" },
  "targets": ["nsis"]
}
```

运行时 `app.path().resource_dir()/collector/collector.exe` 启动。若改 `--onefile`，需接受 `%TEMP%` 解包与更敏感的杀软误报。

### 6.2 更新的完整顺序

1. 收到更新 → `{"t":"shutdown"}`；
2. 等 sidecar 退出（2s 超时 → `TerminateProcess`）；
3. 确认 Job Object 已清空；
4. 调 updater；
5. 重启后校验 `build` 握手与 `hello`。

### 6.3 路径与日志

```
%LOCALAPPDATA%\PeripheralBattery\
  config.json          # 采集层设置
  aliases.json         # 设备 key 映射
  history.json         # 掉电历史
  logs\app.log         # 5×2MB 轮转
  logs\collector.log
  diagnostics\         # 诊断包（设备协议原文）
```

多用户天然隔离。诊断包供适配新设备用。

---

## 7. 测试与验收（修订）

| 项 | 做法 |
|---|---|
| P1 帧率 | **仅在真实 GPU 的自托管机器**跑：CDP Tracing 取 frame 事件 + 显示器刷新率固定 60Hz，覆盖 100/150/200% DPI、iGPU 与 dGPU |
| P2/P3/P4 | 进程树求和（含 `msedgewebview2*` 与 `powershell`），5 分钟窗口，单核百分比 |
| P8 | 采集器内部计数定时器唤醒，ETW `Microsoft-Windows-Kernel-Power` 抽验 |
| P9 | 采集器记录 key 迁移事件 |
| CI | **只做冒烟**：能否启动、sidecar 是否握手、截图是否非空。GitHub Actions 无 GPU，不跑帧率门禁 |
| 长稳 | 24h 循环：插拔、休眠唤醒、亮屏灭屏、更新；每小时采样内存/句柄 |
| 可访问性 | 对比度对 **白/黑/花色壁纸** 三张真实截图断言，不只在 demo 背景上测 |

---

## 8. 风险清单（更新）

| 风险 | 影响 | 对策 |
|---|---|---|
| 集显 + 大面积合成 | P1 | 生产无 CSS backdrop-filter；光斑单层；提供 `reduced_effects` 开关 |
| WebView2 隐藏后仍出帧 | P2 | `put_IsVisible(FALSE)` 实测；失败则隐藏期间不写 DOM + 停动画，目标按进程树重算 |
| key 抖动 | 体验 | §4.1 的稳定 key + `replaces` morph |
| 并发合并失效 | 数据错乱 | §4.2 完整快照门控 + last_seen |
| 更新时文件占用 | 更新失败 | Job Object + shutdown 握手 + 残留进程清理 |
| 休眠恢复后 HID 句柄失效 | 无数据 | `WM_POWERBROADCAST` 恢复后重开 HID、新 session、全量重发 |
| 蓝牙/WGI 的 PowerShell 成本 | P3 | 常驻 helper + CfgMgr32 直读；先量化再替换 |
| PyInstaller 杀软误报 | 分发 | onedir + 签名 + 公开构建 |

---

## 9. 里程碑（更新）

| 阶段 | 交付 | 验收 |
|---|---|---|
| M0 | 采集器骨架：StateStore（快照门控）、稳定 key、stdio 硬化、`--probe` | 插拔/休眠恢复不产生 key 抖动；无 gone 误报 |
| M1 | Tauri 外壳：780×440 透明窗、acrylic、DWM 圆角、托盘、隐藏暂停 | P5/P6；隐藏后进程树 CPU（P2）达标 |
| M2 | UI 接真实数据：keyed diff、null 电量、transport 映射 | 无整列表重建；未知电量不显示 0% |
| M3 | 交互与设置、告警、更新链路 | 更新后无孤儿进程（P10） |
| M4 | 性能矩阵 + 打包 + 24h 长稳 | P1–P10 全绿 |

---

## 10. 当前代码差距（更新）

| 项 | 状态 | 动作 |
|---|---|---|
| 静态 UI + 动效 | ✅ 已有 | 按 §2.4 替换 box-shadow/渐变/fliter 动画 |
| 指针柔光 | ⚠️ 现状错误 | 移到面板外、去 mix-blend-mode、transform-only |
| 隐藏暂停 | ❌ 无 | §3.1 四步 |
| keyed diff | ❌ 无 | §4.4 |
| 采集器 | ❌ 无 | 从上游抽 Collector + StateStore，重写 key |
| 协议 | ❌ 无 | §1.2/§4.3 |
| Tauri 外壳 | ❌ 无 | 需先装 Rust |
| 打包 | ❌ 无 | §6 |

---

## 11. 对抗性审查处置表

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| 1 | P0 | 窗口 720×380 恰好裁掉阴影、露出方角 acrylic | **v0.1.1 再修订**：780×440+30px 安全边会让 acrylic 整窗填充、在降级环境出现灰框（用户复现 P1）；SetWindowRgn 又与 acrylic 冲突（DWM 降级纯灰，实拍确认）。最终采用备选 B：窗口=面板 720×380、无安全边、acrylic 只铺面板、CSS 圆角 12px，Win10/降级走不透明实底；详见 §5.1 |
| 2 | P0 | `externalBin` 不带 `_internal` | 改 `bundle.resources`，运行时 resource_dir 启动 |
| 3 | P0 | 并发破坏合并函数与 gone 语义 | StateStore 完整快照门控 + last_seen |
| 4 | P0 | 设备 key 不稳定/不唯一 | 重建 key 体系 + 持久化映射 + `replaces` |
| 5 | P1 | 柔光不是零重绘（mix-blend-mode + backdrop 祖先） | 去 mix-blend-mode，移到面板外，transform-only |
| 6 | P1 | 生产 backdrop-filter 模糊的是透明，成本照付 | 生产全删，毛玻璃交给 acrylic |
| 7 | P1 | `paused` 类无任何 CSS；WAAPI/rAF/transition 漏网 | §3.1 四步 + IsVisible |
| 8 | P1 | `render()` 全量重建，无 keyed diff | §4.4 |
| 9 | P1 | `webview.setVisible` 不存在 | 改用 `with_webview` + `put_IsVisible` |
| 10 | P1 | CSP 缺 `connect-src`，invoke 被拦 | §5.3 |
| 11 | P1 | stdio 五个坑（None/编码/flush/stderr 死锁/半行） | §1.1 |
| 12 | P1 | 手柄 TTL 3s < Steam 5s 阻塞窗口 | §3.3 TTL ≥ 2×阻塞 |
| 13 | P1 | 蓝牙换 WinRT 不成立；WGI 每 8s 起 PowerShell | §3.4 常驻 helper + CfgMgr32 |
| 14 | P1 | P2–P8 测错对象；CI 跑帧率自欺欺人 | §0/§7 进程树 + 真机门禁 |
| 15 | P1 | 重启 seq 归零冻结 UI；版本不匹配无检测 | §1.2 session/build/capabilities |
| 16 | P1 | 协议缺 approx/left_s/via；null 渲染成 0% 红 | §4.3 |
| 17 | P2 | 对比度只在深色 demo 上成立 | §7 三张壁纸断言 + 动态烟熏 |
| 18 | P1 | 更新/休眠：文件占用、孤儿进程、HID 句柄 | §1.3/§6.2/§8 |
