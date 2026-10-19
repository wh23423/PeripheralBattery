# 外设电量显示器 — 通信协议 v1

> 本文是 **collector (Python) ↔ app (Tauri/Rust) ↔ 前端 (Web)** 三方唯一契约。
> 实现与文档冲突时，以本文为准；修改协议先改本文，再改两端。

---

## 0. 进程图

```
collector.exe (Python, stdio NDJSON)  ⇄  app.exe (Tauri/Rust)  ⇄  WebView2 (ui/)
```

- collector 只和 Rust 说话：stdout 一行一个 JSON，stdin 一行一个命令。
- 前端只和 Rust 说话：`invoke()` 命令 + `listen()` 事件。**前端不直接读写 stdio。**
- 传输统一 UTF-8、`\n` 换行、单行 ≤ 1 MiB。超限丢行并重新同步。

---

## 1. Collector → App 事件（stdout NDJSON）

所有事件都有 `t`、`session`、`seq`（hello 除外，hello 的 seq 固定 0）。

```jsonc
// 握手，进程启动后立刻发一次；UI 收到后全量重置
{"t":"hello","protocol":1,"session":"<uuid4>","build":"<git-sha|unknown>",
 "capabilities":["approx","left_s","via","transport"],
 "devices":[Device, ...],
 "settings":{"poll":{"mouse":5,"keyboard":15,"headset":15,"gamepad":10,"steam":30},
             "providers":["logitech","razer",...],
             "low":20,"full_alert":true,"tick_interval":30.0}}
// capabilities 只列可选能力；replaces / stat / session 是 protocol 1 的必备语义，不列入。
// settings 中 `low` / `full_alert` / `tick_interval` 是 F-04 新增的附加字段
// （protocol 仍为 1，老 UI 忽略即可）：
//   poll          {category: 秒}   各类别轮询间隔
//   providers     [string]         实际启用的 provider
//   low           int 0..100       低电告警阈值（%），来自 collector 的 config.json，默认 20
//   full_alert    bool             充满通知开关，默认 true
//   tick_interval number           全量 tick 间隔（秒），默认 30.0

// 设备新增 / 更新（按 key upsert）
{"t":"device","session":S,"seq":12,"device":Device,"replaces":"旧key"}

// 设备消失（只能由全局 tick 依据 last_seen 发出）
{"t":"gone","session":S,"seq":13,"key":"..."}

// 心跳 / 全量对齐（默认 30s 一次，或收到 refresh 后）
{"t":"tick","session":S,"seq":40,"devices":[Device, ...]}

// 告警（collector 判定，UI 只展示）
{"t":"alert","session":S,"seq":41,"kind":"low|full|offline","key":"...","level":12,"text":"..."}
// 文案由 collector 生成：low →「<设备名> 电量仅剩 <N>%，建议充电」；
// full →「<设备名> 已充满」。UI 直接展示 text，不得自行拼接。

// 调度器自报指标（P3/P8 可观测；每 60s）
{"t":"stat","session":S,"seq":42,"providers":{"logitech":{"polls":12,"avg_ms":31,"last_ms":28}},
 "timers_per_s":0.2,"key_migrations":0}
```

规则：

- `seq` 单调递增；UI 以 `(session, seq)` 排序，乱序丢弃，`session` 变化 = 全量重置（**不重播入场动画**）。
- 只在**语义变化**时发 `device`：`level`、`charging`、`online`、`name`、`approx`、`left_s`、`left_s_src` 任一变。
- `replaces` 出现时，UI 做 morph（保留 DOM/位置/动画）而不是 delete+insert。
- 合并函数（蓝牙去重、控制器去重）只在**完整快照**上运行，绝不在增量里跑。

### Device

```jsonc
{
  "key": "logi:046d:c547:unit-12",  // 稳定 key，见 §3
  "name": "G502 X PLUS",
  "level": 68,             // int 0..100 | null = 未知
  "approx": "",            // 档位设备的展示文案，如 "约 55%（中）"
  "left_s": 6480,          // 剩余秒数；null = 不估算
  "left_s_src": "history", // 该 left_s 的来源（见下）；""|history|spec|mains
  "charging": false,
  "online": true,
  "kind": "mouse",         // headset|mouse|keyboard|gamepad|bluetooth
  "transport": "2.4g",     // 2.4g|usb|bluetooth|unknown
  "via": "",               // 控制器蓝牙来源，如 "bluetooth"
  "updated_unix": 1760000000
}
```

