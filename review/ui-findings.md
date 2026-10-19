# task-3 审查报告：WebView2 前端 UI 与可访问性

- 审查员：ui-reviewer
- 时间：2026-10-19 16:54–17:12（Asia/Shanghai）
- 工作目录：`D:\desktop\电量显示插件`（commit `a24f2fc`）
- 机器：Windows 11 build 26300 / 2560×1440（工作区 2560×1380）/ **125% 缩放** / WebView2 Runtime **155.0.4283.62** / Playwright Chromium 156.0.8078.4
- 范围：`ui/app.js`（1506 行）、`ui/bridge.js`（468 行）、`ui/index.html`（140 行）、`ui/styles.css`（1210 行）
- 约束：**未修改任何源码/配置/构建产物**。仅新增本报告与 `review/ui-evidence/` 证据（截图/JSON；临时脚本在 %TEMP%）。
- 结论：**UI 专项未发现 P0**；P1×2、P2×14、P3×十余条。核心渲染/XSS/CSP/隐藏暂停/keyed diff 等既有修复经复测成立（见 §5）。

---

## 1. 实际运行过的命令与结果

| # | 命令/操作 | 结果 |
|---|---|---|
| 1 | `node tools/ui_smoke.js` | **15 passed, 0 failed**（G1–G7、A1–A3、F1–F5 全过；会重写 design/verify/ui-*.png，属既有测试行为） |
| 2 | `node tools/ui_shots.js` | 7 张截图生成，**no console errors** |
| 3 | 自建 Playwright 评审 harness（临时脚本 `%TEMP%\pb-ui-review\review.js`，本地 HTTP 服务器套与 tauri.conf 相同的 CSP 头） | 覆盖 DSF 1/1.25/1.5/2、forced-colors、reduced-motion、reduced-transparency(CDP)、prefers-contrast、键盘/AX 树、XSS、长文本、空/错/版本错、session 重放、400 次更新性能；**0 pageerror / 0 console error**。结果：`review/ui-evidence/results.json` |
| 4 | 自建 harness #2/#3（`review2.js`/`review3.js` 于 %TEMP%） | 修正首轮无效用例（hello 同 session 被忽略、tab 前误点击）、补有效 XSS 注入、长名、版本错、toast 点击、焦点丢失、运行时媒体切换、forced-colors alpha。结果：`results2.json`/`results3.json` |
| 5 | 真实 Tauri release 启动：`PB_SINGLE_INSTANCE_NAME=Global\PBReviewUI-cdp1 PB_SHOW_ON_START=1 PB_QUIT_AFTER_MS=300000 PB_COLLECTOR="...\.venv\Scripts\python.exe ...\scripts\mock-collector.py"`（`src-tauri\target\release\peripheral-battery.exe`） | 窗口 975×550 physical = 780×440 DIP @1.25；app.log `scale factor cached = 1.250`、`window created ... size=975x550`；我们的实例 17:08:21 由 PB_QUIT_AFTER_MS 正常退出（`app exit`），无残留（其余 peripheral-battery 进程属并行审查员，未动） |
| 6 | 真实窗口 Win32 原生输入测试（PowerShell 7 + user32）：命中测试、拖拽、双击、右键菜单、F5 | 见 P1-1/P1-2/P2-13；截图 `real-app-125.png`、`real-context-menu.png`、`real-after-f5.png`（含裁切图） |
| 7 | 尝试 CDP 连接真实 WebView2：`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333` | **失败（预期）**：wry 0.57 总是设置 `additional_browser_args`（默认 `--disable-features=...`，见 wry `src/webview2/mod.rs:300-333`），盖掉该环境变量 → 无 CDP。故真实 WebView2 内部 DOM 用「静态源码 + Playwright 同特性 + 原生输入」三路取证 |
| 8 | 系统环境读取 | 显示缩放 125%（`LogPixels=128`）；`EnableTransparency=1`；`TextScaleFactor` 未设置(100%)；无高对比度主题；WebView2 155 |

---

## 2. 兼容性结论（WebView2 特性矩阵）

用 `grep` 核实：**未使用** `color-mix`、`:has`、`@container`、`@property`、`popover`、`structuredClone`、`Array.at`、`Intl`（任务清单里的风险项在 UI 中不存在，无需处理）。实际用到的“较新”能力：

