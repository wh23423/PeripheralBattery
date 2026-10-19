# 外设电量显示器 — 技术方案 v0.1

> 基于 HaloBattery（HeyOkay/HaloBattery, MIT）的采集能力，替换全部 UI 层。
> 本文只定架构与边界，不写业务细节。

---

## 0. 结论摘要

1. **采集层原样复用**：`providers/`（30 个厂商协议，9.9k 行）+ `DeviceStatus` 模型是核心资产，一行不改地继承。
2. **UI 层全部重写**：丢弃 `halo/flyout.py`（1375 行 tkinter 复刻的 Win11 菜单）和 `halo/icons.py`（Pillow 画图标），因为它们是"为托盘菜单服务"的，与自定义 UI 的诉求直接冲突。
3. **采集与 UI 拆成两个进程**，中间走本机 IPC。原因见 §5-D1：HID 句柄独占，必须单进程采集；而自定义 UI 需要独立的技术栈与崩溃隔离。

推荐 UI 技术栈：**Tauri 2 + Web 前端**（若你有前端经验），否则 **PySide6/QML**。两种都能实现同一套架构，只换 UI 侧实现。

---

## 0.5 已确认的方向（2026-10-18）

| 项 | 结论 |
|---|---|
| 形态 | **单个托盘图标 + 左键点击弹出面板**；右键出小菜单（刷新 / 设置 / 退出） |
| 技术栈 | **Tauri 2 + Web 前端**（无框架静态页起步，需要时再升 Vite + 框架） |
| 视觉 | 音乐播放器式浮动圆角面板；**液态玻璃材质 + 点击光效**（v2） |
| 面板尺寸 | 720 × 380 CSS px（参考图 767×306、圆角 24px 的比例延续） |
| 设计稿 | `design/panel-v2.png`（默认态）/ `design/panel-v2-hover.png`（悬停光效），渲染自 `ui/index.html` |

面板行为约定：

1. 无边框、透明背景、圆角由 CSS 提供；不进任务栏（`skipTaskbar`）。
2. 点击托盘图标切换显示；失焦自动隐藏（除非点了"固定"）。
3. 出现位置：托盘图标附近，并夹在屏幕工作区内（多屏按所在屏计算）。
4. 顶部非交互区域可拖动窗口（`data-tauri-drag-region`），按钮区不参与拖动。
5. 视觉状态：正常 / 接近阈值（琥珀）/ 低于阈值（红）/ 充电（绿 + 呼吸）/ 休眠（半透明）/ 离线（移出）。
6. 托盘图标本身也用它自己的状态色（红/琥珀/绿），与面板保持一致。

---

## 1. 需求与边界

### 1.1 要做的

- 显示鼠标 / 键盘 / 耳机 / 手柄的电量，覆盖 2.4GHz 接收器、USB 线、蓝牙三条链路。
- 一套**自有的视觉语言**：设备卡片、电量环、充电动画、低电量配色、明暗主题。
- 托盘形态与面板形态都要能承载这套 UI。
- 设备重命名 / 隐藏 / 阈值 / 排序等基础配置。

### 1.2 不做的（v1 边界）

- 不做厂商灯效、DPI、宏等控制功能（那是 OpenRGB / 厂商软件的事）。
- 不复刻 HaloBattery 的自动更新、What's New、多语言（自用不需要，砍掉约 1.7k 行）。
- 不做跨平台（采集层重度依赖 Windows HID 与 WinRT；macOS/Linux 另立项）。

### 1.3 非功能性要求

| 项 | 目标 |
|---|---|
| 首次出数 | ≤ 3 s（不含慢设备如 JBL 监听型） |
| UI 刷新延迟 | 状态变化到界面可见 ≤ 200 ms |
| 空闲内存 | 采集进程 ≤ 80 MB，UI 进程按栈另计 |
| 常驻 CPU | 空闲 < 1% |
| 设备协议兼容 | 跟随上游 providers，**不自行发明** |

---

## 2. 参考实现的可复用边界

