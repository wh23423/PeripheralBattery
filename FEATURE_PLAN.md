# 外设电量显示器 · 项目总计划书（功能 · 动效 · 验收）

| 项目 | 内容 |
|---|---|
| 文档版本 | **v1.3（总计划书，待评审）** — v1.0 草案 + v1.1 动效/演示整合 + v1.2 透明度落地与执行顺序 + v1.3 低电提示两项（F-11/F-12） |
| 编写日期 | 2026-10-19（v1.1：2026-10-20；v1.2：2026-10-20；v1.3：2026-10-20） |
| 基线版本 | v0.1.2（`4abcd40`） |
| 文档性质 | 只做规划与技术决策，**本轮不改代码**；**本文件即项目总计划书**，排期/顺序/验收以它为准，不再维护第二份总计划 |
| 关联文档 | `BUILD_PLAN.md`、`TECH_PLAN.md`、`protocol.md`、`README.md`、`HANDOFF.md`；动效层：`design/motion/MOTION.md`、`design/motion/motion-lab.html`、`design/motion-preview.html` |

---

## 摘要

本次新增两项明确需求，另有 10 项从现有能力缺口里筛出的高价值建议：

| 编号 | 功能 | 优先级 | 目标版本 | 预估 |
|---|---|---|---|---|
| S-01 | 设置基础设施（统一持久化 + IPC + 抽屉分组） | P0 前置 | v0.2.0 | 2 人日 |
| **F-01** | **开机静默启动** | **P0** | **v0.2.0** | **1.5 人日** |
| **F-02** | **界面透明度调节** | **P0** | **v0.2.0** | **1.5 人日** |
| F-03 | 低电/充满系统通知 | P0 | v0.2.0 | 1.5 人日 |
| F-04 | 低电阈值统一（修掉双份阈值） | P0 | v0.2.0 | 0.5 人日 |
| F-05 | 设备别名与隐藏（后端已就绪，只差 UI） | P1 | v0.2.1 | 1 人日 |
| F-06 | 面板位置记忆与多屏适配 | P1 | v0.2.1 | 1.5 人日 |
| F-07 | 系统通知点击打开面板 / 免打扰 | P1 | v0.2.1 | 0.5 人日 |
| F-08 | 托盘菜单增强 | P2 | v0.2.1 | 0.5 人日 |
| F-09 | 轮询档位（省电/标准/实时） | P2 | v0.3.0 | 1 人日 |
| F-10 | 主题/高对比度与诊断导出 | P2 | v0.3.0 | 2.5 人日 |
| **F-11** | **隐藏态「同色系」低电提示（托盘红点 + 设备名 tooltip）** | **P1** | **v0.2.1** | **0.8 人日** |
| **F-12** | **低电「露头」：面板不抢焦点露 1.2s（默认关）** | **P2** | **v0.3.0** | **1.2 人日** |

> **v1.1 整合**：动效与演示统一收在 §21——v0.2.0 只启用 6 条「功能层过渡」，不整体启用潮息语言；与既有文档/约束的冲突及处置见 §22。
>
> **v1.3 整合**：按用户确认「两个都要」，把低电提示拆成两项——F-11 隐藏态同色系提示（托盘红点 + 设备名 tooltip，v0.2.1）、F-12 低电露头（面板不抢焦点露 1.2s，v0.3.0 且默认关）；面板打开时的低电 UI 属既有能力，作为 F-03 的验收基线。详见 §8.9 / §8.10。
>
> **v1.2 整合**：把「总计划」并回本文件——§4.2 补执行顺序，§7 补透明度四档数值、降级矩阵与六步落地，§3 补动效/性能硬约束，末尾补变更记录；删除临时的 `MASTER_PLAN.md`，避免两份总计划互相漂移。
>
> 关键结论先说：**开机静默启动不需要新依赖**（`windows-sys` 已启用注册表 feature）；**Win11 上 Tauri 无法调 acrylic 的色调/强度**（`WindowEffectsConfig.color` 只对 Win10 有效），所以“界面透明度”只能做在 CSS 面板底色层，并必须带一条“acrylic 不可用时锁定为不透明”的降级路径。

---

## 1. 背景与目标

### 1.1 需求来源

1. **开机静默启动**：面板默认就是“启动即隐藏、只驻留托盘”（`main.rs:154-157`、`commands.rs:11-27`），但目前没有任何开机自启手段，用户每次开机要手动点一次。需要“随 Windows 启动、不弹窗、只驻留托盘”。
2. **界面透明度**：面板是 720×380 的 acrylic 玻璃面板，底色 alpha 固定（约 0.52–0.62），浅色壁纸下偏暗、深色壁纸下偏亮，用户有“调淡一点/调实一点”的真实诉求。
3. **其他必要功能**：从代码与 `README §7 已知限制` 中筛出用户可感知的缺口，避免做成 20 项愿望清单。

### 1.2 目标

- 两项新需求以“设置项”形态进入现有设置抽屉，可持久化、可回滚、有验收标准。
- 其余建议功能按“用户价值 ÷ 成本”排序，给出明确的版本归属和依赖关系。
- 所有新增行为都能**完全关掉回到 v0.1.2 行为**。

### 1.3 非目标

- 不改变面板 720×380 的尺寸与不可缩放约束。
- 不改采集器协议版本（新增字段不升 `protocol`，见 `protocol.md §5`）。
- 不做安装器层面的自动开启（是否在安装器加“开机启动”勾选项见 F-01 开放问题）。
- 不做云同步、账号体系、插件系统。

### 1.4 成功标准

1. 设置抽屉里能完成两项新功能的开关/调节，重启后保留，卸载后无残留。
2. 开机自启触发时：无窗口、无白闪、无通知、不抢焦点；用户双击图标仍能正常唤起已有实例。
3. 透明度滑块 50%–100% 全程文字对比度 ≥ 4.5:1（深/浅壁纸各测一张）。
4. 全量测试与构建通过：`cargo test`、`pytest tests/collector`、`node tools/ui_smoke.js`、`python tools/verify_runtime.py`、`scripts/build.ps1`。
5. 隐藏态 CPU 仍满足 P2 目标（<0.5%，现测 0.318%），不新增 `backdrop-filter`。

---

## 2. 现状盘点

### 2.1 设置与持久化的“三份真相”

| 存储 | 位置 | 现有字段 | 写入方 | 问题 |
|---|---|---|---|---|
| 前端 `localStorage` | WebView2 profile | `lowThreshold`、`reducedMotion`、`pinned`（`ui/app.js:35,43,59`） | JS | 卸载清 profile 即丢；与 Rust 状态可能不一致 |
| app 配置 `app.json` | `%APPDATA%\PeripheralBattery\app.json` | 仅 `pinned`（`src-tauri/src/config.rs`） | Rust，原子写 tmp+rename | 字段太少，新增设置没地方放 |
| 采集器 `config.json` | 同目录 | `low=20`、`full_alert`、`tick_interval`、`poll`、`names`、`hidden` 等（`collector/settings.py`） | **collector 独占**，Rust 不写（audit P2-5 防 RMW） | UI 完全没接 `names`/`hidden`；`low` 与 UI 阈值分裂 |

**已经存在的 bug 级不一致（F-04 要修）**：UI 的“低电阈值”默认 15（`ui/app.js:43`），只影响主卡/告警样式（`ui/app.js:242`）；真正触发 `alert` 事件的是 collector 的 `low=20`（`collector/settings.py:93`、`collector/state.py:466`）。用户把滑块拖到 15，提醒仍在 20% 触发。

### 2.2 启动链路

- 窗口配置：`transparent:true`、`decorations:false`、`skipTaskbar:true`、`visible:false`（`tauri.conf.json`）。
- 默认隐藏启动：`ui_ready` 返回 `{show: state.pb_show_on_start || window_visible}`，只有 `PB_SHOW_ON_START=1` 才显示（`commands.rs:11-27`）。
- 单实例：`main.rs:43-88`，`AlreadyRunning` 时 `signal_existing` 激活已有面板。
- **没有任何开机自启代码**；`Cargo.toml` 无 autostart / notification / updater 插件；capabilities 只有 core 权限。
- 安装路径：`%LOCALAPPDATA%\PeripheralBattery\peripheral-battery.exe`（currentUser 安装，无需管理员）；升级覆盖同路径。
- 卸载：`nsis/installer-hooks.nsh` 删除 `HKCU "${MANUPRODUCTKEY}"`、WebView2 profile、按需删数据目录；**没有清 Run 键的逻辑**。

### 2.3 透明度的实现与硬边界

- 窗口 `windowEffects: { effects: ["acrylic"], state: "active" }`。
- `panel.rs:42-78 apply_acrylic_policy`：Win10 / `PB_DISABLE_ACRYLIC=1` / 系统“透明效果”关 / 远程会话 → `set_effects(None)` 且 `state.set_acrylic(false)`；结果通过 `app-state.acrylic` 下发，前端 `html[data-acrylic="off"]` 用 `--panel-solid:#0a1830` 实底降级。
- `.panel` 背景 = 噪点 SVG + 白色高光渐变 + 深蓝渐变（`ui/styles.css:136-158`）；无 acrylic 实底在 `:232-245`；`prefers-reduced-transparency` 实底在 `:248-255`。
- Tauri `WindowEffectsConfig` 只有 `effects/state/radius/color/interactive`，且官方文档写明 **`color` 只影响 Windows 10 v1903+ 的 Blur/Acrylic，Windows 11 无效**；`state/radius` 仅 macOS。
- **结论**：Win11 上无法通过 Tauri 调 acrylic 的色调/强度；调透明度只有三条路——(a) 调 CSS 面板底色 alpha（推荐）；(b) 改系统“透明效果”开关（影响全局，不可接受）；(c) 自写 `SetWindowCompositionAttribute`（未公开 API，风险高，见 §16 不做）。

### 2.4 适合“顺手做掉”的能力缺口