| 特性 | 使用位置 | Chromium 起始版本 | 旧 WebView2（109/120）风险 |
|---|---|---|---|
| `inert` | app.js:1366/1372（抽屉） | 102 | <102 失效；但关闭态 `visibility:hidden` 仍不可聚焦，影响小 |
| `:focus-visible` | styles.css:369/759/1180 | 86 | 无 |
| `accent-color` | styles.css:1084/1109 | 93 | 无（仅原生控件着色） |
| `mask-composite: exclude` | styles.css:164-167 | 120（有 `-webkit-mask-composite:xor` 回退） | 109 走 webkit 回退，正常 |
| `scrollbar-width/color` | styles.css:699-700、1060-1061 | 121 | 109/120 忽略；有 `::-webkit-scrollbar` 回退（703-707），正常 |
| `prefers-reduced-transparency` | styles.css:220-224 | **118** | **109 不支持 → “透明度效果关闭”时不进不透明降级**（见 P2-6） |
| `backdrop-filter` | 仅 `html[data-effects=css]`（192-210） | 76 | 生产不启用；正常 |
| `Element.animate / getAnimations` | app.js:624/1177 | 36/84 | 有 `el.heroCard && el.heroCard.animate` 能力判断（624）；getAnimations 外层 try 兜底（1176-1181） |
| JS：Map/Set/Promise/async/URLSearchParams/padStart/closest | 全局 | ≤61 | 无 |

- 无 WebView2 Runtime 时 Tauri 在创建窗口前失败，UI 无机会渲染；安装器默认 `downloadBootstrapper` 需联网（P3，属打包域，与 shell 报告重叠）。
- 结论：在 Tauri 2 实际支持的 Evergreen 上，**除 `prefers-reduced-transparency`（118+）外没有会坏功能的新特性依赖**；无需为兼容改 JS。

---

## 3. 发现（P1）

### P1-1【拖拽】只有 `.side-head` 自身的约 6px 空带能拖动窗口，标题/副标题上拖拽完全无效
- 位置：`ui/index.html:67`（`<header class="side-head" data-tauri-drag-region>`）；Tauri 2.12.4 `tauri-2.12.4/src/window/scripts/drag.js:44-67`
- 原因：Tauri 2 的裸属性语义是 **only direct clicks on this element**；`deep` 才允许子树触发。`.side-head` 的绝大部分面积被 `.title-row`（h1/在线药丸）和 `.subtitle` 覆盖，二者命中目标是子元素 → `isDragRegion()` 返回 false。真正可拖的只有标题行与副标题之间约 6px 的缝隙（以及 2px 内边距）。
- 真机证据（125%，975×550 physical 窗口）：在 h1 文本中心按下拖动 -100px → 窗口 rect 不变 `(1567,812)-(2542,1362)`；在 header 缝隙（CSS≈500,86.5）同样拖动 → 窗口移动到 `(1467,812)-(2442,1362)`：
  ```
drag-h1   before=(1567,812) after=(1567,812)  moved=False
drag-gap  before=(1567,812) after=(1467,812)  moved=True
  ```
- 触发场景：用户想拖动这块浮动面板（Win11 托盘上方无边框窗口），按最自然的“拖标题”操作 → 毫无反应；只有极精确地拖中 6px 缝隙才移动。
- 修复方向：`data-tauri-drag-region="deep"`（Tauri 2.12 支持，drag.js:63-64）加在 `.side-head` 或整个 `.panel` 上；Tauri 会自动排除 button/input 等 clickable 元素。加完补一条原生拖拽回归（h1 上拖动应移动窗口）。

### P1-2【Windows 集成】生产面板弹出 WebView2 浏览器右键菜单；F5/Ctrl+R/Ctrl+P 等浏览器快捷键全部生效（F5 实测刷新并重置窗口位置/状态）
- 位置：`ui/index.html` 无 `contextmenu` 处理、`ui/app.js` 全局 keydown 只处理 Esc（1468-1470）；`src-tauri/tauri.conf.json` 未配置相关禁用（window 段 12-30）。依赖默认值：wry 0.57 `src/lib.rs:1768-1769` → `browser_accelerator_keys: true`、`default_context_menus: true`，`src/webview2/mod.rs:638-651` 原样应用；Tauri 未覆写（registry 内 grep 只有 `with_hotkeys_zoom`）。
- 真机证据：
  1) 右键面板 → 原生菜单出现：**返回(Alt+←)、刷新(Ctrl+R)、另存为、打印(Ctrl+P)、更多工具**（截图 `real-context-menu.png` / `crop-real-context-menu.png`）。
  2) 对窗口发 F5 → app.log 出现**第二次 `command ui_ready` 与 `window show reason=ui-ready`**（17:05:40.334），即整页重载；重载后 `ui_ready` 又走 `show_panel`，把用户拖到别处的窗口**重置回托盘锚点**（拖动后 `(1467,812)` → F5 后 `(1567,812)`），并丢失用户固定的主卡/抽屉状态；截图 `real-after-f5.png`：
     ```
17:05:40.334 [INFO] command ui_ready
17:05:40.335 [INFO] window show reason=ui-ready ...
17:05:40.335 [INFO] window shown origin=(1567,812) size=975x550
     ```