| 模块 | 行数 | 处置 | 说明 |
|---|---:|---|---|
| `providers/`（30 个） | 9,908 | ✅ 原样复用 | 厂商协议、VID/PID 表、诊断输出 |
| `providers/base.py::DeviceStatus` | 45 | ✅ 复用并扩展 | 加 `transport` 字段 |
| `halo/history.py` | 166 | ✅ 复用 | 剩余时间估算（掉电速率模型） |
| `halo_battery.pyw::App` | 2,361 | ⚠️ 拆分后复用 | 轮询调度、去重、告警、蓝牙监听保留；菜单/托盘/更新丢弃 |
| `halo/icons.py` | 700 | ⚠️ 参考后重写 | 托盘图标仍需位图，新 UI 另画 |
| `halo/flyout.py` | 1,375 | ❌ 丢弃 | tkinter 手绘 Win11 菜单，与自定义 UI 冲突 |
| `halo/updates.py` + `halo/whatsnew.py` | 1,684 | ❌ 丢弃 | 自用不需要 |
| `halo/winevents.py` | 124 | ⚠️ 按需保留 | 仅当要"全屏游戏时静默" |
| `tests/`（49 个） | — | ✅ 保留 providers 相关 | 改采集层时当回归网 |

**关键认知**：HaloBattery 的价值 90% 在 `providers/` 和 `docs/protocols.md`，UI 部分（约 4.7k 行）是我们本来就要替换的东西。所以这不是"改造一个 App"，而是"从一个 App 里取走数据层"。

---

## 3. 总体架构

```
┌─────────────────────────── collector.exe (Python) ───────────────────────────┐
│                                                                              │
│  providers/  ──►  Collector  ──►  StateStore  ──►  Transport  ──►  IPC       │
│  (HID/BT/WinRT)   每设备线程池     合并/去重/告警     JSONL 事件      stdio / WS│
│                        │              │                                      │
│                        │              └──► history.json（剩余时间）           │
│                        └──► 诊断(--probe / --diag)                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴────────────────┐
                    │                                │
          ┌─────────▼─────────┐          ┌───────────▼──────────┐
          │  UI 进程（Tauri）  │          │ 第三方消费方（可选）    │
          │  托盘图标/面板/    │          │ Rainmeter / StreamDeck│
          │  仪表盘/设置       │          │ 浏览器调试页           │
          └───────────────────┘          └──────────────────────┘
```

### 3.1 进程与生命周期

- `collector.exe`：无窗口，单实例（互斥体）。持有全部 HID 句柄，负责唯一一份真实状态。
- `ui.exe`：负责显示。启动时拉起 collector（或连接已存在的实例），退出时可选择是否连带退出。
- 第三方消费方：只读，连 WS 或读 `status.json`（保留兼容）。

### 3.2 为什么拆进程

- **HID 独占**：同一个 dongle 被两个进程 `hid.open()` 会互相抢报文，必须只有一个采集者。
- **崩溃隔离**：UI 是自研代码，迭代频繁；它崩了不该导致电量断更。
- **技术栈自由**：采集层锁死 Python（生态最好），UI 可以任意选。
- **多前端**：一套数据可以同时喂托盘、面板、Web 调试页。

---

## 4. 模块设计

### 4.1 Collector（采集层）

从 `App` 中抽出，只保留数据职责：

```python
class Collector:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def snapshot(self) -> list[DeviceStatus]: ...
    def subscribe(self, cb: Callable[[Event], None]) -> None: ...
```

**必须做的一处结构性改动**：上游是**串行** poll——`poll_once()` 顺序跑完 30 个 provider 才 `apply()`。而 Steam 手柄最多监听 5 s、WLmouse 打不开会重试 ~0.75 s/collection、JBL 要等推送。结果是最坏情况下界面十几秒不更新。

改为：

- 每个 provider 一个常驻 worker 线程 + 自己的 TTL（耳机 15 s、鼠标 5 s、手柄 3 s、蓝牙走推送）。
- 哪个 provider 先出结果就先推哪个（渐进式更新），慢设备只影响它自己。
- 保留上游的 `merge_bluetooth()` / `dedupe_controllers()` / `drop_bluetooth_duplicates()` 三个合并函数（逻辑是对的，直接搬）。
- 保留 `wake` 事件机制：设备插拔、按键唤醒时立即触发对应 provider。