| 缺口 | 证据 | 价值 |
|---|---|---|
| 低电/充满告警只有面板内 toast，而应用默认隐藏 | `ui/app.js` alert→toast；应用启动即隐藏 | **用户根本看不到提醒**，最高价值 |
| 设备别名/隐藏后端已实现，UI 未接 | `collector/settings.py:317 apply_set` 支持 `name`/`hidden`；`collector.py:635` 已处理 | 低成本、直接可用 |
| 面板不记忆拖动位置 | `panel.rs:231-273` 每次按托盘/光标重新定位 | 多屏/固定位置用户高频抱怨 |
| suspend/resume 未接 `WM_POWERBROADCAST` | `README §7` | 唤醒后数据可能长时间不刷新 |
| 无“省电/实时”轮询档位 | `collector/settings.py` 有 `tick_interval`/`poll` | 笔记本用户关心耗电 |
| 托盘菜单只有 4 项 | `tray.rs:207-242` | 快捷操作入口偏少 |
| 无主题/高对比 | 面板固定深色 | 无障碍需求 |

---

## 3. 设计原则与硬约束

1. **单一事实源**：跟系统/进程行为有关的设置 → `app.json` + Rust（托盘也能改）；跟采集行为有关的设置 → `config.json` + collector；前端 `localStorage` 只保留纯观感缓存（`reducedMotion`），不再做第二事实源。
2. **隐藏态零开销**：任何设置变更不得在隐藏态写 DOM / 起动画（沿用现有 `state.hidden` 守卫与 `D.pending*` 排队机制，见 `ui/app.js:1300-1314`）。
3. **新增依赖从严**：优先用已启用的 `windows-sys` / `windows` feature；必须新增 crate 时（如通知插件）先在独立分支验证“离线可构建”，失败则走备选方案。
4. **可完全回滚**：每个功能都能关掉并回到 v0.1.2 行为；配置文件坏掉时按默认值启动，不阻塞。
5. **可读性优先于透明**：透明度滑块有下限，acrylic 不可用时锁定不透明；任何情况下正文对比度 ≥ 4.5:1。
6. **设置抽屉不扩窗**：抽屉仍是覆盖层、宽 306px、`drawer-body` 已有滚动（`ui/styles.css:1150-1159`）；新增项按分组塞入，不改变 720×380 外框。
7. **协议只增不改**：`app-state` 加字段、新增 invoke 命令、collector `set` 扩展字段，均不升 `protocol=1`。
8. **先验证再宣布**：每个功能以“验收脚本 + 手工用例通过”收尾，不以“代码写完”收尾。
9. **动效属性白名单**：只动 `transform` / `opacity` / `color` / `background-color` / `border-color` / `stroke-dashoffset`；**`box-shadow` 不进 transition**（需要光晕就预烘焙层、只动 opacity）；生产不新增 `backdrop-filter`。
10. **时长单一来源**：v0.2.0 的过渡时长只从 §21.2 参数表取，组件里不写魔法数；潮息令牌要等 v0.3 收编才启用。

---

## 4. 功能总览与版本归属

### 4.1 里程碑

| 版本 | 主题 | 内容 | 预估 |
|---|---|---|---|
| **v0.2.0** | 设置与启动 | S-01、F-01 开机静默启动、F-02 透明度、F-03 系统通知、F-04 阈值统一 | ~9 人日（含测试） |
| **v0.2.1** | 设备与位置 | F-05 别名/隐藏、F-06 位置记忆、F-07 通知交互、F-08 托盘增强、**F-11 隐藏态低电提示** | ~4.8 人日 |
| **v0.3.0** | 省电与打磨 | F-09 轮询档位、F-10 主题/诊断、**F-12 低电露头**、offline 告警、suspend/resume | ~7.2 人日 |
| Backlog | 观察后再定 | 多语言、电量历史曲线、更新检查、安装器勾选项 | — |

### 4.2 依赖关系

```
S-01 ──┬── F-01（需要 app.json 字段 + update_settings）
       ├── F-02（需要 app.json.opacity + app-state.settings）
       ├── F-03（需要通知开关持久化）
       ├── F-04（需要 collector set 扩展 + UI 设置读源）
       ├── F-05（设备管理 UI 依赖设置抽屉分组）
       └── F-06（位置记忆需要 app.json 字段）
F-03 ── F-07（通知点击/免打扰）
F-03 ── F-11（红点阈值与通知阈值共用 F-04 的 low）
F-03 ── F-12（露头由 alert 触发；免打扰共用 F-07）
F-08 ── 依赖 F-01/F-03（托盘菜单里的开关）
```

**执行顺序建议**：S-01 落地 → **F-04**（0.5 人日，最便宜地验证地基与写 collector 的路径）→ **F-02**（用户最关心，第二批交付）→ F-01 → F-03 → **F-11**（0.8 人日，依赖 F-03 的 alert 与 F-04 的 low，可紧跟 F-03）→ F-12（v0.3.0，需先验证不抢焦点）→ 统一回归、打包、release notes。F-01 与 F-03 都只依赖 S-01 的字段，可并行。

---

## 5. 前置：S-01 设置基础设施（P0，2 人日）

### 5.1 存储分层规则（写进 `docs/` 或 `TECH_PLAN.md`）

| 设置 | 事实源 | 原因 |
|---|---|---|
| 固定面板 `pinned` | `app.json`（现状） | 托盘可改，Rust 事实源 |
| 开机静默启动 `autostart` | `app.json`（意图）+ 注册表（实际） | 注册表是系统事实，app.json 是“用户希望” |
| 面板不透明度 `opacity` | `app.json` | 卸载/迁移/备份一致 |
| 通知开关 `notifyLow/notifyFull` | `app.json` | Rust 侧决定是否弹通知 |
| 低电阈值 `low` | **collector `config.json`** | `alert` 由 collector 产生，唯一来源 |
| 降低动效 `reducedMotion` | `localStorage`（保留） | 纯前端观感，无跨端语义 |
| 轮询档位 | collector `config.json` | 同 `low` |

### 5.2 `AppConfig` v2（`src-tauri/src/config.rs`）

保持向后兼容：旧文件 `{"pinned":true}` 能直接读；坏文件回落默认值并记日志（现有 `load()` 已具备）。

```rust
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AppConfig {
    #[serde(default = "default_schema")]
    pub schema: u32,              // 2；旧文件缺失按 2 处理即可
    #[serde(default)]
    pub pinned: bool,
    #[serde(default = "default_opacity")]
    pub opacity: u8,              // 50..=100，默认 60（= v0.1.2 观感）
    #[serde(default)]
    pub autostart: bool,          // 用户意图；实际状态以注册表为准
    #[serde(default = "default_true")]
    pub notify_low: bool,
    #[serde(default = "default_true")]
    pub notify_full: bool,
}
```

> 字段名用 snake_case，与现有 `pinned` 一致；`save()` 的原子写逻辑不变。注意 `config.rs` 顶部注释仍写着“只存 pinned / `%LOCALAPPDATA%`”，需同步更正为 `%APPDATA%` 与新字段。

### 5.3 IPC 契约（`ui/bridge.js` + `commands.rs` + `state.rs`）

**扩展事件 `app-state`（只增字段）**：

```json
{
  "pinned": true,
  "acrylic": true,
  "settings": { "opacity": 60, "autostart": false, "notifyLow": true, "notifyFull": true },
  "autostart": { "enabled": false, "osDisabled": false, "path": null },
  "platform": { "windows11": true, "build": 22631 },
  "build": "…",
  "version": "0.2.0"
}
```

`autostart` 子对象是**运行时探测结果**，与 `settings.autostart`（意图）分开：`enabled`=Run 值存在且路径匹配；`osDisabled`=任务管理器“启动项”里被禁用（StartupApproved）。

**新增 invoke 命令**：

| 命令 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `update_settings` | `{patch:{opacity?,autostart?,notifyLow?,notifyFull?}}` | `{ok:bool, settings, autostart, error?}` | Rust 侧白名单 + clamp 后存 `app.json` 并 emit `app-state`；`autostart` 失败时**回滚内存与磁盘并返回错误码** |
| `get_settings` | 无 | `{settings, autostart}` | 前端启动兜底拉取（正常靠 `app-state` 即可） |

保留 `set_pinned` 不删（托盘与旧前端逻辑仍在用）；内部改为复用同一保存/广播路径。

**前端接入点**：
- `handleAppState`（`ui/app.js:1104-1147`）：除 `acrylic/pinned` 外，新增应用 `settings.opacity`（只 `style.setProperty`，不算写 DOM）与通知开关 UI 同步。
- 抽屉控件变更 → 300ms 防抖 → `bridge.call('update_settings', {...})`；失败弹 toast 并把控件回滚。
- 隐藏态守卫沿用 `D.pending*` 模式，恢复显示时统一回填。

### 5.4 设置抽屉重构（`ui/index.html:104-143`）

306px 宽、`drawer-body` 已可滚动，按分组排列并加分隔线：

```
启动     [x] 开机静默启动          状态：已启用 / 被系统启动项管理禁用
外观     面板不透明度  ─────●──  60%
         [x] 降低动效（关闭柔光与涟漪）
提醒     [x] 低电系统通知   [x] 充满系统通知
         低电阈值  ───●────  20%     （F-04 统一后由 collector 值驱动）
面板     [x] 固定面板（失焦不隐藏）
         [x] 记住上次位置          （F-06）
设备     设备管理 ›                （F-05，二级视图）
────────
关于     采集器构建 / 协议版本 / 应用版本 / 平台 / 会话
```

无障碍：所有开关用原生 `input[type=checkbox]` + `<label>`；滑块有 `<output for>`；分组用 `<fieldset><legend>` 或 `role=group` + `aria-labelledby`；抽屉 `inert` 逻辑不变；`data-tauri-drag-region="false"` 保持不变。

### 5.5 迁移与回滚