- 触发场景：在面板上按习惯右键、或误触 F5/Ctrl+R（刷新）、Ctrl+P（打印预览）、Ctrl+F（查找栏）——打印预览会覆盖一块无边框透明小窗，观感像卡死。
- 修复方向：前端 `document.addEventListener('contextmenu', e => e.preventDefault())`；全局 keydown 拦截 F5/Ctrl+R/Ctrl+P/Ctrl+F/Ctrl+S（Chromium 对浏览器加速键会尊重 preventDefault）；更彻底的做法是在 wry 层 `browser_accelerator_keys(false)/default_context_menus(false)`（Tauri 未暴露配置，可能需要补 Rust 自定义或升级后使用新配置项）。补真机回归：右键无菜单、F5 不重载。

---

## 4. 发现（P2）

### P2-1【无障碍】高对比度（forced-colors）完全没有适配；`prefers-contrast: more` 完全不生效
- 位置：`styles.css` 无任何 `@media (forced-colors) / (prefers-contrast)` 规则（全文 grep 为空；仅 220/1194 两处媒体查询）。
- 证据（Playwright forcedColors:'active'）：`forced-colors: active` 匹配，但面板/row/按钮背景渐变不降级为系统 Canvas；`panelBg=rgba(255,255,255,0)`、`panelBoxShadow=none`，部分区域 alpha 仍 163（`forced-colors-alpha.png` 实测面板内含半透明像素）；文字被系统强制为黑/白（subtitle=rgb(0,0,0)、alert-tag 黑字、toast 白底），截图 `forced-colors.png`/`crop-forced-colors-panel.png` 中面板整体变灰、齿轮图标几乎不可见；高对比度下文本对比度取决于桌面/合成结果，无法保证；圆环状态色虽保留（uniqueStrokes=4），但列表行低电仍只能靠颜色区分。
  对 `prefers-contrast: more`：模拟前后 subtitle/hint/all-btn/row 的 color/background/border **computed 值完全一致**（`results.json.prefersContrast`）→ 系统“更高对比度”偏好对 UI 零作用。
- 触发场景：Win11 设置→辅助功能→对比度主题（任意 HC 主题）+ 使用本面板。
- 修复方向：加 `@media (forced-colors: active)`：面板/抽屉/行/按钮设 `background-color: Canvas`、`color: CanvasText`、可见 `border: 1px solid CanvasText`，禁用装饰渐变/阴影；低电状态加文本标签（见 P2-3）。加 `@media (prefers-contrast: more)` 提高文字不透明度/描边。做真机 HC 黑/白主题截图验证（本次只做 Chromium 模拟，未改系统主题）。

### P2-2【DPI】125% 下窗口/面板边缘落在半像素（1px 镜面边、圆角抗锯齿发虚）；窗口阴影被 30px 安全边硬裁
- 位置：`styles.css:60-71`（body padding 30）、`:84-88`（.stage 720×380）、`:141-147`（`0 30px 70px` 阴影）、`index.html:10`
- 证据：DSF 1.25 时面板 `x=30→37.5 device px、y=37.5`（`results.json.dpi.dsf1.25.panelDeviceFrac = {x:-0.5,y:-0.5}`），即所有外边缘都带 .5 → 1px 边框/`mask` 镜面边/圆角被抗锯齿。真机 125%（975×550 physical）截图 `crop-real-panel-125.png` 可复现边框比 100% 略软。125% 是 Win11 最常见缩放。
  `box-shadow: 0 30px 70px` 的扩散远超 30px 安全边，而透明窗口无法绘制到窗口矩形外 → 阴影在窗口边界被硬切（真实桌面下可见光晕突然截断）。
- 触发场景：Win11 默认 125% 笔记本屏；深色壁纸下看面板边缘/阴影。
- 修复方向：按 `window.devicePixelRatio` 让 Rust 把窗口物理位置/尺寸对齐到整数 DIP（或前端在 1.25 下把 1px 伪边框改为内描边/box-shadow inset 避免半像素）；阴影改小（≤30px）或把窗口安全边加到 ≥50 DIP，或接受裁切并在设计文档注明。

### P2-3【无障碍】低电/离线/充电状态在列表行上只靠颜色表达（WCAG 1.4.1）
- 位置：`app.js:270-287`（ringStateClass）、`styles.css:477-482`（state 色）；行的可见文案是 `statusText`（`app.js:260-268`，只写“约 X 小时/充电中/离线”，不写“电量低”）；“建议充电”标签只在主卡（`index.html:60`）。
- 证据：mock 中 12% 行的 aria-label 是“Arctis Nova 7，12%”，可见文本“2.4 GHz · 约1小时48分”；红色只体现在 .ring 的 stroke。色盲/灰度/forced-colors 下与普通行无可区分文本（forced-colors 状态色虽保留，但高对比度下色觉辅助用户仍缺文本）。
- 触发场景：色觉障碍用户、强制颜色、灰度远程桌面。
- 修复方向：低电行在 `.row-level`/sub 加“低电”文本或图标（带 `aria-hidden` 的图形 + 可读文本）；或给行加 `aria-describedby` 状态说明。