### 4.2 状态模型（对 UI 的契约）

在 `DeviceStatus` 基础上补两个 UI 必需字段：

```python
@dataclass
class DeviceStatus:
    key: str            # 稳定 ID，UI 用它做列表 diff 和动画 id
    name: str
    level: int | None   # 0..100
    charging: bool
    online: bool        # 接收器在、但设备休眠/关机
    source: str         # razer / logitech / bluetooth / xinput ...
    approx: str         # 只能读档位的设备："约 55%（中）"
    kind: str           # headset / mouse / keyboard / gamepad / bluetooth
    wake_on_input: bool
    # 新增
    transport: str      # "2.4g" | "usb" | "bluetooth" | "unknown"
    updated_unix: float # 本次读数时间，UI 判断"多久没更新"
```

`key` 的稳定性是整个 UI 动画的基础（插入/移除/重排不能闪），上游已经保证"一个物理设备一个 key"，直接沿用。

### 4.3 事件协议

**原则：快照 + 增量。** 连接即全量，之后只推变化。

```jsonc
// 连接握手
{"t":"hello","protocol":1,"seq":0,"devices":[...全量...],"settings":{...}}

// 设备新增/更新（按 key upsert）
{"t":"device","seq":12,"device":{...}}

// 设备消失
{"t":"gone","seq":13,"key":"hid:1532:0528"}

// 低频心跳（每 30 s，防 UI 与真实状态漂移）
{"t":"tick","seq":40,"devices":[...全量...]}

// 告警（由 collector 判定，UI 只负责展示）
{"t":"alert","kind":"low","key":"...","level":12,"text":"..."}

// UI → collector（双向时才用）
{"t":"set","key":"...","field":"name","value":"客厅手柄"}
{"t":"action","name":"refresh","provider":"logitech"}
```

- `seq` 单调递增，UI 丢弃乱序。
- 只在**语义变化**时推送（level 变化、charging 翻转、online 翻转），不做每轮全量刷屏。
- 协议版本号从 1 开始，加字段不升版本，删/改字段升版本。

### 4.4 传输层（IPC）

定义一个 `Transport` 抽象，三个实现按场景选：

| 实现 | 用途 | 优点 | 缺点 |
|---|---|---|---|
| **stdio JSONL**（推荐主通道） | Tauri sidecar | 零端口、零防火墙、生命周期天然绑定、易调试 | 只能一对一 |
| **WebSocket**（`127.0.0.1` 随机端口 + token 文件） | 浏览器调试页、第三方、多前端 | 多客户端、跨语言 | 需鉴权、有端口管理成本 |
| **in-process queue** | PySide6 同进程方案 | 零序列化开销 | 不隔离崩溃 |

端口/token 发现文件：`%LOCALAPPDATA%\<App>\ipc.json` → `{ "port": 51234, "token": "...", "pid": 1234, "protocol": 1 }`。
安全边界：仅绑 `127.0.0.1`；握手必须带 token；消息长度上限 1 MB。

**不要用 `status.json` 文件轮询做主通道**——它是最小 1 s 级的延迟且无事件，只作为兼容层保留。

### 4.5 UI 层

形态无关的能力清单（无论 Tauri 还是 Qt 都要实现）：

1. **设备卡片**：图标 + 名称 + 电量环 + 百分比 + 充电态 + 剩余时间。
2. **状态映射**：正常 / 接近阈值（琥珀）/ 低于阈值（红）/ 充电（绿+呼吸）/ 休眠（半透明，保留最后读数 5 min）/ 离线（移出）。
3. **主题**：浅色 / 深色 / 跟随系统；亚克力或 Mica 背景（Windows 11）。
4. **交互**：重命名、隐藏、单设备阈值、拖拽排序、手动刷新。
5. **托盘**：单图标聚合 or 每设备一图标（产品决策，见 §10-Q1）。
6. **告警**：低电量一次、充满一次、全屏游戏时静默攒着。

