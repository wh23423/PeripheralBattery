# PeripheralBattery v0.1.2

> 发布日期：2026-10-19（Windows 11 / x64 构建）
> 本版本修复用户反馈的三个问题：**面板有时拖不动**、**设备显示「未知连接」**、**剩余使用时间长期未知**。

Windows 托盘常驻的外设电量面板：鼠标 / 键盘 / 耳机 / 手柄，覆盖 2.4G 接收器、USB、蓝牙三条链路。
液态玻璃浮动面板（720×380 DIP 窗口 = 面板尺寸 / 12px 圆角，acrylic 只铺满面板），Tauri 2 + WebView2 + Python collector 双进程架构。

## 安装包与校验

- `PeripheralBattery_0.1.2_x64-setup.exe`（NSIS，当前用户安装，**无需管理员权限**；内置 WebView2 引导程序）
  - 大小 **11,166,020 B（10.65 MiB）**，SHA-256 `0633094a151b66a2199b84a41cf03112b8163f27edc71872157f7e736f8ae384`
  - 同包 app `peripheral-battery.exe` SHA-256 `0a8000e71daff88d7b4115570cf2bc34bc714e9840372fa1b01daaf38f70d19a`（11,001,856 B）；
    collector `collector.exe` SHA-256 `a66933f2ba26a7585fa01243b49f091b694378c5ceead9c4be1bf9201a728e04`（2,140,226 B）
  - 构建来源：干净提交 `7c4c858274525dc2e5bc0904ab13284b6b2617e9`（`build-info.json` 中 `git_dirty=false`、`signed=false`）
- 随包发布 `SHA256SUMS.txt` 与 `build-info.json`（版本 / git commit / 构建时间 / 每个产物的 SHA-256）
  - 校验：`certutil -hashfile PeripheralBattery_0.1.2_x64-setup.exe SHA256`，与 `SHA256SUMS.txt` 对比
- **代码签名状态说明**：构建脚本支持 Authenticode（signtool）签名，但**本仓库不包含证书**；
  若构建时未配置 `PB_SIGN_*` 环境变量，发布包为 **NotSigned**，SmartScreen 可能显示“未知发布者”。
  校验请以上述 SHA-256 为准。签名方法见 README「发布与签名」。

## 本版本修复

### 1. 「点开后有时候会无法拖动」

**根因**：v0.1.1 只把 `data-tauri-drag-region="deep"` 挂在右上角标题区（`.side-head`）。
面板真正的视觉主体是左侧大号电量卡（hero）、底部名称与大量玻璃空白，用户自然从这些位置起手拖拽，
而这些区域不是拖拽区，窗口纹丝不动——这就是“有时候”拖不动的来源。

**修复**：拖拽区上移到整个面板（`#panel` 挂 `data-tauri-drag-region="deep"`）。
Tauri 2 的 `drag.js` 会自动排除 `button/input/a/label/select/textarea/summary/[tabindex]/role=button` 等
可交互元素，因此：hero 大卡、hero 名称、设备列表空白、底部提示、标题空白全部可拖；
设备行仍可点击选主卡，按钮仍可点击，设置抽屉的遮罩与抽屉内部显式退出拖拽（`="false"`），
点击遮罩仍能关闭抽屉。

**顺带修复手感**：面板全域可拖后，原来按下的瞬间就炸开的“大涟漪”会在每次拖动起手时出现
（Tauri 在 mousedown 进入原生窗口移动循环，拖动中页面收不到 pointerup）。
现改为「无位移的点击」才出大涟漪（pointerup、位移 < 6px、非交互目标）；按钮/设备行的小涟漪仍在按下时触发。

**真机验收**（真实 Win32 鼠标输入，125% DPI，[tools/verify_drag.py](../tools/verify_drag.py)）：

| 位置（CSS 坐标） | 修复前 | 修复后 |
|---|---|---|
| header 空白 (460,30) | 可拖 | 可拖 |
| hero 大卡 (120,120) | **不动** | 可拖（90 CSS px） |
| hero 名称 (120,330) | **不动** | 可拖 |
| 列表空白 (500,330) | **不动** | 可拖 |
| 设备行 (500,140) | 不动（点击选主卡） | 不动（点击选主卡） |
| 控制按钮 (650,32) | 不动（按钮） | 不动（按钮） |
| 第二实例唤出后立刻拖 | hero 区域失败 | header / hero 均可立即拖 |

修复前 8/12、修复后 **13/13 passed**；证据见 `design/verify/drag-native-before.json` 与
`design/verify/drag-native.json`（含窗口矩形前后对比）。

### 2. 设备下方显示「未知连接」

**根因**：LunaFury Luna33-W 通过 **2.4 GHz USB 接收器**（VID 373E / PID 0033）连接，
但采集器只知道“这是 LunaFury 鼠标”，没有把链路类型上报；`infer_transport()` 又没有 `lunafury` 规则，
只能给出诚实但无用的 `unknown`，界面于是显示「未知连接」。
（用户的另一台 FlyDigi BS2PRO 走蓝牙，所以一直正常显示「蓝牙」。）

**修复**：采集器链路类型改为“设备自己最清楚就自己报”：
`DeviceStatus` 新增 `transport` 字段；LunaFury provider 在检测到 0x0033 接收器时报 `2.4g`，
只插线（0x0032）时报 `usb`；`infer_transport()` 优先采用 provider 上报值，旧的启发式规则原样保留兜底。
Xbox 手柄等确实无法区分的设备仍显示「未知」（不猜测）。