### P2-4【无障碍】`#subtitle` live region 每次数据更新都重写同一文本 → 读屏重复朗读
- 位置：`index.html:72`（`aria-live="polite"`）、`app.js:746`（`el.subtitle.textContent = subtitle;` 无变化判断）
- 证据：4 次 device 更新（同一分钟文本完全相同 `"5 台设备 · HH:MM 更新"`）→ MutationObserver 记录 **4 次 childList mutation**（`results.json.a11y.liveNoise.mutations=4`），distinctTexts 只有 1 条 → 读屏会重复播报同一句。对比其它写 DOM 都有 cache 判断（patchRow 471-521），只有 subtitle 没有。
- 修复方向：只在 textContent 变化时写入；或把时间/设备数拆成非 live 文本，仅对状态变化（在线/断开）用 live region。

### P2-5【无障碍】设置抽屉不是 dialog：无 role/aria-modal，打开时背景未 inert；toast 仅可鼠标关闭；主卡 aria-label 在无设备后残留
- 抽屉：`index.html:99`（`<aside aria-label="设置">`，role=null、aria-modal=null）、`app.js:1351-1378`（只切 aria-hidden/inert）、`results.json.a11y.drawerOpen.sideInert=false`：读屏仍可遍历背景内容；AX 树（CDP `Accessibility.getFullAXTree`）里没有 dialog 节点。
  焦点管理本身是好的：打开后焦点到阈值滑块，Tab 循环 `threshold→reduced→pinned→close→threshold`，Esc 关闭并把焦点还给 `btn-settings`（实测），这部分通过。
- toast：`app.js:1131-1132` 只绑 click；`tabIndex=-1、role=null`（`results.json.a11y.toastInfo`），键盘用户无法提前关闭（只能等 4.2s）。
- 主卡 aria：`app.js:616` 只有 `if (hero) setAttribute('aria-label',...)`，无 else；实测设备全部 gone 后 `#hero` 的 aria-label 仍是“主设备：Viper V3 Pro，68%”，而可见文本已是“等待设备”（`results.json.a11y.heroAfterEmpty`）→ 读屏播报陈旧信息。
- 触发场景：Narrator/NVDA 用户打开设置、主卡设备全部离线/消失。
- 修复方向：抽屉改 `role="dialog" aria-modal="true"` 并对背景（除抽屉外）`inert`；toast 加 `tabIndex=0 + keydown(Esc/Enter)` 或提供全局“关闭通知”键；`renderHero(null)` 时把 `#hero` 的 aria-label 重置为“主设备：等待设备”或移除。

### P2-6【可访问性/兼容】`prefers-reduced-transparency` 降级只在 WebView2 ≥118 生效；旧版无回退
- 位置：`styles.css:220-224`（媒体查询 + `html:not([data-effects=css])` 兜底）；`index.html:57/60`（body/html 透明）
- 证据：Chromium 156 下 CDP 模拟该媒体特性 → 匹配且面板 `background-color` 从 `rgba(0,0,0,0)` 变 `rgb(11,24,52)`（`results.json.reducedTransparency`，截图 `reduced-transparency.png`），说明降级本身正确；但该特性 Chromium **118** 才支持（Chrome 118 beta 公告）。若机器是固定旧 WebView2（如 109），或系统“透明效果”关闭而 WebView2 <118，面板仍是半透明渐变叠在无 acrylic 的透明窗口上 → 直接透出壁纸，深/亮壁纸都会显著降低文字可读性。本次真机 WebView2 155 + Transparency=1，未能复现旧版路径。
- 触发场景：Win10 升级机/锁定 WebView2 版本的企业环境 + 系统关闭“透明效果”。
- 修复方向：不要只依赖 CSS 媒体特性；由 Rust 读取系统透明设置（或检测 `set_effects` 是否成功）通过 `app-state` 下发 `transparencyEnabled`，前端据此给面板不透明底色（把 220 行规则改成 JS 可控 class）；CSS 媒体查询保留作增强。

### P2-7【协议健壮性】旧 session 的迟到事件会把 UI 整体切回旧 session（面板短暂清空/显示旧设备）
- 位置：`app.js:1011-1016`（session 不一致 → `resetSession({session:ev.session, devices:[]})`）
- 证据（`?mock&freeze`）：hello(B) 后注入一条 session=A 的 device（seq=99）→ `__pbDebug.session` 从 B 变回 A、devices 只剩 `a2`、hero 变 `a2`；直到下一条 B 的 tick 才恢复：`results.json.sessionReplay = {afterB:B/[b1], afterStale:A/[a2], afterRecover:B/[b1]}`。
- 触发场景：采集器重启/切换时，Rust stdout 线程可能在新 hello 之后才转发旧进程缓冲行（shell 审计 P3-5 已指出该窗口），用户会看到面板闪一下旧设备/空列表。
- 修复方向：对 `ev.session` 不等于当前 session 且**不是 hello** 的消息：若当前 session 已由 hello 建立 → 直接丢弃（不回退）并计数；只有 hello 能切换 session。或维护 knownSessions/单调 session 代际。