**托盘图标的硬约束**：Windows 托盘只吃位图（16/20/24/32 px，多 DPI）。前端 SVG 不能直接塞进去，三条路：
- (a) 采集层继续用 Pillow 生成 PNG 推给 UI（复用 `icons.py` 思路，改动最小）；
- (b) UI 侧在 Rust/C++ 里用 `tiny-skia`/`resvg` 运行时绘制（灵活，多一套绘图代码）；
- (c) 构建期预生成 11 档电量 × 5 种设备 × 2 主题的 PNG 集（简单，但档位不连续）。

推荐 (a)：绘制逻辑只写一次，UI 只负责显示；且换 UI 技术栈时不用重画。

---

## 5. 关键技术决策

### D1. 采集与 UI 分离进程 ✅
理由见 §3.2。代价是引入 IPC 与 sidecar 打包复杂度，收益是技术栈自由 + 崩溃隔离 + 多前端。

### D2. 不魔改 providers ✅
`providers/` 是上游社区用真机验证过的协议集合（每加一个设备都要抓包），**任何修改都会让后续上游同步变难**。我们只在外面套 Collector，通过 fork + 定期 cherry-pick 维护。

### D3. 保留 `status.json` ✅
成本几乎为零（上游已有），换来自动兼容 Rainmeter / Stream Deck / 脚本，也是 UI 挂掉时的兜底观测手段。

### D4. 采集层做 provider 级并发 ✅
串行 poll 是上游为"单份 UI"做的简化，对自定义 UI 的实时性不够。改并发时**必须**保留：
- 同一 provider 不重入（线程内串行）；
- HID 枚举缓存（上游 `usb_signature()` 每 2.5 s 只列路径不开设备，别改成 `hid.enumerate()`）。

### D5. 配置归属
- **采集层配置**（轮询间隔、启用哪些 provider、蓝牙开关）放 collector 的 `config.json`。
- **UI 配置**（主题、窗口位置、卡片顺序、缩放）放 UI 侧自己的存储。
- 两边通过 IPC 的 `settings` 事件同步交集部分，避免双写。

### D6. 图标渲染留在采集层 ✅
见 §4.5 (a)。理由：新 UI 无论用什么栈，都需要同一个"电量→像素"的映射，集中一份最省事；也便于以后加自定义皮肤。

---

## 6. UI 技术栈选型

| 方案 | 包体积 | 视觉自由度 | 托盘/Win32 能力 | 与 Python 集成 | 开发门槛 | 结论 |
|---|---:|---|---|---|---|---|
| **Tauri 2 + Web** | ~8 MB + sidecar | ★★★★★（CSS/SVG/Canvas） | 中上（官方 tray、window-vibrancy 做亚克力） | sidecar stdio，简单 | 需 Node + Rust 工具链 | **推荐（有前端经验）** |
| **PySide6 / QML** | ~70 MB | ★★★☆ | 高（QSystemTrayIcon，可直接 ctypes 调 Win32） | 同进程，最简单 | 纯 Python | **推荐（想单栈单进程）** |
| Electron | ~150 MB | ★★★★★ | 高 | child_process | 需 Node | 除非团队只会 Electron，否则不选 |
| WPF / WinUI3 | 小 | ★★★★ | 最高（原生 Win11） | 需要 C# 重写采集或挂 sidecar | 双语言栈 | 采集层白写，不划算 |
| 纯 Web + 本地服务 | 0 | ★★★★★ | 无托盘，不算桌面应用 | WS | 最低 | 只适合做调试页 |

**已定：Tauri 2 + 静态 Web 前端。**
理由：面板的视觉密度高（发光圆环、玻璃层、渐变、动效），用 CSS/SVG 实现成本最低；Tauri 产物小、内存低，且有官方托盘与窗口特效支持。
前端先做成无构建的静态页（`ui/index.html` + `styles.css` + `app.js`），接入 Tauri 后直接把 `ui/` 作为 `frontendDist`，不需要打包器；等交互复杂了再引入 Vite + 框架。

---

## 7. 改造清单（文件级）

### 新增

