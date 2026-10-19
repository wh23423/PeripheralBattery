//! 前端 → Rust 命令（协议 §4.2）。

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, State};

use crate::sidecar::CollectorState;
use crate::state::AppState;

/// 前端首帧完成：返回是否应显示窗口；Rust 此前不显示，避免白闪。
#[tauri::command]
pub fn ui_ready(app: AppHandle, state: State<'_, AppState>) -> Value {
    crate::log_info!("command ui_ready");
    state.mark_ui_ready();
    state.emit_app_state(&app);
    let collector_state = state.sidecar.state();
    let _ = app.emit("collector-state", &collector_state);

    // 默认启动只驻留托盘（不弹窗）；PB_SHOW_ON_START=1 强制显示（截图/帧率用），
    // 托盘在 ui_ready 之前被点开时也保持显示。
    let show = state.pb_show_on_start || state.window_visible();
    if show {
        crate::panel::show_panel(&app, "ui-ready");
    } else {
        crate::panel::mark_startup_hidden(&app);
    }
    json!({ "show": show })
}

/// 写一行命令给 collector（协议 §2）。
#[tauri::command]
pub fn collector_send(line: String, state: State<'_, AppState>) -> bool {
    let ok = state.sidecar.send_line(&line);
    crate::log_debug!("command collector_send ok={ok} line_len={}", line.len());
    ok
}

/// 重启 sidecar（版本握手失败 / 采集器更新后）。
#[tauri::command]
pub fn collector_restart(state: State<'_, AppState>) -> bool {
    crate::log_info!("command collector_restart");
    state.sidecar.restart()
}

/// 固定面板，持久化到 app config，并同步托盘菜单勾选（P1-12：Rust 侧是唯一事实源）。
#[tauri::command]
pub fn set_pinned(pinned: bool, app: AppHandle, state: State<'_, AppState>) -> bool {
    crate::log_info!("command set_pinned pinned={pinned} source=frontend");
    let ok = state.set_pinned(&app, pinned);
    crate::tray::sync_pin_menu(pinned);
    ok
}

/// 托盘状态色：level/charging/online/kind → 图标。
/// 前端在可见态/兜底时下发；Rust 侧 tray_mirror 也会在隐藏态直接更新，二者幂等。
#[tauri::command]
pub fn set_tray_state(
    app: AppHandle,
    state: State<'_, AppState>,
    level: Option<i64>,
    charging: bool,
    online: bool,
    kind: String,
) {
    let payload = crate::tray::TrayState {
        level,
        charging,
        online,
        kind,
    };
    state.update_tray_from_devices(&app, payload, "frontend");
}

/// 窗口动作：关闭按钮 = hide；最小化同样走完整隐藏四步（skipTaskbar 应用无任务栏恢复入口，
/// 最小化会变成永久不可恢复，见 audit P1-1）。
#[tauri::command]
pub fn window_action(action: String, app: AppHandle) {
    crate::log_info!("command window_action action={action}");
    match action.as_str() {
        "hide" => crate::panel::hide_panel(&app, "window-action", true),
        "minimize" => {
            crate::log_info!("window_action minimize -> 走隐藏四步（等价 hide）");
            crate::panel::hide_panel(&app, "minimize", true);
        }
        "quit" => crate::state::request_quit(&app, "window-action"),
        other => crate::log_warn!("window_action 未知动作: {other}"),
    }
}

/// FPS 探针回传（PB_FPS_PROBE）：把前端测得的帧时间数据写进 app.log。
#[tauri::command]
pub fn report_fps(data: String) {
    if data.len() > 64 * 1024 {
        crate::log_warn!("report_fps payload 过大({} bytes)，仅记录前 64KB", data.len());
        let head: String = data.chars().take(64 * 1024).collect();
        crate::log_info!("FPS_PROBE {head}");
        return;
    }
    crate::log_info!("FPS_PROBE {data}");
}

/// 前端启动时主动拉当前 collector 状态。
#[tauri::command]
pub fn collector_status(state: State<'_, AppState>) -> CollectorState {
    state.sidecar.state()
}