### P2-8【协议健壮性】`helloReceived` 只置 true 从不复位 → 采集器重启后“ready 但 hello 丢失”时不再主动要快照
- 位置：`app.js:86`（state.helloReceived）、`:1006`（置 true）、`:1062-1068`（`cs.state==='ready' && !state.helloReceived` 才发 `get snapshot`）、`:965-987`（resetSession 不复位该字段）
- 影响：首个 session 后该兜底失效；collector 重启时若 hello 在 WebView 监听就绪前被转发掉/丢失，UI 只能等下一次 tick（正常 1s 级，但若 tick 也延迟则空白更久）。本次未构造出真实 hello 丢失，属代码级风险。
- 修复方向：resetSession 或 `collector-state starting/exited` 时把 helloReceived 置 false；或改成按 `(session, helloApplied)` 判断。

### P2-9【安全加固】生产 Tauri 页面无条件下发 `window.__pbMock` 与完整 `__TAURI__`；CSP 可再收紧
- 位置：`bridge.js:466-467`（`window.__pbMock = makeMockApi()` 不区分场景）、`tauri.conf.json:10`（`withGlobalTauri: true`）、`tauri.conf.json:33`（CSP 无 `object-src/base-uri/frame-ancestors`）
- 证据：当前无 XSS sink（见 §5），风险低；但页面一旦被任何注入（扩展、devtools、未来回归）即可伪造任意设备/托盘状态并直接调用 Tauri API。CSP `script-src 'self'` 已阻止内联注入，属纵深防御问题。
- 修复方向：release 下不创建 `__pbMock`（或仅 `?mock` 时创建）；评估关闭 `withGlobalTauri` 改用显式 import；CSP 补 `object-src 'none'; base-uri 'none'; frame-ancestors 'none'`。

### P2-10【健壮性】toast 文本不换行，超长设备名/告警文本溢出面板与窗口
- 位置：`styles.css:958-972`（.toast/.toast-text 无 `overflow-wrap/word-break`、无 `overflow:hidden`）、`app.js:1122-1132`（toast 文本来自采集器 `alert.text`/设备名）
- 证据：400 个连续 `W` 的 alert → toast `scrollWidth=4400 vs clientWidth=298`，文字横穿整个面板并盖住“全部设备”按钮（截图 `long-text.png`，`results.json.longText.toast.overflow=true`）。正常中文/带空格英文会正常换行；但设备名/`alert.text` 是外部采集数据，长 token 直接溢出。
  对照：行名/主卡名/banner 都有 ellipsis + overflow:hidden（实测 300 字名单行不溢出，仅 ellipsis），所以这是 toast 单点缺失。
- 修复方向：`.toast-text { overflow-wrap:anywhere; word-break:break-word; }` + `.toast { overflow:hidden; }`；normalizeDevice 对 `name`/`approx` 做长度上限（如 120 字符）并保留完整值给 aria-label。

### P2-11【交互】“全部设备”按钮行为与文案不符；空列表时点击会打开“设置”
- 位置：`index.html:85-88`（文案“全部设备”+ 右箭头）、`app.js:1425-1429`（有设备时只把焦点移到第一行；无设备时 `openDrawer()`）
- 证据：有设备时点击 → `document.activeElement` 变成 `.row`，但 `:focus-visible=false`（鼠标点击的程序化聚焦无视觉反馈），用户观感“按钮没反应”（`results.json.a11y.afterAll`）；`?mock&empty` 下点击 → 抽屉打开（`results2.json.btnAllEmpty.drawerOpen=true`），与“全部设备”语义完全无关。
- 触发场景：用户想展开完整设备列表（尤其主卡行被移出列表后只剩部分设备）。
- 修复方向：改名（如“定位列表”）、或实现真正的“全部设备”展开层；无设备时禁用按钮（`disabled`）而不是打开设置。

### P2-12【无障碍】行/主卡的 focus 外框被 `#list overflow` 裁掉左右与上边；行被删除时焦点直接掉到 body
- 位置：`styles.css:690-701`（`.list { overflow-y:auto; overflow-x:hidden; }`）、`:730/:768-770`（row `outline-offset:2px` + focus-visible 2px）；`app.js:541-549`（删除行无焦点迁移；相比之下 hero 切换有迁移 452-456）
- 证据：行 rect 与 `#list` rect 完全同 x（267..729），outline 向外 4px 必然被裁剪；focus-row 截图只看到行内 `::after` glow 作为替代提示（`results.json.a11y.focusRing`）。行被 tick 移除时 `activeElement` 从 row 变成 `BODY`（`results2.json.focusRemoval`），键盘用户丢失位置。
- 修复方向：`.list { padding:2px 4px; }` 给 outline 留空间（或行 focus 用内描边 `box-shadow: inset 0 0 0 2px` 配合 outline none）；删除聚焦行时把焦点移到相邻行，空了则移到 `#btn-all`。