```
collector/
  __main__.py          # 入口：--stdio / --ws / --probe / --diag
  collector.py         # Collector：provider 线程池、TTL、事件分发
  state.py             # StateStore：合并、去重、告警判定
  transport.py         # Transport 抽象 + StdioTransport / WsTransport
  protocol.py          # 消息编解码、协议版本
  settings.py          # 采集层配置
ui/                    # Tauri 或 PySide6 工程（§6 待定）
protocol.md            # 事件协议文档（给 UI 和第三方看）
```

### 复用（拷贝，不改）

```
providers/             # 原样
halo/history.py        # 原样
docs/protocols.md      # 保留在自己仓库 docs/ 下，作为设备适配手册
```

### 丢弃

```
halo/flyout.py
halo/updates.py
halo/whatsnew.py
halo/icons.py          # 重写为 render.py（仅托盘位图）
halo_battery.pyw       # 抽完 App 的逻辑后废弃
```

### 改写

```
render.py              # 从 icons.py 抽管线：DeviceStatus -> 多尺寸 PNG
alerts.py              # 从 App.check_alert / check_full / quiet 抽出
```

---

## 8. 里程碑

| 阶段 | 内容 | 验收标准 | 估时 |
|---|---|---|---|
| **M0 骨架** | Collector 抽出、stdio 协议、`--probe` 可用 | 命令行能打印 JSON 事件流；插拔设备能看到 `device`/`gone` | 2–3 d |
| **M1 只读 UI** | 设备列表、电量环、明暗主题、充电动画 | 界面数据与 `--probe` 一致；状态变化 ≤ 200 ms 上屏 | 3–5 d |
| **M2 交互** | 托盘图标、面板/弹窗、重命名/隐藏/阈值、告警 | 托盘点击出面板；低电量只提醒一次 | 3–4 d |
| **M3 产品化** | 打包、开机自启、单实例、DPI/多屏、图标适配 | 干净机器上双击即用；125%/150%/200% 缩放清晰 | 2–3 d |

总计约 **2–3 周**（单人）。若先做 L1（只换图标）或 L2（单进程换皮肤），见 §9 的降级路线。

### 降级路线（按投入从小到大）

| 路线 | 做法 | 估时 | 适合 |
|---|---|---|---|
| **L1** | 只改 `halo/icons.py` 的绘制，换一套自己的图标语言 | 0.5–1 d | 只想让它"看起来是我的" |
| **L2** | 保留单进程，用 PySide6 重写 flyout + 托盘，providers 不动 | 3–5 d | 想要自定义面板但不想搞 IPC |
| **L3** | 本文方案（拆进程 + 自研 UI） | 2–3 周 | 想要完整设计自由 + 未来多端 |

---

## 9. 风险与对策

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | provider 阻塞（Steam 5 s / WLmouse 重试 / JBL 等待） | UI 卡顿、数据陈旧 | D4 的 provider 级并发 + TTL；UI 永远显示"最后已知值 + 时间戳" |
| R2 | HID 独占：厂商软件（G HUB / Synapse）抢句柄 | 读不到数 | 上游已有提示；采集层加"被占用"诊断项 |
| R3 | 蓝牙走 PowerShell 子进程，开销与延迟都高 | CPU 抖动、响应慢 | 换 WinRT（`winrt-Windows.Devices.Bluetooth`）或保留 watcher 长驻 |
| R4 | 部分设备协议未在真机验证（上游标 Unverified） | 数值错误 | 保留 `--probe` 诊断；UI 对"未验证来源"显示弱化样式 |
| R5 | 未签名 Python exe 被杀软误报 | 无法分发 | 参考上游：PyInstaller onedir、公开构建、必要时代码签名 |
| R6 | 托盘位图在多 DPI 下模糊 | 观感差 | 按 16/20/24/32/40 px 出图，运行时按 DPI 选 |
| R7 | 上游项目很新（2026-09 创建），接口可能变 | 同步成本 | fork + 锁 commit，不引 submodule；改动集中在 Collector 边界 |

---

## 10. 待定问题