- 首次运行 v0.2.0：`app.json` 缺失新字段 → 用默认值并写回（一次）。
- `localStorage` 的 `lowThreshold`：v0.2.0 首次启动读一次，作为“用户覆盖值”推给 collector；之后以 collector 为准（见 F-04）。
- 回滚到 v0.1.2：旧版本忽略未知字段（serde 默认行为），不会崩；注册表 Run 值需手工删（回滚说明写进 release notes）。

### 5.6 验收

- [ ] 旧 `app.json`（只有 `pinned`）启动正常，字段补默认值。
- [ ] `app.json` 写入损坏 JSON → 启动不崩、日志有记录、使用默认值。
- [ ] `update_settings` 对越界值 clamp（opacity 50–100），非法类型拒绝。
- [ ] 设置变更后立即 `app-state` 广播，托盘/再次打开抽屉状态一致。
- [ ] `cargo test` 新增 `AppConfig` 兼容性用例（旧 JSON、未知字段、坏 JSON）。

---

## 6. F-01 开机静默启动（P0，1.5 人日）

### 6.1 用户场景

| 场景 | 期望 |
|---|---|
| 开机登录 | 应用自动启动，只出现托盘图标，不弹面板、不抢焦点、不闪白 |
| 开机后手动双击图标 | 正常唤起面板；若自启实例已在跑，唤醒它而不是起第二个 |
| 设置里关闭开机启动 | 立即删除自启项，下次开机不再启动 |
| 卸载 | 自启项一并清除，重装后不会“偷偷自启” |
| 用户曾在任务管理器禁用启动项 | 设置里显示“被系统启动项管理禁用”，不静默改回 |

### 6.2 方案选型

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **HKCU `…\CurrentVersion\Run`** | 无需管理员；一行代码可读写；卸载易清；可用 `windows-sys` 零新依赖 | 可被任务管理器禁用（StartupApproved） | ✅ **采用** |
| 启动文件夹 `.lnk` | 直观 | 需 COM/ShellLink 或手写 lnk；清理易漏；AV 观感差 | ❌ |
| 计划任务 | 可延迟、可提权 | 需管理员；卸载残留难清；过度设计 | ❌ |
| Windows 服务 | 最早启动 | 完全不适合 GUI 托盘应用 | ❌ |

### 6.3 注册表契约

```
键：HKCU\Software\Microsoft\Windows\CurrentVersion\Run
值名：PeripheralBattery
类型：REG_SZ
数据："C:\Users\<user>\AppData\Local\PeripheralBattery\peripheral-battery.exe" --autostart
```

实现：`windows-sys` 的 `Win32_System_Registry`（`Cargo.toml` 已启用）——`RegGetValueW` 读、`RegSetKeyValueW` 写、`RegDeleteKeyValueW` 删。路径必须带引号（安装路径可能含空格），用 `std::env::current_exe()` 获取。

**任务管理器禁用检测**：`HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run`，同名值，`REG_BINARY` 12 字节；首字节 `0x02/0x06` = 启用，`0x03/0x07` = 禁用，其他值视为未知。只读展示，不主动改写（除用户在我们 UI 里切换时，见 6.5）。

### 6.4 启动链路与 `--autostart`

```
main()
 ├─ 解析 CLI：--autostart（静默自启）、--hidden（手动静默启动）
 ├─ 单实例检测
 │    ├─ AlreadyRunning + --autostart → 直接 exit(0)（绝不 signal_existing，避免抢焦点弹面板）
 │    ├─ AlreadyRunning + 普通启动   → signal_existing（现状）
 │    └─ 首次实例 → 继续
 ├─ 创建窗口（visible:false，现状）
 ├─ 启动 collector sidecar（现状）
 ├─ ui_ready：show = !silent && (pb_show_on_start || window_visible)
 └─ 不发通知、不弹 toast、不设置前台窗口
```

- `silent = args 含 --autostart || --hidden`；`--autostart` **优先于 `PB_SHOW_ON_START=1`**（调试请直接运行 exe 或只用 `PB_SHOW_ON_START`）。
- Rust 侧新增 `state.autostart_launch: bool`，随 `app-state` 下发仅作诊断；日志写一条 `autostart silent launch`。
- 不延迟 collector：登录时的采集已按 `poll` 错峰且 tick 1 Hz，CPU 实测 0.318%（P2），无需额外延迟逻辑（若实测登录风暴再议，见 6.10 可选）。
- 托盘 tooltip / 图标状态初始化流程不变，避免“托盘未就绪”竞态。

### 6.5 状态检测、同步与自愈

**事实源分工**：`app.json.autostart` = 用户意图；注册表 = 系统实际。启动时执行一次 `reconcile()`：

| 意图 | Run 值 | StartupApproved | 动作 |
|---|---|---|---|
| true | 存在且路径匹配 | 无 / 启用 | 什么都不做 |
| true | 存在但路径不匹配（换目录/重装） | — | 改写为当前 exe 路径 |
| true | 缺失 | — | 重建（自愈） |
| true | 存在 | 禁用 | 不改写；UI 显示“被系统启动项管理禁用” |
| false | 存在且值名=PeripheralBattery | — | 删除 Run 值（清残留） |
| false | 缺失 | — | 不做 |

**UI 切换**（`update_settings {autostart}`）：
- 开启：写 Run 值 → 读回校验 → **删除 StartupApproved 值**（否则用户之前在任务管理器禁用过，会永远不启动）→ 保存 `app.json`.
- 关闭：删 Run 值 + 删 StartupApproved 值 → 保存 `app.json`.
- 任一步失败：回滚 `app.json`、恢复开关、弹 toast“无法写入启动项（可能被安全软件拦截）”。

### 6.6 UI 设计

```
启动
  [x] 开机静默启动
      已启用，登录 Windows 后自动在托盘运行
      ⚠ 已被 Windows 启动项管理禁用（打开任务管理器 → 启动应用 可恢复）
```

- 状态文案由 `app-state.autostart` 驱动，三态：已启用 / 未启用 / 被系统禁用。
- “被系统禁用”时开关仍显示为关，旁边给一键打开任务管理器的入口（`ms-settings:startupapps` 或 `taskmgr /0 /startup`，P1 可延后）。
- 开发版（`target\debug`）允许开启，但副文案提示“仅对当前 exe 生效”。

### 6.7 失败处理与边界

| 情况 | 处理 |
|---|---|
| 安装路径含空格/中文 | 数据值整体加引号；`cmd` 解析测试覆盖 |
| 安全软件拦截写注册表 | 返回错误、回滚、toast 提示，不静默失败 |
| 用户删除/移动安装目录 | 下次启动 reconcile 检测路径不匹配并重写；目录不存在则 UI 提示重装 |
| 开机瞬间双击图标 | 普通启动 `signal_existing` 唤醒，不产生第二个实例 |
| 已运行又触发自启 | `--autostart` 分支静默 `exit(0)` |
| 便携版（无安装目录） | 允许，但明确“换目录后需重新开启” |
| 漫游/多用户 | HKCU 天然按用户隔离，无需额外处理 |

### 6.8 卸载清理（`src-tauri/nsis/installer-hooks.nsh`）

在现有 `DeleteRegKey HKCU "${MANUPRODUCTKEY}"` 之前/之后追加：

```nsis
DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PeripheralBattery"
DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "PeripheralBattery"
```

同步修改 `tools/verify_install_uninstall.ps1`：安装→开启自启→卸载→断言 Run 值不存在。

### 6.9 验收与测试

**Rust 单测（不碰真实注册表）**：把 `reconcile` 抽成纯函数 `plan_reconcile(intent, run_value, approved) -> Action`，覆盖 6.5 表全部 6 行 + 路径比较（大小写不敏感、引号、环境变量展开）。

**集成脚本 `tools/verify_autostart.ps1`**：
1. 开启自启 → 断言 Run 值存在、数据路径 = 当前 exe、带 `--autostart`；
2. 启动 `peripheral-battery.exe --autostart` → 断言进程在、主窗口 `IsWindowVisible == false`、无新面板；
3. 再次 `--autostart` → 断言没有第二个进程、已有实例窗口仍未显示；
4. 关闭自启 → 断言 Run 值删除；
5. 恢复现场。

**手工用例**：重启登录看托盘；任务管理器禁用后设置页提示；卸载后 `reg query` 无残留。

### 6.10 工作量拆分与可选增强

| 子项 | 估时 |
|---|---|
| `autostart.rs`（读写/探测/reconcile/单测） | 0.75 人日 |
| `main.rs` CLI + 单实例分支 + `ui_ready` show 逻辑 | 0.25 人日 |
| `AppConfig` 字段 + `update_settings` + `app-state` | 0.25 人日（S-01 内） |
| UI 开关/状态/错误提示 | 0.25 人日 |
| NSIS + 验收脚本 + 文档 | 0.5 人日 |

可选增强（不进 v0.2.0）：安装器勾选“开机启动”；`--autostart` 下延迟 3s 启动采集器降低登录竞争；任务管理器启动项跳转按钮。

---

## 7. F-02 界面透明度调节（P0，1.5 人日）

### 7.1 术语澄清（避免做错东西）

| “透明度”可能指 | 是否本次做 | 说明 |
|---|---|---|
| 窗口本身透明 | 已具备 | `transparent:true` + acrylic |
| acrylic 模糊的**色调/强度** | ❌ Win11 不可调 | Tauri 的 `color` 字段对 Win11 无效；不碰未公开 API |
| **面板底色不透明度**（叠在模糊之上的深蓝层） | ✅ 本次做 | 滑块控制 CSS alpha，安全、可逆、立即见效 |
| 文字/描边透明度 | ❌ | 会破坏可读性，不做 |

### 7.2 技术边界（已核实）

- `ui/styles.css:136-158` 深蓝渐变三档 alpha：`0.60 / 0.52 / 0.62`。
- `WindowEffectsConfig.color` 文档明确仅 Win10 v1903+ 生效；Win11 上改不了 acrylic。
- 所以滑块只调“深蓝底色层”的 alpha，白色高光/噪点/边框保持不变；acrylic 关闭时底色被实底覆盖，滑块自然失效（锁定并提示）。