`left_s_src` 说明 `left_s` 是哪来的（**附加字段，protocol 仍为 1**，老 UI 可忽略）：

- `"history"`：本机实测掉电速率推算，UI 显示「≈ X 小时 Y 分」等真实估算（原有行为）。
- `"spec"`：设备刚接上、还没有实测历史时，按型号名义续航 × 当前电量做冷启动估算（上游标注为估算值）；UI 与 `history` 一样显示「≈ X 小时 Y 分」，不得当成实测。
- `"mains"`：设备是**外接供电**（如 USB-C 供电的笔记本散热器），`left_s` 恒为 `null`；UI 显示「外接供电」，**不得**显示电量百分比或剩余时间。
- `""`：`left_s` 为 `null` 且无以上含义（未知 / 充电中 / 离线）。

UI 必须处理 `level === null`：空心环 + `approx || "未知"`，**不得**按 `<= 15` 判红。

---

## 2. App → Collector 命令（stdin NDJSON）

```jsonc
{"t":"shutdown"}                              // 退出；Rust 等 2s 后 TerminateProcess
{"t":"refresh"}                               // 立即轮询全部 provider（并回 tick）
{"t":"refresh","provider":"logitech"}         // 只刷新一个 provider
{"t":"set","key":"...","field":"name","value":"客厅手柄"}   // 重命名（持久化）
{"t":"set","key":"...","field":"hidden","value":true}       // 隐藏设备
{"t":"set","field":"low","value":25}                        // 低电阈值 0..100（全局，无需 key）
{"t":"set","field":"full_alert","value":true}               // 充满提醒开关（全局，无需 key）
{"t":"get","what":"snapshot"}                 // 立即回 tick
```

`set` 规则（F-04）：

- `low` 只接受 int 0..100（含 0 和 100）；`full_alert` 只接受 bool；`name` 只接受字符串（空串 = 清除重命名），`hidden` 只接受 bool。
  非法值（越界、类型不符、缺 value）整条命令忽略，**不落盘**，collector 继续运行。
- 全局字段 `low` / `full_alert` 与设备无关，可省略 `key`（也接受空字符串）；`name` / `hidden` 必须带设备稳定 key（见 §3）。
- 成功后立即原子写 `config.json`，并触发一次 `refresh_from_settings()` + 全量 `tick`，UI 无需再发 `get`。
- UI 实际发送形状：`{"t":"set","field":"low","value":25}`、`{"t":"set","field":"full_alert","value":true}`（均无 `key`；全量设置字段见 `hello.settings`）。`low` 变化**即时生效**：写盘后立刻 `refresh_from_settings()` + `tick`，下一次告警判定即用新阈值。
- `tick_interval` 当前**只读**：仅经 `hello.settings` 下发，不接受 `set` 写入（发送会被忽略且不落盘）。
- 未知 `field` 忽略且不落盘。`protocol` 仍为 1：只加字段/命令形状，不改既有语义。

App 保证：启动时先把上一轮残留 collector 杀干净；退出/更新前先发 `shutdown` 并确认进程退出。

---

## 3. 稳定设备 key（P9）

```
key = provider ":" vid ":" pid ":" (serial | mac | unit_id | "u" + stable_ordinal)
```

- `serial` / `mac` / `unit_id` 全部小写、去分隔符后比较；优先序：serial > mac > unit_id > 持久化序号。
- `stable_ordinal` 来自 `%LOCALAPPDATA%\PeripheralBattery\aliases.json`：
  首次见到 `(provider, vid, pid, name)` 时分配递增序号，之后固定；删除设备不回收序号。
- **禁止**用 slot / idx / usb_count / 枚举顺序 / 进程内计数作为 key 组成部分。
- key 迁移（上游 key 变化但物理设备不变）时发 `replaces`，并在 aliases.json 记 `history`，供 P9 统计。

---

## 4. Rust ↔ 前端

本节新增的事件字段 / invoke 命令均为 protocol 1 的向前兼容扩展（只增不改，删/改才升版本）。

### 4.1 Rust → 前端事件

| 事件名 | payload | 说明 |
|---|---|---|
| `collector-message` | string（原始 JSON 行） | 前端 `JSON.parse` 后按 §1 处理 |
| `collector-state` | `{state:"starting"\|"ready"\|"exited"\|"error", pid?, code?, message?, protocol?, session?}` | sidecar 状态 |
| `app-state` | `{pinned:bool, acrylic:bool, settings:{...}, autostart:{...}, autostartLaunch:bool, platform:{windows11:bool, build:number}, build:string, version:string}` | 启动首帧、pin 变化、`update_settings` 成功后广播；完整形状见下 |
| `tray-action` | `{action:"refresh"\|"settings"\|"quit"\|"toggle-pin"}` | 托盘菜单动作 |
| `window-visibility` | `{hidden:boolean}` | Rust 在 `put_IsVisible` 前后发出；前端据此暂停/恢复动画与 rAF |