- **Q1 形态**：托盘图标为主（每设备一个图标）？还是单个托盘图标 + 点击弹出面板？还是常驻悬浮小组件（无边框置顶）？
- **Q2 技术栈**：Tauri 2 还是 PySide6？取决于你对前端/Rust 工具链的接受度。
- **Q3 取舍**：是否保留自动更新、多语言、诊断报告导出？自用可砍，但"诊断报告"对适配新设备很有用，建议留。
- **Q4 视觉方向**：偏 Windows 11 原生（Mica/亚克力、Segoe 字体）还是完全自成一派（自定义字体、暗色霓虹、卡片式）？

---

## 附：与上游的同步策略

- fork 到自己的仓库，`providers/` 目录**保持与上游一致的路径与文件名**，这样上游更新可以 `git cherry-pick` 或直接目录级 merge。
- 每次同步只解冲突在 `collector/`，不碰 `providers/`。
- 自己的设备适配补丁单独放 `providers_local/`，通过注册表合并，避免污染上游目录。

---

## 11. 视觉规范（当前稿：液态玻璃 v2）

材质方向：**一块真的玻璃**，不是深蓝卡片贴磨砂。面板透出背后内容，边缘有镜面高光，指针移动时玻璃里有一团柔光跟随。

| Token | 值 | 用途 |
|---|---|---|
| 面板烟熏层 | `rgba(14,34,74,.60)` → `rgba(10,24,52,.62)` | 保证任意壁纸下的文字对比（实测正文 ≥ 4.8:1） |
| `--glass` / `--glass-hi` | `rgba(255,255,255,.07)` / `.12` | 列表行、标签 |
| 描边 | `rgba(255,255,255,.13~.18)` | 玻璃边缘 |
| 镜面边 | 1px 渐变（左上 .72 → 右下 .36），mask 挖空 | 玻璃厚度 |
| 噪点 | SVG `feTurbulence` 200px 平铺，`.13` 不透明度 `soft-light` | 消除"纯模糊"的塑料感 |
| `--accent / --amber / --red / --green` | `#DBE9FF` / `#FFC24B` / `#FF5C6C` / `#3DDC97` | 电量状态 |
| 圆角 | 28 / 20 / 14 px | 面板 / 主卡 / 列表行 |

**动效（三个来源，各司其职）**

1. **指针柔光**：`pointermove` 写入 `--mx/--my`，驱动面板背景里的 560px 径向高光，跟手但不跟得太紧。
2. **点击光效**：按钮按下时发光收紧（Uiverse 那套 glow，色相换成 `#A9CCFF`）；列表行和玻璃空白处点击会在落点炸开一圈 screen 混合的涟漪。主按钮「全部设备」保留 `::after` 的投影光斑（perspective + blur），悬停时反色。
3. **数据动效**：圆环 `stroke-dashoffset` 过渡，充电时绿色呼吸；在线状态点脉冲（有真实数据变化才动）。

**Tauri 落地注意**：CSS `backdrop-filter` 只模糊 WebView 内部的内容，**透明窗口下不会模糊桌面**。真实毛玻璃要在 Tauri 侧开窗口特效（`tauri.conf.json` 的 `windowEffects: ["acrylic"]`，或运行时 `setEffects`），Web 层只负责烟熏色、镜面边、噪点和光效。两层叠加才是完整的液态玻璃。

## 12. 开发环境现状（本机实测）
| 依赖 | 状态 | 备注 |
|---|---|---|
| Node.js | ✅ v24.20.0 | 系统安装；DSH 另带一份 |
| npm | ✅ 11.19.0 | `registry.npmjs.org` 可访问 |
| WebView2 运行时 | ✅ 155.0.4283.x | Tauri 渲染依赖，已具备 |
| Visual Studio 2022 | ✅ `E:\VisualStudio\2022\Community` | MSVC 14.44 + Windows SDK 10.0.26100 齐全，`link.exe` 可用 |
| **Rust / cargo** | ❌ 未安装 | 需 rustup，下载源 `static.rust-lang.org` 可访问（12.7 MB） |
| Chrome | ⚠️ 存在但无法无头 | 命令行被已有会话劫持，改用 Playwright |
| Playwright | ✅ 已装（chromium-1248） | 用于把 `ui/index.html` 截成 PNG |