**真机证据**（本机真实设备，`collector --probe`）：

```
lunafury:373e:0000:u1 "LunaFury Luna33-W" level=95% left_s=136800 left_s_src=spec ... transport=2.4g
```

修复前同一设备为 `transport=unknown`。证据见 `design/verify/transport-endurance.json/.log`。

### 3. 「剩余时间未知」：补足剩余使用时间计算

**根因**：剩余时间原本只来自“实测掉电外推”，要求同一设备**累计使用 ≥30 分钟且电量下降 ≥3%**
才开始估算。刚装好、刚充电、或设备用得少的时候 history 为空，界面只能一直显示「剩余时间未知」——
用户说的“补足剩余使用时间计算”正是指这种冷启动空档。

**修复**：新增标称续航目录 `collector/device_specs.py`，采集器在**没有实测数据时**用
“设备标称续航 × 当前电量比例”回填一个明确标注的估算，并在协议中新增 `left_s_src` 字段区分来源：

| `left_s_src` | 含义 | 界面显示 |
|---|---|---|
| `history` | 历史实测掉电外推（原有逻辑，最可信） | `约 X 小时 Y 分`（原样） |
| `spec` | 厂商标称续航估算（新增回填） | `≈ X 小时 Y 分`，悬停提示「按标称续航估算」 |
| `mains` | 外接供电设备，没有“剩余续航”概念 | `外接供电` |
| `""` | 无任何可用估算 | `剩余时间未知` |

标称续航目录当前收录两台设备：

- **LunaFury Luna33-W**：厂商标称电池容量 250 mAh，**没有公布官方续航小时**，
  故按 2.4G / 1kHz 游戏鼠标典型约 6 mA 电流估算满电约 **40 小时**（250 mAh ÷ 6 mA），
  来源与“这是估算”的说明写进目录注释；95% 电量显示 `≈ 38 小时`（`left_s=136800`）。
- **FlyDigi BS2PRO**：USB-C 供电的笔记本散热器，**没有电池续航**；
  Windows 蓝牙仍会暴露一个恒定的 94% “电量”，标为外接供电后界面显示「外接供电」，不再计算剩余时间。

当设备积累到足够的实测数据（≥30 分钟且掉电 ≥3%）后，估算自动切换为 `history` 实测值。
`left_s_src` 是**向后兼容的增量字段**：协议版本仍为 1；旧版 UI 忽略该字段时行为不变，
新版 UI 遇到旧版采集器时按原逻辑显示。

## 兼容性与升级

- 从 v0.1.1 直接覆盖安装即可：安装目录、注册表键、数据目录 `%APPDATA%\PeripheralBattery` 均不变，
  pinned / 别名 / 历史样本保留；无需卸载旧版。
- 协议版本仍为 1；`left_s_src` 为增量字段。新版采集器 + 旧版 UI、旧版采集器 + 新版 UI 均可运行。
- 系统要求不变：Windows 10 1809+ / Windows 11 x64，WebView2 Runtime ≥ 118。

## 已知限制

- **Luna33-W 的 40 小时是估算**：厂商未公布续航小时，实际续航随回报率、灯光、DPI、电池老化变化；
  该数字只用于冷启动阶段的“量级参考”，满 30 分钟 / 掉 3% 后自动切换为实测。
- **只有目录内设备有标称回填**：未收录的设备在冷启动时仍显示「剩余时间未知」；
  新增设备只需在 `collector/device_specs.py` 的 `SPECS` 追加一条（名称关键词、续航或 `powered`、来源、备注）。
- **BS2PRO 的 94% 是 Windows 蓝牙侧的静态值**，与真实供电状态无关；本版本选择显示「外接供电」，
  不再假装有剩余续航。
- **Xbox 手柄等 `unknown` 链路**：没有可靠 API 区分有线与无线适配器（需 WGI 的 `IsWireless`），
  仍显示「未知连接」而不猜测。
- 同时插着 2.4G 接收器和数据线时，链路按无线（`2.4g`）显示、充电状态单独显示（充电中优先）。
- 自动更新（updater）仍未接线；只能手动下载安装包升级。
- 其余与 v0.1.1 相同的限制（P7 24h 内存、P9 24h key 抖动、Win10/多屏/非 125% DPI、签名需证书、
  WebView2 离线安装等）见 README §7。

## 校验与排障

- 日志：`%APPDATA%\PeripheralBattery\logs\app.log`（含 `build=<git sha>`、`data_dir=`）、`collector.log`
- 数据：`%APPDATA%\PeripheralBattery\{app.json,config.json,aliases.json,history.json,logs\}`；
  剩余时间历史样本在 `history.json`（可删除以重新采集）
- 本版本回归：collector pytest **133 passed**、UI smoke **22 passed**、`cargo test` **55 passed**、
  真实鼠标拖拽 **13/13**（修复前 8/12）；安装包安装 / 运行 / 卸载验收 **24 项全 PASS**
  （`tools/verify_install_v012.ps1`，日志 `design/verify/v012-install.log`；安装版真实设备截图
  `design/verify/v012-ui-installed.png`），详见 README §6.1。

完整说明见仓库 [README.md](../README.md)、[PROGRESS.md](../PROGRESS.md)、[protocol.md](../protocol.md)。