### 7.3 交互设计

```
外观
  面板不透明度  ─────────●──  60%
  （越往左越透明，越往右越实；100% 为不透明深蓝）
  [x] 降低动效
```

- 控件：`<input type="range" id="set-opacity" min="50" max="100" step="1">` + `<output>`，默认 **60**（= v0.1.2 观感，避免“升级后视觉突变”）。
- 动效与可交互预览见 §21：`design/motion-preview.html`（浏览器打开即可拖滑块看 50–100 的真实观感）。
- 实时预览：`input` 事件即改 CSS 变量（仅改样式，不触发布局）；`change`/防抖 300ms 后持久化。
- 100% = 完全不透明（深蓝渐变，接近现有 `--panel-solid` 但不改变降级路径）；50% = 最透，仍保证可读。
- acrylic 不可用（Win10 / 系统透明关闭 / 远程会话 / `PB_DISABLE_ACRYLIC=1` / `prefers-reduced-transparency`）时：滑块 `disabled`，副文案“当前系统不支持玻璃模糊，已锁定为不透明背景”。

### 7.4 实现方案

**CSS 变量由 JS 计算并写入 `document.documentElement`：**

```js
/* v=50..100；60 为设计基准，>60 时向 1.0 插值，保证 100 真正不透明 */
function panelAlphas(v) {
  const base = [0.60, 0.52, 0.62];
  return base.map(b => v <= 60
    ? b * v / 60
    : b + (1 - b) * (v - 60) / 40);
}
```

```css
/* styles.css: .panel 第三层渐变改为变量 */
linear-gradient(155deg,
  rgba(14, 34, 74, var(--panel-a1, 0.60)) 0%,
  rgba(7, 16, 38, var(--panel-a2, 0.52)) 55%,
  rgba(10, 24, 52, var(--panel-a3, 0.62)) 100%);
```

- 变量只在这一个渐变上使用；`.panel::before` 镜面高光、边框、内阴影、噪点层不动。
- 默认值写在 CSS 里（`0.60/0.52/0.62`），即使 JS 未执行也与 v0.1.2 一致。
- Rust 只存整数 `opacity`，不参与渲染；`app-state.settings.opacity` 下发，前端在隐藏态仅 `setProperty`（与现有 `acrylic` 属性写法同类，不触发重排）。
- 降级路径优先级（CSS 已存在，保持）：`html[data-acrylic="off"] .panel { background-color: var(--panel-solid) }`；`prefers-reduced-transparency` 同理。此时渐变 alpha 变化被实底遮住，视觉不变 → 滑块禁用。

### 7.4.1 四档映射值（验收时照表比对）

| 档位 | a1 / a2 / a3 | 预期观感 |
|---|---|---|
| 50% | 0.500 / 0.433 / 0.517 | 最透，壁纸纹理明显，正文仍可读（下限） |
| 60%（默认） | 0.600 / 0.520 / 0.620 | 与 v0.1.2 完全一致，升级不突变 |
| 80% | 0.800 / 0.760 / 0.810 | 玻璃感减弱，文字更稳 |
| 100% | 1.000 / 1.000 / 1.000 | 完全不透明深蓝，看不到壁纸 |

### 7.4.2 降级矩阵（什么时候锁死滑块）

| 条件 | 面板 | 滑块 | 文案 |
|---|---|---|---|
| acrylic 可用（Win11 默认） | 变量生效 | 可用 | 正常 |
| acrylic 不可用（Win10 / 系统「透明效果」关 / 远程会话 / `PB_DISABLE_ACRYLIC=1`） | `html[data-acrylic="off"] .panel` 实底 `--panel-solid` | **禁用** | 「当前系统不支持玻璃模糊，已锁定为不透明背景」 |
| `prefers-reduced-transparency: reduce` | 强制实底 | **禁用** | 同上（系统偏好优先） |
| 桌面窗口管理器组合关闭（罕见） | 回落实底 | 禁用 | 同上 |

实现要点：滑块 `disabled` 时保留当前值（不重置），避免恢复系统能力后用户设置丢失；降级判断复用现有 `data-acrylic` 属性，不新增探测代码。

### 7.4.3 六步落地（每步可独立回滚）

1. **CSS 变量化**（0.2d）：`ui/styles.css:143` 三个 alpha 改为 `var(--panel-a1/2/3, 原值)`；先不接 JS，确认与 v0.1.2 逐像素一致。
2. **应用函数**（0.15d）：`ui/app.js` 新增 `applyPanelOpacity(v)`（clamp + 公式 + `setProperty`），在 `handleAppState`（`ui/app.js:1112`）调用；初始化先读一次 `app-state`。
3. **降级联动**（0.1d）：函数内检查 `root.dataset.acrylic === 'off'` 与 `matchMedia('(prefers-reduced-transparency: reduce)')`，禁用滑块并加提示；acrylic 状态变化时重新求值。
4. **设置项 UI**（0.1d）：抽屉「外观」分组加滑块 + output + 禁用文案；`aria-valuetext` 同步。
5. **持久化与 IPC**（0.2d，依赖 S-01）：`change` 防抖 300ms → `bridge.call('update_settings',{patch:{opacity}})`；失败回滚控件并 toast。
6. **验收与截图**（0.5d）：四档 × 三种壁纸截图、对比度计算、`PB_DISABLE_ACRYLIC=1` 与 Win10 模拟路径、隐藏态 CPU 复测。

**回滚**：删掉变量声明即回到固定 alpha（CSS 默认值兜底）；`app.json.opacity` 对旧版是未知字段，serde 自动忽略。

### 7.5 可读性与无障碍

- **下限 50%**：低于此值在浅色壁纸上正文对比度不足；此时三档 alpha 为 0.500 / 0.433 / 0.517（见 §7.4.1），叠上 0.10 的白色高光层后等效深色底约 0.45–0.55，配合 acrylic 模糊仍在可读区——但仍以实测为准。
- 验收时必须截图取色验证：正文 `--t1`、次要文字 `--t2` 在纯白与纯黑壁纸下对比度 ≥ 4.5:1（用 `tools/shot.js` 截图 + 取色脚本）。
- 系统“降低透明度”（`prefers-reduced-transparency: reduce`）优先于用户滑块：强制实底，滑块禁用。
- 键盘：滑块可 Tab 聚焦，方向键 ±1，`Home/End` 到 50/100；`aria-valuetext` 输出“60%，面板不透明度”。

### 7.6 持久化与性能

- 存 `app.json.opacity`（S-01），随 `app-state` 下发；无 localStorage 第二份。
- 拖动时只改 3 个 CSS 自定义属性，Chrome 对自定义属性变更只重绘受影响层，不触发重排；60fps 无压力。
- 隐藏态不会拖动滑块（抽屉不可见），无需额外守卫；`app-state` 在隐藏态应用 opacity 也只 `setProperty`，与 `data-acrylic` 同模式。

### 7.7 验收与测试

- [ ] 滑块 50/60/80/100 四档截图，壁纸深/浅各一张；100% 时完全看不到壁纸。
- [ ] 重启应用后恢复上次值；改 `app.json` 外部值后重启生效。
- [ ] `PB_DISABLE_ACRYLIC=1` 启动：滑块禁用、面板实底、无视觉跳变。
- [ ] Win10（或模拟 `windows11=false`）路径：同上。
- [ ] 系统开启“降低透明度”：强制实底、滑块禁用。
- [ ] 对比度检查通过（7.5）。
- [ ] 无新增 `backdrop-filter`（`grep -r backdrop-filter ui/` 为空）。
- [ ] 隐藏态 CPU 无回归（`tools/verify_fps.js` / P2 脚本）。

### 7.8 工作量拆分

| 子项 | 估时 |
|---|---|
| CSS 变量化 + JS 计算/应用/降级禁用 | 0.5 人日 |
| 设置项 + 持久化 + 同步（S-01 配合） | 0.25 人日 |
| 对比度与多壁纸验收 | 0.5 人日 |
| 文档/截图 | 0.25 人日 |

### 7.9 可选（P2，不进 v0.2.0）

- “悬停时自动加深面板”：`pointerenter` 临时 +0.15 透明度系数，移出恢复；默认关，避免干扰截图/录屏。
- “玻璃模糊开关（仅本应用）”：关闭后运行期 `set_effects(None)`，省电/兼容模式；需要处理窗口已显示时的重设闪烁，单独评估。

---

## 8. F-03 低电/充满系统通知（P0，1.5 人日）

**痛点**：应用默认隐藏，`alert` 事件只走面板内 toast（`ui/app.js` alert→toast），用户实际看不到低电提醒——这是当前最大的功能断点。

**方案**
- 首选 `tauri-plugin-notification`（WinRT Toast）；Rust 收到 collector `alert` 后直接弹系统通知，不依赖 WebView 是否可见。需要新增依赖，先在分支验证离线 `cargo build`，并确认 NSIS 安装的快捷方式带 AppUserModelID（Tauri 默认处理）。
- 备选（依赖不可用/通知被禁）：托盘图标闪烁 + 面板显示时补 toast；或启用 `windows` crate 的 `Win32_UI_Shell` 做 `Shell_NotifyIcon` 气泡（需要拿到托盘 `HWND`，Tauri 未直接暴露，成本高）。
- 去重：collector 已有“每放电周期一次、高于 `low+5` 重新武装”的逻辑（`collector/state.py:472-479`），UI/Rust 侧再加 10 分钟同设备同类型节流，防重启刷屏。
- 开关：`notifyLow` / `notifyFull` 存 `app.json`，设置抽屉“提醒”分组。
- 通知内容：`设备名 电量仅剩 12%，建议充电` / `设备名 已充满`；设备名用 alias（F-05）优先。
- 点击打开面板：v0.2.1 视插件能力做（F-07）；不做也不影响主流程。