安装 Rust 的命令（待确认后执行）：

```powershell
# 1) rustup（默认 msvc toolchain）
Invoke-WebRequest https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe -OutFile $env:TEMP\rustup-init.exe
& $env:TEMP\rustup-init.exe -y --default-toolchain stable-x86_64-pc-windows-msvc

# 2) 校验 MSVC 链接器能被找到（VS2022 已装，通常无需额外操作）
#    若 cargo build 报 link.exe 找不到，用 "x64 Native Tools Command Prompt" 打开终端再构建
cargo --version
```

SSR 截图 / 视觉回归：

```bash
node tools/shot.js                 # 输出 design/panel-v1.png
node tools/shot.js out.png 780 440 # 自定义尺寸
node tools/shot.js out.png 780 440 ".all-btn"   # 悬停某元素后的状态
```

录操作演示：

```bash
node tools/record.js               # design/rec/*.webm（25fps 原片）
```

---

## 13. 动效清单（最终确认）

| # | 动效 | 触发 | 参数 | 状态 |
|---|---|---|---|---|
| 1 | 面板入场：浮起 + 去模糊 + 内容分层 | 面板出现 | 620ms，子项 40–340ms 错峰 | ✅ |
| 2 | 指针柔光跟随 | `pointermove` | 560px 径向高光，无缓动（跟手） | ✅ |
| 3 | 列表行悬停 | `:hover` | 200–250ms，边框转蓝 + 外发光 + 图标环放大 + 指针柔光 | ✅ |
| 4 | 点击涟漪 | `pointerdown` | 620ms 扩散 + `screen` 混合 | ✅ |
| 5 | 按钮发光 / 按下收紧 | `:hover` / `:active` | 300ms，悬停反色 | ✅ |
| 6 | 主卡切换：圆环扫动 + 数字滚动 + 卡片微缩 | 点击列表行 | 圆环 700ms / 数字 620ms / 卡片 320ms | ✅ |
| 7 | 充电呼吸 | `charging = true` | 2.8s 循环，绿色弧线明暗 | ✅ |
| 8 | 在线脉冲 | 有设备在线 | 2.4s 循环 | ✅ |
| 9 | 刷新旋转 | 点击刷新 | 600ms 一圈 | ✅ |
| 10 | 固定点亮 | 点击图钉 | 300ms，亮底深字 | ✅ |
| 11 | 键盘焦点环 | Tab | 即时，`#A9CCFF` 2px | ✅ |
| 12 | 降低动效 | `prefers-reduced-motion` / 设置开关 | 全部退化到 0.001ms | ✅ |
| 13 | 环境呼吸灯 | 面板可见 | 7s 循环，玻璃外圈蓝白光晕明暗（只动 opacity） | ✅ |
| 14 | 主卡圆环呼吸 | 面板可见 | 5.6s 循环，柔光层 opacity 0.05→0.18；充电时 2.8s 强化 | ✅ |
| 15 | 在线状态点呼吸 | 有设备在线 | 3.4s 循环，状态点 opacity 0.62→1 | ✅ |
| 16 | 行 / 主卡悬停柔光 | `pointermove` 命中行或主卡 | 140px / 120px 径向高光跟随指针，rAF 合并，隐藏/降低动效时清除 | ✅ |

**演示素材**

- `design/demo.gif` —— 操作演示（520×292，8fps，5.7MB，适合聊天窗口直接看）
- `design/demo.webp` —— 同内容高帧率版（720×406，12.5fps，1.4MB，浏览器打开）
- `design/rec/page@*.webm` —— 原始录制（780×440，25fps，17.6s）

**还需要 Tauri 外壳才能做的三件事**（不属于 Web 层）

1. 面板从托盘图标弹出 / 收回的位置与缩放动画。
2. 托盘图标本身的状态色与左右键行为。
3. 桌面背景真实模糊（`windowEffects: ["acrylic"]`）；CSS `backdrop-filter` 不会模糊桌面。