`app-state` 完整形状（S-01 / F-01；protocol 1 只增字段）：

```jsonc
{"pinned":false,"acrylic":true,
 "settings":{"opacity":60,"autostart":false,"notifyLow":true,"notifyFull":true},
 "autostart":{"enabled":false,"osDisabled":false,
              "path":"\"C:\\Users\\<u>\\AppData\\Local\\PeripheralBattery\\peripheral-battery.exe\" --autostart"},
 "autostartLaunch":false,
 "platform":{"windows11":true,"build":22631},
 "build":"<git-sha|unknown>","version":"0.2.0"}
```

- `acrylic`：WebView2 透明效果可用时为 true；远程会话 / 用户关闭透明 / `PB_DISABLE_ACRYLIC=1` 时为 false，前端应把面板底色调实（`html[data-acrylic="off"]`），不得假定透明一定可用。
- `settings`：`app.json` 中持久化的设置，唯一事实源由 Rust 写入。`opacity` 为 int 50..100（100 = 完全不透明），`autostart` 是**用户意图**，`notifyLow` / `notifyFull` 是两个通知开关。
- `autostart`：注册表运行时探测，只读不改。`enabled` = HKCU Run 值 `PeripheralBattery` 存在且命令中的 exe 路径匹配当前 exe；`osDisabled` = `...\Explorer\StartupApproved\Run` 同名值首字节表示被 Windows「启动项管理」禁用；`path` 为 Run 值原始数据，缺失时不出现该键。`enabled` 与 `settings.autostart` 可以不一致（实际注册表 vs 用户意图）。
- `autostartLaunch`：仅诊断用，表示本进程以 `--autostart` / `--hidden` 静默启动，**不**代表注册表已开启。

### 4.2 前端 → Rust 命令（invoke）

| 命令 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `ui_ready` | 无 | `{show:bool}` | 前端首帧完成；默认隐藏启动返回 `show:false`（只驻留托盘），`PB_SHOW_ON_START=1` 时返回 true 并显示面板（避免白闪） |
| `collector_send` | `{line:string}` | `boolean` | 写一行给 collector（§2）；含换行或 >1MiB 的行拒绝写入并返回 false |
| `collector_restart` | 无 | `boolean` | 重启 sidecar（版本握手失败时用） |
| `update_settings` | `{patch:{opacity?:number, autostart?:bool, notifyLow?:bool, notifyFull?:bool}}` | `{ok:boolean, settings:{opacity,autostart,notifyLow,notifyFull}, autostart:{enabled,osDisabled,path?}, error?:string}` | S-01：白名单 patch；`opacity` 越界 clamp 到 50..100；`autostart` 变化先写/删 HKCU Run 值（数据 `"<exe>" --autostart`），失败回滚并返回 `ok:false`+`error`；成功后原子写 `app.json` 并广播 `app-state` |
| `get_settings` | 无 | `{settings:{...}, autostart:{...}}` | S-01：读当前设置 + 自启动运行时探测（`settings`/`autostart` 形状同 `app-state`） |
| `set_pinned` | `{pinned:bool}` | `boolean` | 持久化到 `app.json` 并广播 `app-state`（注意：`config.json` 归 collector，二者不可混写） |
| `set_tray_state` | `{level:number\|null, charging:boolean, online:boolean, kind:string}` | 无 | 托盘图标状态色 |
| `window_action` | `{action:"hide"\|"minimize"\|"quit"}` | 无 | 关闭按钮 = hide |
| `collector_status` | 无 | `collector-state` 当前值 | 前端启动时主动拉一次 |

`update_settings` 规则（S-01）：

- `patch` 必须是对象，且只允许 `opacity` / `autostart` / `notifyLow` / `notifyFull` 四个键；含未知键整条拒绝，不做部分写入。
- 类型不符整条拒绝：`opacity` 必须是非负整数（小数 / 负数报错），小于 50 按 50、大于 100 按 100 clamp（不报错）；其余三项必须是 bool。
- `autostart` 写入顺序：先写/删注册表 Run 值 → 再原子写 `app.json`；写盘失败会把注册表回滚到旧意图，保证两端一致。注册表失败时不改内存、不写盘，直接返回错误。
- 失败返回 `{ok:false, error:<中文>, settings:<当前值>, autostart:<当前探测>}`，前端据此回滚控件；成功返回 `{ok:true, settings:<新值>, autostart:<新探测>}` 且已广播 `app-state`（前端无需再拉 `get_settings`）。
- `set_pinned` 行为不变（单一布尔，写 `app.json` 并广播 `app-state`）。

