//! 外设电量显示器 — Tauri 2 外壳入口。
//!
//! 职责：窗口（acrylic / DWM 圆角 / 720×380 = 面板尺寸，备选 B）、托盘、失焦隐藏、隐藏四步
//! （含 WebView2 `put_IsVisible`）、collector sidecar（stdio NDJSON + Job Object）、
//! 以及协议 §4 的命令/事件转发。

#![windows_subsystem = "windows"]

mod commands;
mod config;
mod fps;
mod logging;
mod panel;
mod paths;
mod platform;
mod sidecar;
mod single_instance;
mod state;
mod tray;
mod tray_mirror;
mod webview2_check;

use std::sync::Arc;
use std::time::Duration;

use tauri::{Manager, RunEvent};

fn main() {
    let paths = paths::Paths::resolve();
    let level = logging::Level::parse(&std::env::var("PB_LOG_LEVEL").unwrap_or_default());
    let _logger = logging::init(paths.app_log.clone(), level);

    crate::log_info!(
        "app start version={} build={} pid={} exe={}",
        env!("CARGO_PKG_VERSION"),
        env!("APP_BUILD"),
        std::process::id(),
        std::env::current_exe()
            .map(|p| p.display().to_string())
            .unwrap_or_default()
    );

    // 单实例（BUILD_PLAN §1.3）：互斥体必须在创建窗口/托盘/collector 之前。
    // 已有实例时先通知它激活面板（命名事件），失败再 ShowWindow 兜底，然后本进程正常退出。
    let instance_name = std::env::var("PB_SINGLE_INSTANCE_NAME")
        .unwrap_or_else(|_| single_instance::DEFAULT_SINGLE_INSTANCE_NAME.to_string());
    let mut _single_instance_guard: Option<single_instance::SingleInstanceGuard> = None;
    let mut activation_event: Option<isize> = None;
    match single_instance::acquire(&instance_name) {
        single_instance::AcquireResult::AlreadyRunning { pid } => {
            let signaled = single_instance::signal_existing(&instance_name);
            let activated = if signaled {
                false
            } else {
                platform::activate_existing_window()
            };
            crate::log_info!(
                "single-instance: existing pid={pid:?} name={instance_name} signaled={signaled} window_activated={activated} exit"
            );
            return;
        }
        single_instance::AcquireResult::AccessDenied { pid: Some(pid) } => {
            // P1-05：同名互斥体存在但当前权限打不开，且找到了本程序窗口 → 已有另一权限
            // 级别的实例在运行。必须给出可见提示后退出，不能静默起第二个实例/第二个 collector。
            crate::log_warn!(
                "single-instance: permission conflict name={instance_name} existing_pid={pid} → show dialog and exit（不启动第二实例）"
            );
            single_instance::show_permission_conflict_message(Some(pid));
            crate::log_info!("single-instance: permission conflict dialog dismissed, exit");
            return;
        }
        single_instance::AcquireResult::AccessDenied { pid: None } => {
            // 权限被拒但枚举不到本程序窗口：无法确认已有实例，只能 WARN 后继续（不能凭空阻止启动）。
            crate::log_warn!(
                "single-instance: access denied name={instance_name} but no existing window found; continuing without single-instance protection"
            );
        }
        single_instance::AcquireResult::Acquired(guard) => {
            crate::log_info!(
                "single-instance: mutex acquired name={} pid={}",
                guard.name(),
                std::process::id()
            );
            activation_event = guard.activation_event_raw();
            _single_instance_guard = Some(guard);
        }
        single_instance::AcquireResult::Disabled(msg) => crate::log_warn!("{msg}"),
    }

    // WebView2 运行期预检（review P1-02，由 packaging/webview2_check.rs 提供）：
    // 必须在 tauri::Builder::default() 之前、logging::init 之后；缺失/过旧时中文提示后
    // 以退出码 2/3 退出，避免用户看到“双击没反应”。PB_SKIP_WEBVIEW2_CHECK=1 可跳过。
    webview2_check::ensure_available_or_exit();

    let windows_build = platform::windows_build();
    // 取不到 build 号时不要误判成 Win10：按 Win11 处理（DWM 调用失败会被忽略），
    // 避免现代系统被错误关掉 acrylic。
    if windows_build == 0 {
        crate::log_warn!("windows build 检测失败（RtlGetVersion/GetVersionExW/registry 均失败），按 Windows 11 处理");
    }
    let windows11 = windows_build == 0 || windows_build >= 22000;
    crate::log_info!(
        "platform windows_build={windows_build} windows11={windows11} data_dir={}",
        paths.data_dir.display()
    );

    // app 自身配置独立于 collector 的 config.json（audit P2-5）；旧 pinned 一次性迁移。
    let (cfg, cfg_warn) = config::AppConfig::load_with_migration(&paths.app_config, &paths.config);
    if let Some(w) = cfg_warn {
        crate::log_info!("app config: {w}");
    }
    crate::log_info!(
        "app.json loaded pinned={} path={}",
        cfg.pinned,
        paths.app_config.display()
    );

    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::ui_ready,
            commands::collector_send,
            commands::collector_restart,
            commands::set_pinned,
            commands::set_tray_state,
            commands::window_action,
            commands::collector_status,
            commands::report_fps,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let sidecar = Arc::new(sidecar::Sidecar::new(handle.clone(), paths.clone()));
            let app_state =
                state::AppState::new(paths.clone(), cfg.clone(), sidecar.clone(), windows_build);
            app.manage(app_state);

            panel::apply_platform(&handle, windows11, windows_build);
            panel::attach_window_events(&handle);
            if let Some(event_raw) = activation_event {
                single_instance::spawn_activation_listener(handle.clone(), event_raw);
            }
            tray::create_tray(&handle)?;
            sidecar.start();
            state::schedule_env_timers(&handle);

            // ui_ready 兜底：前端若尚未接入 Tauri 桥（或首帧失败），2s 后仍然显示面板，
            // 避免"进程起了但永远没有窗口"；前端正常握手时窗口早已显示，这里是无操作。
            let fallback = handle.clone();
            std::thread::Builder::new()
                .name("ui-ready-fallback".into())
                .spawn(move || {
                    std::thread::sleep(Duration::from_millis(2000));
                    let h = fallback.clone();
                    let _ = fallback.run_on_main_thread(move || {
                        // 默认启动是 hidden（只驻留托盘）；只有显式 PB_SHOW_ON_START=1
                        // 才在 ui_ready 缺失时兜底显示。
                        if !state::env_flag("PB_SHOW_ON_START") {
                            return;
                        }
                        if let Some(app_state) = h.try_state::<state::AppState>() {
                            // F4：ui_ready 已收到就绝不兜底——窗口不可见可能只是失焦自动隐藏
                            // 或用户主动关闭，不能重新拉起。
                            if app_state.ui_ready_received() {
                                crate::log_debug!(
                                    "ui_ready received; fallback thread exits (window_visible={})",
                                    app_state.window_visible()
                                );
                                return;
                            }
                            if !app_state.window_visible() {
                                crate::log_warn!(
                                    "ui_ready not received within 2000ms; showing panel (fallback)"
                                );
                                panel::show_panel(&h, "ui-ready-fallback");
                            }
                        }
                    });
                })
                .ok();
            // [debug-only] 托盘菜单动作注入钩子：本机桌面被全屏应用占用、无法用真实右键
            // 菜单做自动化验收时，用 PB_TEST_TRAY_ACTION=settings 触发与菜单完全相同的
            // Rust 分支（release 构建不编译此代码）。
            #[cfg(debug_assertions)]
            if let Ok(action) = std::env::var("PB_TEST_TRAY_ACTION") {
                let hook_handle = handle.clone();
                std::thread::Builder::new()
                    .name("test-tray-action".into())
                    .spawn(move || {
                        std::thread::sleep(Duration::from_millis(2500));
                        let h = hook_handle.clone();
                        let _ = hook_handle.run_on_main_thread(move || {
                            crate::log_info!("[debug-only] PB_TEST_TRAY_ACTION={action}");
                            tray::handle_menu(&h, &action);
                        });
                    })
                    .ok();
            }

            crate::log_info!("setup complete");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| match event {
        RunEvent::ExitRequested { .. } => {
            crate::log_info!("run event: ExitRequested -> shutdown handshake");
            if let Some(app_state) = app_handle.try_state::<state::AppState>() {
                app_state.sidecar.shutdown(Duration::from_secs(2), true);
            }
        }
        RunEvent::Exit => {
            crate::log_info!("run event: Exit");
            if let Some(app_state) = app_handle.try_state::<state::AppState>() {
                app_state.sidecar.shutdown(Duration::from_secs(2), false);
            }
            crate::log_info!("app exit");
        }
        _ => {}
    });
}