**验收**：隐藏态触发低电 → 系统通知出现、托盘不弹窗；关掉开关后不再通知；重启后去重状态不重复轰炸；Windows 专注助手开启时系统自动抑制（不额外处理）。

**面板内低电（既有能力，作为 F-03 的验收基线，不新增开发）**：主卡环转红（`#ff5c6c`）、`.alert-tag` 显示「建议充电」、`.panel.is-low` 红色泛光、面板内玻璃 toast 4.2s 自动消失；状态切换按 §21.2 的 250ms 预烘焙高光层过渡；心跳动画只在面板可见时运行，隐藏态不跑，性能契约不变。隐藏态的可见性由 F-11（红点 + tooltip）与 F-12（露头）补足。

**风险**：新增依赖需联网；未签名应用 Toast 可能显示通用名称——需实机确认，必要时在 NSIS 里写 AppUserModelID 快捷方式。

---

### 8.9 延伸 F-11：隐藏态的「同色系」低电提示（P1，v0.2.1，0.8 人日）

**为什么是「同色系」而不是「同风格」**：应用隐藏时用户面对的是 Windows 托盘，玻璃面板本体没有出场位置。所以隐藏态能复用的不是面板材质，而是面板已经建立的颜色与图标语言：低电红 `#ff5c6c`、设备类型 glyph、环形电量条。做到这三点，托盘与面板读起来就是同一个产品；要让「玻璃面板本体」在隐藏态出现，那是 F-12 的事。

**现状（可直接复用，不新增依赖）**
- 托盘图标由 Rust 运行时按状态渲染 SVG（`src-tauri/src/tray.rs:120-166` `build_svg` / `render_tray_icon`），不是静态 PNG；
- 托盘 tooltip 已存在（`src-tauri/src/tray.rs:195-203` `tooltip_for`）；
- 隐藏态由 `src-tauri/src/tray_mirror.rs` 独立驱动，不依赖 WebView；镜像里已有全部设备的 `level / charging / online / kind`（`DeviceLite`，`:23-29`），只缺 `name`。

**方案**
1. **低电红点**：`build_svg` 末尾追加 4.4px 圆点（`fill #ff5c6c` + 1px 深色描边，与主环低电同色），条件 `online && !charging && level <= low`，位置放图标右上角空白（cx 27.6 / cy 5.6），不遮电池与 glyph。
2. **tooltip 带设备名**：`DeviceLite` 增加 `name`（collector 的 `device` 事件已带 `name`，`upsert` 里解析即可），`TrayState` 增加 `name`；`tooltip_for` 在低电时输出 `低电 · Arctis Nova 7 12%`，其余维持现状。`name` 只由 `tray_mirror` 填充；前端 `set_tray_state` 命令签名不变（不传 name），该路径下 tooltip 回退现状。
3. **阈值来源**：红点用的 `low` 由 Rust 从 F-04 扩展后的 `hello.settings.low` 缓存（默认 20），阈值变更随 `hello`/`app-state` 刷新，不在 Rust 里写死。
4. **不做托盘闪烁**：托盘重绘走 `Shell_NotifyIcon`，每 2s 闪一次在任务栏上过于吵；低电的“动”留给面板内的心跳与 F-12 的露头。
5. **契约**：`tray-state` 事件只**增** `name` 字段，不删不改，不升 `protocol`；`update_tray_from_devices` 的 payload 去重串同步纳入 `name`。

**验收**：低电 → 红点出现且 tooltip 含设备名；充电 / 离线 / 未知 → 无红点；阈值跟随 F-04 统一后的 collector `low`；多台同时低电时 tooltip 取最低者；隐藏 5 分钟 CPU 不回归（P2）。

---

### 8.10 延伸 F-12：低电「露头」提示（P2，v0.3.0，1.2 人日，默认关）

**目标**：隐藏态也能看到一次与面板同材质的「一眼信息」——玻璃面板滑入停留 1.2 秒后退出，不抢焦点、不进入交互。

**方案**
- **触发条件（需全部满足）**：收到 `alert(kind=low)`；`low_peek=true`；面板当前隐藏；距上次露头 > 10 分钟；距登录 > 60 秒（避免与 F-01 自启竞争）；前台窗口不是全屏（`GetForegroundWindow` 尺寸不近似等于屏幕）。
- **呈现**：新增 `platform::show_no_activate(win)`（`ShowWindow(hwnd, SW_SHOWNOACTIVATE)`），复用 `show_panel` 的定位、尺寸还原、WebView2 Resume 逻辑，但**跳过 `set_focus()`**，保留 `suppress_for_ms(450)`；1.2s 后 `hide_panel(force=false)`。
- **动效**：入场 320ms（`--m-5`）+ 8px 位移，退场 240ms（`--m-4`）；`prefers-reduced-motion` 或 `data-reduced-motion` 时去掉位移、停留时长不变。
- **去重与让位**：同设备 10 分钟一次；面板已可见或抽屉打开时不露头；F-07 免打扰时段内不露头。
- **风险与止损**：`SW_SHOWNOACTIVATE` 对 WebView2 不保证完全不激活（`set_focus` 只是显式一步，去掉后系统仍可能激活）；全屏游戏/演示会判定为扰动。所以**默认关、v0.3.0 才做**，先在分支验证「触发后 `GetForegroundWindow` 的 HWND 不变」，不达标就降级为「托盘红点 + 系统通知」。
- **回滚**：开关关闭完全不触发；删除 `show_no_activate` 调用即回到普通 `show_panel`。

**验收**：开启后低电 2s 内露头，前台窗口 HWND 不变；1.2s 后隐藏且无残留窗口；关闭开关完全不触发；reduced-motion 无位移；全屏场景不触发。

---

## 9. F-04 低电阈值统一（P0，0.5 人日）

**现状**：UI 15% / collector 20% 两个值（`ui/app.js:43` vs `collector/settings.py:93`），告警在 20% 触发，UI 样式在 15% 变化，用户困惑。

**方案**
1. collector `apply_set` 扩展白名单：`low`（0–100 int）、`full_alert`（bool）；沿用 `LIMITS` 校验（`collector/settings.py:111`）。
2. **补协议下发**：`state.settings_payload()` 目前只返回 `{poll, providers}`（`collector/state.py:572-574`），需扩展为附带 `low`、`full_alert`、`tick_interval`，随 `hello` 下发（加字段不升 `protocol`）。UI 启动/collector 重启后以 `hello.settings` 为初值；拖动后发 `{"t":"set","field":"low","value":N}` 并乐观更新，无需额外 ack（collector 重启会重新对齐）。
3. 旧 `localStorage.lowThreshold` 仅在首次升级时读取：若 collector 仍是默认 20 且 localStorage 非 15，则推送一次用户值；否则忽略并删除该键。
4. 删除前端 `threshold()` 对 collector 值的“影子”语义（`ui/app.js:242` 改为读取同步后的值）。

**验收**：滑块 25% → 电量 24% 时系统通知/主卡告警同时触发；重启后保持；`config.json` 里 `low` 变化；pytest 补 `apply_set` 边界用例（越界、非整数、未知字段）。

---

## 10. F-05 设备别名与隐藏（P1，1 人日）

**现状**：后端 `apply_set` 已支持 `name`/`hidden` 并持久化（`collector/settings.py:317`、`collector/collector.py:635`），`migrate_key` 还会把别名/隐藏跟着设备 key 迁移（`:342`），但 UI 完全没接。

**方案**：设置抽屉 →“设备管理”二级视图（复用抽屉宽度，列表滚动）：
- 每行：设备名 + 提供方 + 当前电量 + “重命名”输入 + “隐藏”开关；
- 重命名：`collector_send {"t":"set","field":"name","key":…,"value":"…"}`，空值 = 恢复默认名；
- 隐藏：`field:"hidden"`；主面板列表与托盘 tooltip 跳过隐藏设备，抽屉底部显示“已隐藏 N 台，点击管理”；
- 隐藏设备仍保留其 key 与配置，重新显示后别名不丢（后端已保证）。

**验收**：改名后主卡/列表/通知/托盘全部用新名；隐藏后列表不显示但仍能恢复；重启后保留；pytest 补 `apply_set` 空名/超长名（建议 40 字符上限，超长截断）用例。

---

## 11. F-06 面板位置记忆与多屏适配（P1，1.5 人日）

**现状**：`panel.rs:231-273 position_panel` 每次显示都按托盘矩形/光标重新定位；`compute_panel_window_position` 已有负坐标/125%/小工作区单测（`panel.rs:665+`），但不记忆用户拖动位置，多屏未实测。

**方案**：
- `app.json` 增 `remember_position: bool`（默认 **false**，保持现状行为）+ `position: {x, y, work_area:{x,y,w,h}, monitor_id?}`；
- 记忆的是**相对工作区锚点**（如右下角偏移），而不是绝对屏幕坐标，换分辨率/DPI/任务栏位置后仍合理；
- 显示时若记忆点超出当前工作区（拔掉副屏），夹取到主屏可见区域；
- 设置抽屉加“记住上次位置”开关 + “重置位置”链接；
- 拖动结束（`tauri://move` 防抖 500ms）保存，只在 `remember_position=true` 时生效。

**验收**：拖动→隐藏→显示位置不变；重启后不变；125%/150% DPI 与双屏拔插不越界；单元测试覆盖“记忆点在已移除显示器”的夹取。

---

## 12. F-07 通知点击打开面板 / 免打扰（P1，0.5 人日）

- 通知点击后显示面板（若插件支持 Windows 激活回调；否则保持系统通知纯提示）。
- “免打扰时段”（如 23:00–07:00）存 `app.json`，Rust 侧判断后抑制 Toast；面板内 toast 不受影响。
- 验收：时段内不弹系统通知但面板仍显示告警；跨天边界正确。

---

## 13. F-08 托盘菜单增强（P2，0.5 人日）