### 4.3 隐藏 / 显示顺序（不可颠倒）

隐藏：`put_IsVisible(FALSE)` → 发事件 `window-visibility {hidden:true}`（前端 pause WAAPI / 停 rAF / 不写 DOM）→ `window.hide()`。
显示：`window.show()` + `put_IsVisible(TRUE)` → 发 `window-visibility {hidden:false}` → 前端用最后快照渲染一帧 → 恢复动画。

### 4.4 设置单一事实源（S-01 / F-04）

| 归属 | 存储 | 字段 | 唯一读写方 |
|---|---|---|---|
| 外壳 / 进程层 | `%APPDATA%\PeripheralBattery\app.json`（schema 2） | `pinned` / `opacity` / `autostart` / `notifyLow` / `notifyFull` | Rust；前端经 `update_settings` / `get_settings` / `app-state` 间接读写 |
| 采集层 | 数据目录 `config.json`（运行时 `%APPDATA%\PeripheralBattery\config.json`，见 §6） | `low` / `full_alert` / `tick_interval` / `poll` / `providers` | collector；经 `hello.settings` 下发、`set` 命令写入 |
| 前端本地 | `localStorage` | 仅 `reducedMotion` | 前端 |

- 前端不得在 `localStorage` 再存一份 `low` / `full_alert` / `opacity` 等设置（避免影子语义）。旧 `pb.settings.v1` 里的 `lowThreshold` 只在启动时做一次性迁移：**仅当收到 `hello`、`hello.settings.low === 20`、旧键存在且值不等于 15** 时，转成 `{"t":"set","field":"low","value":<旧值>}` 发给 collector，随后删除该键；其余情况直接忽略并删除。
- Rust 不写 collector 的 `config.json`，collector 不写 `app.json`（避免两端 RMW 竞态互相覆盖）。
- 通知开关分工：`notifyLow` / `notifyFull`（app.json）决定 **Rust 侧是否弹 WinRT 系统通知**（`src-tauri/src/notifications.rs` 读取开关后调用 `tauri-plugin-notification`）；**前端不弹系统通知**，面板内 toast 是独立的 UI 提示（不归这两个开关管）。`low`（collector）决定 collector 何时发低电 `alert`；`full_alert`（collector）决定是否发充满 `alert`。两侧独立，互不改写。

---

## 5. 版本与错误

- `protocol = 1`；加字段不升版本，删/改字段升版本。
- Rust 校验 `hello.protocol === 1`；不符时向前端发 `collector-state.error`（文案「采集器版本过期」），并提供重启按钮。
- sidecar 退出时 Rust 发 `collector-state.exited`；5s 内未收到 hello 以 `error` 上报。
- `build` 用于诊断展示，不阻断运行。

---

## 6. 路径

```
%APPDATA%\PeripheralBattery\           # 数据目录；PB_DATA_DIR 可覆盖
  app.json                # 外壳/进程层配置（schema 2：pinned/opacity/autostart/notify*），Rust 写
  config.json             # 采集层配置（low/full_alert/tick_interval/poll/providers…），collector 写
  collector.pid.json      # sidecar 归属记录（Rust 写，残留清理用）
  aliases.json            # 稳定 key 映射 + 迁移历史（collector 写）
  history.json            # 剩余时间模型（collector 写）
  logs\app.log            # 5×2MB 轮转（Rust 写）
  logs\collector.log      # 5×2MB 轮转（Python stderr 线程写）
  diagnostics\            # --diag 输出
```

- 数据目录由 Rust 解析（`PB_DATA_DIR` 优先，默认 `%APPDATA%\PeripheralBattery`），再以同名环境变量传给 collector，保证两端同目录。
- 0.1.0 的旧目录 `%LOCALAPPDATA%\PeripheralBattery` 在首次启动时迁移一次（不覆盖已有文件）。
- collector 独立运行（`--once` / 手动 `--stdio`）且未设 `PB_DATA_DIR` / `--data-dir` 时仍默认 `%LOCALAPPDATA%\PeripheralBattery`（历史行为，仅开发/测试用）。