### P2-13【Windows 交互】透明 30px 安全边吞掉桌面点击（透明窗口区域仍参与命中测试）
- 位置：`tauri.conf.json:20`（transparent:true）、`styles.css:60-71`（body 透明 + 30px padding）、`index.html:11`
- 证据（真机 125%）：`WindowFromPoint` 在窗口四角/四边（透明边带内）返回的 root 都是本应用 HWND（`ours=True`），即 OS 把点击路由给本窗口；点击桌面图标/壁纸时，只要落在这 780×440 窗口的 30 DIP 透明边带内就被吞掉：
  ```
marginTL point=(1575,820)  root=8783730 rootPid=11352 ours=True
marginBL point=(1575,1354) root=8783730 rootPid=11352 ours=True
marginTR point=(2534,820)  root=8783730 rootPid=11352 ours=True
  ```
- 触发场景：面板浮在桌面/浏览器上，用户在面板边缘附近点桌面图标或滚动，前 30px 无反应；带圆角的四角同样被吞。
- 修复方向：需要按区域命中测试（`WM_NCHITTEST` 在 margin/圆角区域返回 `HTTRANSPARENT`），或缩小窗口到 720×380 并把阴影/间距改由 DWM 提供；纯前端无法解决（`pointer-events:none` 只影响页面内命中，不影响 Win32 窗口命中）。

### P2-14【性能/交互】toast 覆盖列表底部行并吞掉点击；运行中切换系统动效不同步
- toast 位置 `styles.css:945-956`（bottom:14px, pointer-events:auto）与 `app.js:1132`：7 台设备滚到底时，可见行 `t5` 与 toast 重叠，`elementFromPoint` 命中 `toast-text`，点击只关闭 toast、不会选中该行（`results3.json.toastClick`）。4.2s 内底部行可点击性受损。修复：toast 容器保持 `pointer-events:none`、只让关闭按钮可点，或把 toast 放到面板底部留白区。
- 运行中改系统“减少动效”：`page.emulateMedia({reducedMotion:'reduce'})` 后 `matchMedia` 已为 true，但 `__pbDebug.reducedMotion` 仍 false、`data-reduced-motion` 未设置（`results2.json.runtimeMediaChange`）。CSS 媒体查询仍生效、`prefersReducedMotion()` 每次实时读取，所以动效确实被抑制；但设置抽屉的“降低动效”勾选与 data 属性不同步。修复：`matchMedia(...).addEventListener('change', applyReducedMotion)`（app.js:1395-1406 目前只 boot/手动调用）。

---

## 5. 既有修复复验（确认成立）

| 旧结论 | 复验方式 | 结论 |
|---|---|---|
| F1 主卡锁定首个设备（P1） | 代码 `pickHero()` 每次取 `lowest(online)`（app.js:793-809）；mock 逐条 device（smoke F4、本 harness） | **已修，成立** |
| keyed diff 行复用（P3-15） | smoke G3 + `results.json.perf`：400 次 level 更新 + 50 台设备后 `rowCreates` 只随新增设备增长、`rowRemoves=0`；节点增长与新增行数匹配 | 成立（hero 行用 hidden 切换，未重建） |
| 隐藏四步：暂停 WAAPI/停 rAF/清 timer/不写 DOM（G6/F1-F2） | smoke G6/F1/F2 + 15 次 hide/show churn：`pausedAnimations=0`、timers 稳定、nodes 不增长 | 成立；getAnimations 有 try/catch 兜底 |
| XSS：设备名/文案全 textContent，innerHTML 只拼常量 SVG | 有效注入（新 session）device name/key/kind/transport/approx/alert.text = `<img onerror>/<script>/<svg onload>`：`window.__xss` 未定义、`img=0`、scripts 仍为 2 个本地脚本；行名/aria-label/toast 全为纯文本（`results2.json.xssValid`） | **无 XSS 可利用点**（加固见 P2-9） |
| CSP（script/style self，无 inline） | 本地 HTTP 服务器强制同 CSP，所有 mock/demo 页面 0 console CSP 违规、0 inline style/script（smoke G7） | 成立 |
| 圆环 level=null 空心/不判红、approx 文案 | smoke G2 + `?mock` 5 台含 null/approx/charging：状态类正确，主卡 `%` 隐藏 | 成立 |
| session/seq 乱序丢弃、replaces 原地 morph、session 重置不重播 intro | smoke G4 + 本 harness session 重放（除 P2-7 的跨 session 回退） | 同 session 内成立 |
| 设置抽屉键盘循环/Esc/焦点回转/localStorage | smoke A2 + Tab 9 次循环 + Shift+Tab + Esc：aria-hidden/inert/aria-expanded 同步、焦点回 btn-settings | 成立（模态语义见 P2-5） |
| prefers-reduced-motion：时长 0.001ms、rAF 不启动、柔光/涟漪关闭 | 模拟 reduce：`getAnimations()=[]`、`pseudoDur=1e-06s`、pointermove 后 rafTicks 不增、ripple 0；点击行不产生涟漪 | 成立 |
| 生产 0 个 backdrop-filter（毛玻璃交给 acrylic） | smoke G5 + 源码 192-210 全在 `data-effects="css"` 下 | 成立 |
| 125% DPI 下窗口尺寸/内容完整 | 真机 125%：窗口 975×550、面板完整无裁切（`crop-real-panel-125.png`）、无 console 错误 | 成立（半像素问题见 P2-2） |
| 键盘设主卡后焦点落到主卡（P3-18） | 行上 Enter → `activeElement=#hero`、`:focus-visible=true`、outline 2px（styles 369-373） | 成立 |
| 性能：DOM 无泄漏、heap 稳定 | 400 次 level + 50 台 + 30 toast，GC 后 heap `1.39MB→1.53MB (+139KB)`；15 次 hide/show 后 nodes/animations 稳定；动画数回到 2（脉冲+充电呼吸） | 未发现泄漏；未做 2h 长稳 |