现有菜单只有 刷新 / 固定面板 / 设置 / 退出（`tray.rs:207-242`）。追加：
- “开机静默启动”勾选项（与 F-01 同一写路径）；
- “低电通知”勾选项（F-03）；
- “低电时让面板露头”勾选项（F-12，默认不勾）；
- “显示/隐藏面板”；
- 刷新项显示“上次刷新 xx:xx”（tooltip 已有电量，不重复）。

验收：菜单状态与设置抽屉双向同步（Rust 事实源，`app-state` 广播）；托盘菜单在隐藏态可操作。

---

## 14. F-09 轮询档位（P2，1 人日）

- 三档：省电（`tick_interval=60`，外设 poll 翻倍）/ 标准（现状 `tick_interval=30`）/ 实时（`tick_interval=15`，仅接入电源时可选）；
- 写 collector `set`（需扩展白名单，与 F-04 同一批）；设置抽屉显示“当前刷新间隔”；
- 验收：切换后 `stat.timers_per_s` 与 CPU 有对应变化；重启保留；pytest 校验 `LIMITS` 边界。

---

## 15. F-10 主题/高对比与诊断导出（P2，2.5 人日）

- **高对比模式**：`html[data-contrast="high"]` 提升 `--t2/--t3` 与边框对比、去掉低透明装饰；比“浅色主题”成本低、收益明确。浅色主题会牵动整套玻璃视觉，放 Backlog。
- **诊断导出**：设置“关于”里加“导出诊断”，把 `logs/` + `app.json` + collector `config.json`（序列号可打码）+ 平台信息打 zip 到桌面；支撑排障。
- 验收：高对比模式对比度 ≥ 7:1；导出包不含明文 MAC/serial（脚本检查）。

---

## 16. 明确不做或推迟

| 事项 | 决定 | 理由 |
|---|---|---|
| Win11 上调节 acrylic 色调/强度 | ❌ 不做 | Tauri 不支持；`SetWindowCompositionAttribute` 未公开、随系统更新失效、有崩溃风险 |
| 自绘毛玻璃/`backdrop-filter` | ❌ 不做 | 违反性能约束（生产 0 backdrop-filter）；隐藏态 CPU 会恶化 |
| 全局快捷键 | ❌ 暂不做 | 与游戏/其它工具冲突；托盘单击已足够 |
| 云同步/账号 | ❌ 不做 | 与本地工具定位冲突 |
| 插件系统 | ❌ 不做 | 维护成本远超收益 |
| 电量预测/健康度 | ❌ 不做 | 多数外设不提供足够数据，误导用户 |
| 安装器默认开启自启 | ⏸ 待定 | 默认开启易被视为流氓行为；建议默认关，安装器可选勾选（开放问题） |
| 浅色主题 | ⏸ Backlog | 视觉重做成本高；先做高对比 |
| 多语言 i18n | ⏸ Backlog | 用户群以中文为主；安装器已有中英 |
| 更新检查（updater） | ⏸ Backlog | 需代码签名与发布基础设施；先用手动下载 |

---

## 17. 风险登记表

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | 安全软件拦截写 Run 键 | 中 | 高 | 写入后读回校验；失败回滚 + 明确提示；不重试骚扰 |
| R2 | StartupApproved 与 Run 不一致导致“开了不启动” | 高 | 中 | 开启时删除 StartupApproved；启动时探测并提示；脚本化用例 |
| R3 | 自启实例抢焦点/弹面板 | 中 | 高 | `--autostart` 静默退出分支 + 集成测试；绝不复用 `signal_existing` |
| R4 | 透明度导致文字不可读 | 中 | 中 | 下限 50%、实底降级、双壁纸对比度验收 |
| R5 | 通知插件引入新依赖、离线构建失败 | 中 | 高 | 分支先行验证；备选托盘气泡/面板 toast；不阻塞 F-01/F-02 |
| R6 | 设置抽屉 306px 放不下新增项 | 高 | 低 | 已可滚动；分组 + 二级视图；控件紧凑化 |
| R7 | 卸载残留 Run 值 | 中 | 中 | NSIS 显式删除 + `verify_install_uninstall` 断言 |
| R8 | 多屏/DPI 位置记忆错位 | 中 | 中 | 相对工作区锚点 + 夹取 + 单测；默认关闭该功能 |
| R9 | 通知刷屏 | 中 | 中 | collector 已有去重 + 侧 10 分钟节流 + 开关 |
| R10 | 升级后视觉/行为突变 | 中 | 低 | 新功能默认值 = v0.1.2 行为（opacity=60、自启关、位置记忆关） |
| R11 | 托盘红点在深色任务栏上不显眼 | 中 | 低 | 圆点描 1px 深色边；16px 图标下保持 4.4px 直径；截图验收 100%/125% 缩放 |
| R12 | 露头抢焦点/全屏游戏被打断 | 高 | 高 | 默认关；`SW_SHOWNOACTIVATE` + 跳过 `set_focus`；前台 HWND 断言；不达标降级为红点 + 系统通知 |

---

## 18. 待办清单

> 勾选即完成；“文件落点”为预期改动位置，实际以实现为准。优先级 P0 全部进 v0.2.0。

### P0 · v0.2.0

**S-01 设置基础设施**
- [ ] `src-tauri/src/config.rs`：`AppConfig` 扩字段（schema/opacity/autostart/notifyLow/notifyFull），serde 默认值兼容旧文件
- [ ] `src-tauri/src/config.rs`：补单测（旧 JSON、未知字段、坏 JSON、原子写）
- [ ] `src-tauri/src/commands.rs` + `state.rs`：新增 `update_settings` / `get_settings`；`app-state` 扩 `settings`/`autostart`
- [ ] `ui/bridge.js`：命令白名单；`ui/app.js handleAppState` 应用新字段
- [ ] `ui/index.html` + `ui/styles.css`：设置抽屉分组（启动/外观/提醒/面板/设备/关于）
- [ ] `protocol.md §4`、`TECH_PLAN.md`：契约更新

**F-01 开机静默启动**
- [ ] 新建 `src-tauri/src/autostart.rs`：Run 键读写、StartupApproved 探测、`plan_reconcile` 纯函数 + 单测
- [ ] `src-tauri/src/main.rs`：`--autostart`/`--hidden` 解析；单实例 AlreadyRunning 静默退出；`ui_ready` show 逻辑
- [ ] `src-tauri/src/state.rs`：`autostart` 运行时状态 + payload
- [ ] `ui/index.html` + `ui/app.js`：开关、三态文案、失败回滚
- [ ] `src-tauri/nsis/installer-hooks.nsh`：卸载清 Run + StartupApproved
- [ ] `tools/verify_autostart.ps1` + `tools/verify_install_uninstall.ps1` 扩展
- [ ] 文档：README 环境变量/行为、release notes

**F-02 界面透明度**
- [ ] `ui/styles.css`：`.panel` 深蓝渐变改用 `--panel-a1/2/3` 变量（默认值同现状）
- [ ] `ui/app.js`：`panelAlphas()` 计算、`input` 实时应用、防抖持久化、降级禁用逻辑
- [ ] `ui/index.html`：滑块 + output + 说明文案 + 降级提示
- [ ] 验收截图（50/60/80/100 × 深/浅壁纸）+ 对比度检查脚本
- [ ] 确认无新增 `backdrop-filter`，隐藏态 CPU 无回归

**F-03 系统通知**
- [ ] 分支验证 `tauri-plugin-notification` 可离线构建（失败则切备选）
- [ ] `src-tauri` alert 转发 → 系统通知；开关读 `app.json`
- [ ] `ui/index.html` 提醒分组开关；面板内 toast 保留
- [ ] 通知节流（同设备同类型 10 分钟）；实机验证未签名/专注助手行为

**F-04 阈值统一**
- [ ] `collector/settings.py apply_set` 支持 `low`/`full_alert`；pytest 边界用例
- [ ] `collector/state.py settings_payload()` 增加 `low`/`full_alert`/`tick_interval`，随 `hello` 下发
- [ ] `ui/app.js`：滑块读写 collector `low`；首次迁移 localStorage 值
- [ ] `protocol.md`：`set` 字段表与 `hello.settings` 字段更新

### P1 · v0.2.1

- [ ] F-05 设备管理二级视图（重命名/隐藏）+ pytest
- [ ] F-06 `remember_position`/`position` 字段、相对锚点保存、越界夹取、重置按钮
- [ ] F-07 通知点击/免打扰时段（视插件能力）
- [ ] F-08 托盘菜单：开机启动、通知、显示/隐藏，与抽屉双向同步
- [ ] F-11 `tray.rs build_svg` 低电红点；`tray_mirror.rs` DeviceLite 补 `name`；`tooltip_for` 带设备名；`tray-state` 增字段
- [ ] F-11 托盘图标/tooltip 截图验收（100%/125%/150% 缩放、深色任务栏）

### P2 · v0.3.0+

- [ ] F-09 轮询档位（collector set 白名单扩展 + UI）
- [ ] F-10 高对比模式、诊断导出（脱敏）
- [ ] offline 告警（collector 已有 `ALERT_KINDS`，补状态机与 UI）
- [ ] suspend/resume（`WM_POWERBROADCAST`）刷新链路
- [ ] F-12 `platform::show_no_activate` + 露头状态机（默认关）+ 前台 HWND 断言
- [ ] F-12 全屏/免打扰/登录 60s 让位逻辑与验收
- [ ] 更新检查、安装器自启勾选项、浅色主题（Backlog 排序另议）

---

## 19. 测试矩阵与全局 DoD

| 层级 | 命令/方式 | 通过标准 |
|---|---|---|
| Rust 单测 | `cargo test`（`src-tauri`） | 全绿；新增 config/autostart 用例 |
| collector | `pytest tests/collector` | 全绿；新增 apply_set 边界 |
| UI 冒烟 | `node tools/ui_smoke.js` | 15 项全过；新增设置项用例 |
| 运行时验收 | `python tools/verify_runtime.py` | 全过；隐藏态 CPU 不回归 |
| 安装/卸载 | `scripts/build.ps1` + `tools/verify_install_uninstall.ps1` | 安装可用、卸载无 Run/数据残留 |
| 手工 | 重启登录、任务管理器启动项、深浅壁纸、双屏/DPI、Win10 降级 | 见各功能验收 |

