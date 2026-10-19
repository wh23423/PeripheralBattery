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
             "providers":["logitech","razer",...]}}
// capabilities 只列可选能力；replaces / stat / session 是 protocol 1 的必备语义，不列入。

// 设备新增 / 更新（按 key upsert）
{"t":"device","session":S,"seq":12,"device":Device,"replaces":"旧key"}

// 设备消失（只能由全局 tick 依据 last_seen 发出）
{"t":"gone","session":S,"seq":13,"key":"..."}

// 心跳 / 全量对齐（默认 30s 一次，或收到 refresh 后）
{"t":"tick","session":S,"seq":40,"devices":[Device, ...]}

// 告警（collector 判定，UI 只展示）
{"t":"alert","session":S,"seq":41,"kind":"low|full|offline","key":"...","level":12,"text":"..."}

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
{"t":"get","what":"snapshot"}                 // 立即回 tick
```

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

### 4.1 Rust → 前端事件

| 事件名 | payload | 说明 |
|---|---|---|
| `collector-message` | string（原始 JSON 行） | 前端 `JSON.parse` 后按 §1 处理 |
| `collector-state` | `{state:"starting"\|"ready"\|"exited"\|"error", pid?, code?, message?, protocol?, session?}` | sidecar 状态 |
| `app-state` | `{pinned:bool, platform:{windows11:bool, build:number}, build:string, version:string}` | 启动时 + pin 变化 |
| `tray-action` | `{action:"refresh"\|"settings"\|"quit"\|"toggle-pin"}` | 托盘菜单动作 |
| `window-visibility` | `{hidden:boolean}` | Rust 在 `put_IsVisible` 前后发出；前端据此暂停/恢复动画与 rAF |

### 4.2 前端 → Rust 命令（invoke）

| 命令 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `ui_ready` | 无 | `{show:bool}` | 前端首帧完成；默认隐藏启动返回 `show:false`（只驻留托盘），`PB_SHOW_ON_START=1` 时返回 true 并显示面板（避免白闪） |
| `collector_send` | `{line:string}` | `boolean` | 写一行给 collector（§2）；含换行或 >1MiB 的行拒绝写入并返回 false |
| `collector_restart` | 无 | `boolean` | 重启 sidecar（版本握手失败时用） |
| `set_pinned` | `{pinned:bool}` | `boolean` | 持久化到 `app.json`（注意：`config.json` 归 collector，二者不可混写） |
| `set_tray_state` | `{level:number\|null, charging:boolean, online:boolean, kind:string}` | 无 | 托盘图标状态色 |
| `window_action` | `{action:"hide"\|"minimize"\|"quit"}` | 无 | 关闭按钮 = hide |
| `collector_status` | 无 | `collector-state` 当前值 | 前端启动时主动拉一次 |

### 4.3 隐藏 / 显示顺序（不可颠倒）

隐藏：`put_IsVisible(FALSE)` → 发事件 `window-visibility {hidden:true}`（前端 pause WAAPI / 停 rAF / 不写 DOM）→ `window.hide()`。
显示：`window.show()` + `put_IsVisible(TRUE)` → 发 `window-visibility {hidden:false}` → 前端用最后快照渲染一帧 → 恢复动画。

---

## 5. 版本与错误

- `protocol = 1`；加字段不升版本，删/改字段升版本。
- Rust 校验 `hello.protocol === 1`；不符时向前端发 `collector-state.error`（文案「采集器版本过期」），并提供重启按钮。
- sidecar 退出时 Rust 发 `collector-state.exited`；5s 内未收到 hello 以 `error` 上报。
- `build` 用于诊断展示，不阻断运行。

---

## 6. 路径

```
%LOCALAPPDATA%\PeripheralBattery\
  app.json                # 外壳/UI 配置（pinned 等），Rust 写
  config.json             # 采集层配置（轮询、启用 provider），collector 写
  aliases.json            # 稳定 key 映射 + 迁移历史
  history.json            # 剩余时间模型
  logs\app.log            # 5×2MB 轮转（Rust 写）
  logs\collector.log      # 5×2MB 轮转（Python stderr 线程写）
  diagnostics\            # --diag 输出
```