---

## 6. 发现（P3，建议但不阻塞）

1. **触摸/笔目标偏小**：`.ctrl 30×30`（styles.css:283-296）、`.drawer-close 28×28`（1050）；低于 Win11 触控建议 44px（WCAG 2.5.8 24px 可过）。
2. **`role=toolbar` 无方向键导航**（index.html:19；实测 ArrowRight 不移动焦点），ARIA toolbar 模式建议左右键切换。
3. **Esc 在抽屉关闭时不隐藏面板**（app.js:1468-1470 只处理 drawerOpen）；Win11 浮动面板通常 Esc=收起，可加。
4. **`body { user-select:none }`（styles.css:69）导致无法选中/复制设备名、session、版本**；诊断场景建议允许选择 about 区文本或提供“复制诊断信息”。
5. **`::selection` 样式不可达**（styles.css:1182）因上面的 user-select:none，属无效规则。
6. **无 `color-scheme` 声明**：暗色 UI 下原生 range/checkbox/滚动条仍按浅色主题绘制（accent-color 只改配色），可加 `<meta name="color-scheme" content="dark">`。
7. **via/updated_unix/stat 采集字段未展示**（app.js:896-914 归一化、1046 只存 debug）：设置页可展示数据新鲜度/代理，便于排查 `updated_unix` 长时间不更新但 collector 未退出的场景（前端无数据新鲜度看门狗）。
8. **采集器数据不设行数上限**：`state.devices/rowMap` 随外部 key 数量无限增长（app.js:524-565）；正常 5–15 台没问题，异常/钥匙轮换时可加 200 行上限与合并策略。
9. **字号偏小**：`10.5px`（drawer-note）、`11px` 标签在 100% 缩放下偏小，低视力用户无文本缩放（见 P2 文本缩放）；可统一 ≥12px 或随 DPI 略放大。
10. **主卡“自动最低”无法恢复**：点击行后 `heroPinnedByUser=true` 且无 UI 回到自动（app.js:1231-1240、793-809）；可再点同一行/长按或加设置项。
11. **`title` 属性 tooltip 与自绘控件混用**：Win11 深色面板 + 原生 tooltip 观感不一致；非必须。
12. **设置抽屉打开时背景动画继续跑**（脉冲/充电呼吸）且 scrim 只挡鼠标，性能/专注度可优化。

> 说明：P3 中“主卡自动恢复”与 shell 审计 P3-17 重复；“via/字段未用”属产品取舍，列出供 Lead 决策。

---

## 7. TOP 待修复清单（按优先级）