**发布 DoD**：以上全绿 + 文档（README/protocol/release notes）更新 + 截图/验收记录进 `design/verify/` + 回滚说明。

---

## 20. 已确认决策（2026-10-19）

| 问题 | 决策 | 状态 |
|---|---|---|
| 开机静默启动默认开还是关？ | **默认关**（用户显式开启） | ✅ 已确认 |
| 不透明度默认值 | **60%**（= v0.1.2 观感，升级不突变） | ✅ 已确认 |
| 低电/充满通知默认开？ | **开**（当前最大功能断点） | ✅ 已确认 |
| 隐藏态低电提示要做成「面板同材质」还是「同色系图标语言」？ | **两者都要**：F-11 红点 + tooltip（v0.2.1），F-12 露头（v0.3.0，默认关） | ✅ 已确认（2026-10-20） |

其余待定项（不阻塞 v0.2.0 开发）：

| 问题 | 建议 |
|---|---|
| 安装器是否提供“开机启动”勾选项？ | v0.2.0 先不做，观察反馈；后续默认不勾 |
| 不透明度下限 | 50%（40% 有对比度风险） |
| 位置记忆默认开？ | 关（保持现状行为） |
| 悬停自动加深面板 | v0.2.0 不做，列入可选增强 |

---

## 21. 动效与演示（v1.1 整合）

本章是 v1.1 的核心增量：把**功能计划（本文）**、**「潮息」动效语言**、**真机实验室**与**三项功能的交互原型**收进同一套权威边界、同一张参数表。

结论先行：**v0.2.0 只启用 6 条「功能层过渡」，不整体启用动效语言**；原型只对交互时序负责，不对像素负责；动效语言整体保留为 v0.3 候选。

### 21.1 四层交付与权威边界

| 层 | 文件 | 回答什么问题 | 权威性 |
|---|---|---|---|
| 功能与版本 | 本文 `FEATURE_PLAN.md` | 做什么、何时做、怎么验收 | **唯一事实源** |
| 动效语言 | `design/motion/MOTION.md` + `tokens.css` | 怎么动、为什么这样动、完整语汇 | 动效规范唯一事实源；v0.2.0 不整体启用 |
| 真机实验室 | `design/motion/motion-lab.html`（`lab.css` / `lab.js`） | 在 720×380 真实面板上验证运动 | **视觉与运动的权威基准**（直接复用 `ui/styles.css`） |
| 功能交互原型 | `design/motion-preview.html` + `.gif` / `.webp` | 三项功能的交互流程、时序、手感 | 只对**交互**负责；视觉是简化 mock，不参与验收 |

三条边界：

1. **原型不反向约束视觉**：preview 里的浅色壁纸、简化面板、`backdrop-filter` 都是 mock；真机视觉以 `ui/` + motion-lab 为准。
2. **视觉不反向约束时间**：动效时长只从 §21.2 与 `tokens.css` 取值，不允许在组件里写魔法数。
3. **冲突降级顺序**：功能约束 > 动效语言 > 原型观感。即：性能/无障碍/协议优先，其次语言一致性，最后才是原型里“看起来更顺”的个别参数。

### 21.2 v0.2.0 最小启用集（实现时照此落地）

这 6 条是**功能层过渡**，写在目标组件自己的 CSS 里；**不使用** `--m-*` 令牌、**不新增 keyframes**、**不引入** 1.8s 呼吸时钟。

| 元素 | 时长 / 曲线 | 属性 | 来源 | 备注 |
|---|---|---|---|---|
| 设置抽屉滑入/滑出 | 260ms `cubic-bezier(.22,.61,.36,1)` | `transform`（+200ms opacity） | 现网已有 | 关闭时 `visibility` 延迟 260ms，避免闪 |
| 抽屉遮罩 | 200ms 同曲线 | `opacity` | 现网硬切 → 新增 | 打开即可点空白关闭 |
| 开关（开机静默启动 / 通知） | 160ms `cubic-bezier(.2,.8,.2,1)` | `transform`（thumb）、`background-color` | 新增 | 文案同步切换，颜色变绿提示“已启用” |
| 不透明度滑块 | 无过渡（跟手）；thumb 按下 140ms `scale(1.15)` | CSS 变量 `--panel-a1/2/3` | 新增 | 只改样式不重排；持久化仍 300ms 防抖 |
| 面板内 toast | 280ms `--ease` | `opacity` + `translateY(6px)` + `scale(.98)` | 现网已有 | 沿用，不重写 |
| 系统通知（mock 演示） | 280ms 滑入 / 240ms 淡出 | `transform` + `opacity` | mock | 真机由 Windows 通知中心动画接管，不自绘 |
| 低电卡片状态 | 250ms | `border-color` + **预烘焙高光层 `opacity`** | 新增 | **不写 `box-shadow` 过渡**（见 §21.4）：高光层常驻、只动透明度，等价观感且不触发布局/重绘 |

> 系统通知那一行是**演示 mock**，不计入 6 条实现量：真机由 Windows 通知中心渲染与播放动画，产品不自绘。

原则：只动 `transform` / `opacity` / `color` / `background-color` / `border-color`；不新增 `backdrop-filter`；通知与静默启动都不得抢焦点（`--autostart` 不激活窗口）。

> F-11 的托盘红点是**静止标记**，不参与动效；F-12 的露头 320ms 入 / 240ms 出只在 v0.3 启用，v0.2.0 不实现。

### 21.3 与潮息语言的关系：v0.2.0 并存，v0.3 收编

**v0.2.0 不做的事**：不启用 `tokens.css`（它仍是 `html[data-motion="tidal"]` 作用域的 opt-in 层，删除属性即回滚）；不做主环双层呼吸、潮痕、液面、心跳、FLIP 场景语汇；不统一现有 `.hero-card` / `.row` 的 hover 时长。

**v0.3 收编映射**（先复验，再替换，禁止静默改）：

| v0.2.0 实测 | 潮息令牌 | 差异 |
|---|---|---|
| 260ms 抽屉 | `--m-4` 240ms | −20ms |
| 200ms 遮罩 | `--m-3` 180ms | −20ms |
| 160ms 开关 | `--m-3` 180ms | +20ms |
| 140ms thumb | `--m-2` 120ms | −20ms |
| 280ms toast | `--m-5` 320ms | +40ms |
| 250ms 低电高亮 | `--m-4` 240ms | −10ms |
| 320ms 露头入场 / 240ms 退场（F-12） | `--m-5` 320ms / `--m-4` 240ms | 0 |

规则：① 每次只收编一类交互，单独提交；② 必须先在 motion-lab 与真机录屏里对照通过；③ ±40ms 的观感差异要写进提交说明，不能“看起来差不多”就过。

**为什么现在不统一**：v0.2.0 的三个功能要与现有面板共存，任何全局动效改动都会扩大回归面（性能四契约、隐藏暂停、250Hz 屏）；先交付功能，再统一语汇。

### 21.4 降低动效与性能：两阶段

- **v0.2.0 沿用现网**：`prefers-reduced-motion` 或设置开关触发时，过渡/动画压到 `0.01ms`，抽屉直接出现、通知直接显示（preview 已复现）。它满足“不播放动画”的无障碍底线，且**零回归**。
- **v0.3 采用潮息做法**：循环层停成静帧、变化层保留 **120ms opacity 衰减**，让用户仍能感知“东西变了”，只是不去“看它怎么飞过来”。切换前必须在真机 + 关闭 Windows“动画效果”两种环境下各验一次。
- **性能硬约束（v0.2.0 内即生效）**：`box-shadow` 不进 transition（低电高亮用预烘焙层，见 §21.2）；`ui/` 保持 0 个 `backdrop-filter`；隐藏态四契约不变（pause 全部动画、停 rAF、清 timer、不写 DOM）。
- 新增过渡必须可被 `getAnimations().pause()` 暂停——隐藏面板时不能有动画继续跑。

### 21.5 演示资产（2026-10-20 实测）

| 文件 | 实测 | 用途 |
|---|---|---|
| `design/motion/MOTION.md` | 384 行 | 动效语言规格与落地清单 |
| `design/motion/tokens.css` | 507 行 | 可启用动效层（v0.2.0 不启用） |
| `design/motion/force-motion.css` | 243 行 | 演示页「强制播放」覆盖层（`tools/build_force_motion.py` 生成，仅评审用） |
| `design/motion/motion-lab.html` + `lab.css` + `lab.js` | 292 + 918 + 1146 行，页面高 ≈3300px | 720×380 真机面板 + 12 场景 + 自动巡演 + 时间轴解剖台 |
| `design/verify/motion/reel-1-breath.gif` | 560×309，约 2.6MB，8.3fps | 常态呼吸 → 主卡交接 → 列表增删 |
| `design/verify/motion/reel-2-charge-low.gif` | 560×309，约 3.0MB，8.3fps | 充电接入 → 低电心跳 |
| `design/verify/motion/motion-reel.webm` | 18s，裁切 720×380 | 逐帧核对 |
| `design/motion-preview.html` | 786 行，900×600 独立页 | 三项功能交互原型（可切壁纸 / Win10 降级 / 降低动效 / 播放演示） |
| `design/motion-preview.gif` | 780×520，54 帧，首帧 730ms + 其余 60ms ≈ 9.96s，约 6.0MB | 静态平台演示 |
| `design/motion-preview.webp` | 约 0.28MB，同内容 | 文档 / 网页优先用 |
| `tools/motion_preview.js` | Playwright + CDP screencast | 逐帧录制预览页 |
| `tools/encode_motion.py` | Pillow，全局调色板 + 时间轴重采样 | 编码 GIF / 动画 WebP |