| 排序 | 级别 | 事项 | 关键位置 | 修复成本 |
|---|---|---|---|---|
| 1 | P1 | 拖拽区只写在 .side-head 自身：拖标题完全不能移动窗口 | `index.html:67` → 改 `data-tauri-drag-region="deep"` | 1 行 + 原生回归 |
| 2 | P1 | 生产弹 WebView2 右键菜单；F5/Ctrl+R/Ctrl+P 浏览器快捷键生效（F5 实测重载并重置窗口位置/状态） | index.html 加 contextmenu 拦截；app.js 全局 keydown 过滤；根治需 wry 设置 | 低（前端）/中（Rust） |
| 3 | P2 | forced-colors/prefers-contrast 零适配；HC 下 UI 变灰、部分图标不可见，状态色无文本替代 | styles.css 末尾加媒体查询 + .row 状态文本 | 中 |
| 4 | P2 | 无障碍打包：subtitle live region 重复播报、抽屉无 dialog/背景未 inert、toast 键盘不可关、hero aria 残留、行 focus 外框被裁、删行焦点掉 body | app.js:746/616/1131/1351/541、styles.css:690/730/768 | 中 |
| 5 | P2 | 125% 半像素边框/圆角 + 阴影被 30px 边裁切 | styles.css:60-71/84-88/141-147 | 低-中 |
| 6 | P2 | 旧 WebView2（<118）无 reduced-transparency 兜底；建议 Rust 侧下发透明能力标志 | styles.css:220-224 + panel.rs apply_platform | 中 |
| 7 | P2 | 旧 session 迟到事件把 UI 切回旧 session；helloReceived 不复位导致重启用快照兜底失效 | app.js:1011-1016/1006/965-987/1062-1068 | 低 |
| 8 | P2 | toast 超长文本溢出面板；toast 遮挡并吞掉底部行点击 | styles.css:958-972、app.js:1122 | 低 |
| 9 | P2 | “全部设备”语义不符：有设备时无可见反馈、空列表时打开设置 | index.html:85-88、app.js:1425-1429 | 低 |
| 10 | P2 | 透明 30px 安全边吞桌面点击（780×440 窗口内全部区域参与命中测试） | 需 WM_NCHITTEST/窗口区域方案（Rust） | 中-高 |
| 11 | P2 | 文本缩放不生效（全部 px、根字号 200% 实测字号不变）、10.5–11px 小字；建议 UI 缩放设置或 rem 化 | styles.css 全量字号 | 中 |
| 12 | P2 | 安全加固：release 不暴露 __pbMock、评估关 withGlobalTauri、CSP 补 object-src/base-uri/frame-ancestors | bridge.js:466、tauri.conf.json:10/33 | 低 |

---

## 8. 证据索引

- 报告：`review/ui-findings.md`
- 浏览器矩阵/无障碍/安全/性能结果：`review/ui-evidence/results.json`、`results2.json`、`results3.json`
- 截图（全部在 `review/ui-evidence/`）：
  - DPI：`dpi--1.png`、`dpi--1_25.png`、`dpi--1_5.png`、`dpi--2.png`
  - 真实 WebView2 125%：`real-app-125.png`、`crop-real-panel-125.png`
  - 右键菜单：`real-context-menu.png`、`real-context-menu-after-esc.png`、`crop-real-context-menu.png`
  - F5 后：`real-after-f5.png`
  - 高对比度：`forced-colors.png`、`crop-forced-colors-panel.png`、`forced-colors-alpha.png`、`forced-colors-dark-bg.png`
  - 减少动效/透明度：`reduced-motion.png`、`reduced-transparency.png`
  - 焦点/键盘：`focus-row.png`、`drawer-kbd.png`、`hero-keyboard-focus.png`、`crop-focus-row.png`
  - XSS/长文本/空态/错误态：`xss-valid.png`、`xss.png`、`long-text.png`、`long-names-valid.png`、`empty-state.png`、`empty-mock.png`、`error-state.png`、`version-error-valid.png`、`stale-session.png`、`perf-after-updates.png`
- 真机 app.log 关键行：`%LOCALAPPDATA%\PeripheralBattery\logs\app.log`（17:03:21 scale 1.250；17:05:40 F5 触发第二次 command ui_ready / window show reason=ui-ready；17:08:21 app exit）
- 依赖源码依据：`%USERPROFILE%\.cargo\registry\src\index.crates.io-*\tauri-2.12.4\src\window\scripts\drag.js`（deep 语义）、`wry-0.57.0\src\lib.rs:1768-1769`、`wry-0.57.0\src\webview2\mod.rs:300-333/638-660`

---

## 9. 局限与未覆盖

1. 真实 WebView2 内部 DOM 无法通过 CDP 检查（wry 覆盖 additional_browser_args），媒体特性/键盘/无障碍用 Playwright Chromium 156 同特性验证 + 真机原生输入/日志三方交叉；真机 WebView2 版本 155，无法直接验证 109/120 旧运行时渲染差异（仅按特性发布版本分析）。
2. 未修改系统主题/缩放/文本大小（避免干扰并行审查与宿主机），高对比度与文本缩放为模拟/代码级结论，**建议发布前用 Win11 真机 HC 黑/白主题 + TextScaleFactor 125/150% 各跑一遍截图回归**。
3. 未做多显示器不同 DPI 拖移实机测试（单屏环境）；相关代码路径 `panel.rs ScaleFactorChanged` 已存在，属 shell 报告范围。
4. 未做 2 小时以上长稳内存采样（并行负载会干扰）；本次 400 次更新 + 15 次隐藏/显示 churn 未见泄漏。
5. 无头模拟的 forced-colors 与真实 Windows 高对比度主题在 DWM/acrylic 交互上仍有差异，P2-1 的严重度需真机确认。