说明：preview 的 `backdrop-filter`、浅色壁纸、简化面板都是**演示用 mock**；生产面板模糊仍由 Tauri 窗口 acrylic 提供，`ui/` 继续 0 个 `backdrop-filter`。

### 21.6 已决问题（原 21.4）

| 问题 | 决定 | 理由 |
|---|---|---|
| v0.2.0 是否整体启用 `tokens.css`？ | **否**，只取 §21.2 六类过渡 | 收窄回归面；功能优先 |
| `MOTION.md` 是否并入 `docs/`？ | **否**，留在 `design/motion/`；实现后 `docs/` 只留一页链接 | 设计说明与代码同仓，便于对照 |
| preview 能否作为视觉验收依据？ | **不能**，只验交互时序；视觉以 motion-lab + 真机截图为准 | preview 是简化 mock，且未复用 `ui/styles.css` |
| 降低动效是否改成 120ms 衰减？ | **v0.3 再改**，v0.2.0 保持 0.01ms | 避免无障碍行为在相邻版本反复横跳 |
| 样片参数以什么为准？ | **以实测为准**（见 §21.5），旧稿的 15fps / 5.7MB 作废 | 编码参数由重采样决定 |

### 21.7 动效部分的验收

**v0.2.0 验收**

- 六类过渡全部 ≤280ms；只动 §21.2 列出的属性；组件里没有魔法数（时长集中在组件顶部或 `tokens.css` 之外的变量区）。
- 抽屉关闭不闪、遮罩可点空白关闭、开关状态与文案同步。
- 降低动效下：无位移、无缩放、无 `filter`，状态变化仍可见。
- 隐藏面板后 `document.getAnimations()` 里无 running 项，rAF 停、timer 清。
- 帧时间 p99 不劣于基线 +10%（复用 `tools/perf.js`）；无新增依赖。
- `ui/` 仍 0 个 `backdrop-filter`；`box-shadow` 仍未进入任何 transition。

**v0.3 准入门槛**

- motion-lab 12 场景全绿（`tools/lab_check.js`），动画计数与 `MOTION.md` §11 口径一致。
- 主环双层呼吸 / 潮痕 / 液面 / 心跳在真机 720×380 上截图对照通过。
- 降低动效的 120ms 衰减在真机与 Windows“关闭动画效果”两种环境各验一次。

---

## 22. 整合记录（v1.1，2026-10-20）

记录 v1.0 计划书、潮息动效语言、两个演示页与既有工程约束之间的冲突及处置。**本轮只整合文档，产品代码零改动。**

| # | 冲突 / 不一致 | 处置 |
|---|---|---|
| 1 | 抽屉 260ms（计划/现网）vs 潮息 320ms / 240ms | v0.2.0 保留 260ms；v0.3 收编为 `--m-4` 240ms（§21.3） |
| 2 | 开关 160ms vs 潮息 120 / 180ms | 保留 160ms，v0.3 收编为 `--m-3` 180ms |
| 3 | 低电卡片计划写 `box-shadow` 过渡，违反「box-shadow 不进 transition」硬约束 | 改为「预烘焙高光层 opacity + border-color」250ms（§21.2 / §21.4） |
| 4 | 降低动效：计划 0.01ms vs 潮息 120ms 衰减 | 两阶段：v0.2.0 保持 0.01ms，v0.3 再切（§21.4） |
| 5 | preview 视觉与真机差异大（浅壁纸、简化面板、独立样式） | 新增四层权威边界（§21.1）：preview 只验交互，不验视觉 |
| 6 | v1.0 §21 称“两套演示都只复刻视觉与动效”，表述不准确 | 改写为：motion-lab 是视觉权威，preview 是交互原型（§21.1 / §21.5） |
| 7 | GIF 参数写“15fps / 约 5.7MB”，实测 54 帧、首帧 730ms、其余 60ms、≈9.96s、约 6.0MB | 以实测为准（§21.5），旧数字作废 |
| 8 | 动效文档清单写 3 个文件，实际 6 个（另有 `lab.css` / `lab.js` / `force-motion.css`） | 两处文档统一为完整清单（`MOTION.md` §15 已同步） |
| 9 | preview 用 `--pa1/2/3`，计划用 `--panel-a1/2/3` | 生产按计划命名；preview 变量仅 mock（`MOTION.md` §16.6） |
| 10 | 低电阈值双份（UI 15% vs collector 20%） | 已在 F-04 范围，动效层不重复处理 |
| 11 | 面板内 toast 覆盖 footer 提示（现网既有） | v0.2.0 不动（不属功能范围），登记为 v0.3 布局项 |
| 12 | `FEATURE_PLAN.md` 未出现在 `HANDOFF.md` 交接文件表 | 已在 `HANDOFF.md` §2 补一行；`PROGRESS.md` 属 v0.1.2 验收记录，不改 |
| 13 | 计划 §7.4 的 `--panel-a1/2/3` 与 preview 的滑块映射数值一致（base `[.60,.52,.62]`，v≤60 线性放大） | 确认一致，生产直接采用；`MOTION.md` §16 不重复定义 |
| 14 | 潮息 `tokens.css` 含 4 个未使用 token（`--line` / `--line-soft` / `--glass` / `--glass-hi`，来自现网未用令牌） | 保留（可启用层不追求零冗余），v0.3 收编时清理 |

**v1.1 改动范围**：`FEATURE_PLAN.md`（本章与文档头）、`design/motion/MOTION.md`（§10 补两阶段说明、§15 补 preview、新增 §16）、`HANDOFF.md` §2 一行、`design/motion-preview.html` caption 一行。**`ui/`、`collector/`、`src-tauri/` 零改动，`tokens.css` 未启用。**

---

## 附录 A：注册表命令速查

```bat
:: 查看
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PeripheralBattery
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" /v PeripheralBattery

:: 手动开启（等价于设置里打开）
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PeripheralBattery /t REG_SZ ^
        /d "\"%LOCALAPPDATA%\PeripheralBattery\peripheral-battery.exe\" --autostart" /f

:: 手动关闭
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PeripheralBattery /f
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" /v PeripheralBattery /f
```

## 附录 B：设置字段字典（v0.2.0）

| 字段 | 类型 | 范围/默认 | 存储 | 说明 |
|---|---|---|---|---|
| `schema` | u32 | 2 | app.json | 迁移用 |
| `pinned` | bool | false | app.json | 现有，托盘可改 |
| `opacity` | u8 | 50–100 / 60 | app.json | 面板不透明度 |
| `autostart` | bool | false | app.json | 用户意图；实际以注册表为准 |
| `notify_low` | bool | true | app.json | 低电系统通知 |
| `notify_full` | bool | true | app.json | 充满系统通知 |
| `low_peek` | bool | false | app.json | F-12 低电露头（默认关，v0.3.0） |
| `low` | int | 0–100 / 20 | config.json（collector） | 低电阈值，唯一事实源 |
| `full_alert` | bool | true | config.json（collector） | 充满告警开关 |
| `names` | map | — | config.json（collector） | 设备别名（F-05） |
| `hidden` | list | — | config.json（collector） | 隐藏设备（F-05） |

## 附录 C：参考位置

- 启动/单实例：`src-tauri/src/main.rs:43-88,154-157`；`src-tauri/src/commands.rs:11-27`
- 设置与配置：`ui/app.js:35-70,1104-1147,1300-1314`；`src-tauri/src/config.rs`；`collector/settings.py`
- 透明度：`ui/styles.css:136-158,232-255`；`src-tauri/src/panel.rs:42-78`
- 托盘：图标 SVG 渲染 `src-tauri/src/tray.rs:120-166`、tooltip `:195-203`、创建 `:207-242`；隐藏态设备镜像 `src-tauri/src/tray_mirror.rs`
- 卸载：`src-tauri/nsis/installer-hooks.nsh`
- 告警逻辑：`collector/state.py:464-491`
- 既有测试：`tests/collector/`、`tools/ui_smoke.js`、`tools/verify_runtime.py`、`tools/verify_install_uninstall.ps1`

## 附录 D：变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-19 | v1.0 | 用户初版功能计划书：S-01、F-01 开机静默启动、F-02 界面透明度、F-03..F-10；§20 默认值确认 |
| 2026-10-20 | v1.1 | 整合「潮息」动效语言与两个演示页：新增 §21（四层权威边界 / v0.2.0 最小启用集 / v0.3 收编映射 / 两阶段 reduced-motion / 生产禁令 / 演示资产）、§22（14 条冲突处置）；四层清单 6 个文件 |
| 2026-10-20 | **v1.3（低电提示两项）** | 按「两个都要」新增 **F-11 隐藏态同色系低电提示**（§8.9：托盘红点、设备名 tooltip、`tray-state` 只增 `name`）与 **F-12 低电露头**（§8.10：`SW_SHOWNOACTIVATE` 露 1.2s、默认关、v0.3.0）；F-03 补「面板内低电」既有能力验收基线；摘要/里程碑/依赖/风险 R11-R12/待办/决策/附录 B·C/§21.2·§21.3 同步 |
| 2026-10-20 | **v1.2（总计划版）** | 本文件升级为**项目总计划书**：§3 补动效属性白名单与时长单一来源两条硬约束；§4.2 补执行顺序；**§7 补 7.4.1 四档映射值、7.4.2 降级矩阵、7.4.3 六步落地与回滚**；删除临时的 `MASTER_PLAN.md`，避免两份总计划漂移；登记界面修正（设置图标太阳→齿轮，`ui/index.html`，仅图标无行为变化） |

关于「产品代码零改动」的例外：2026-10-20 按用户确认，`ui/index.html` 的设置按钮图标由太阳改齿轮（一行 SVG，无行为变化），其余 `ui/`、`collector/`、`src-tauri/` 仍为零改动；动效语言未应用。

---

*本文档为**总计划书 v1.2**；§20 三项默认值已于 2026-10-19 确认，可进入实现。动效与演示见 §21，冲突处置见 §22，透明度落地见 §7。*
