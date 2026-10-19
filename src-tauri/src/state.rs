//! 应用共享状态：pin、隐藏抑制、托盘状态、可见性代际（用于取消延迟隐藏）、
//! 环境变量开关（PB_SHOW_ON_START / PB_HIDE_AFTER_MS / PB_QUIT_AFTER_MS）。

use std::env;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::json;
use tauri::tray::TrayIcon;
use tauri::{AppHandle, Emitter, Manager, Wry};

use crate::config::AppConfig;
use crate::paths::Paths;
use crate::sidecar::Sidecar;
use crate::tray::TrayState;
use crate::tray_mirror::TrayMirror;

pub fn env_flag(name: &str) -> bool {
    match env::var(name) {
        Ok(v) => matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
        Err(_) => false,
    }
}

pub fn env_ms(name: &str) -> Option<u64> {
    env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|v| *v > 0)
}

pub struct AppState {
    pub paths: Paths,
    pub config: Mutex<AppConfig>,
    pub pinned: AtomicBool,
    pub window_visible: AtomicBool,
    /// 前端是否真的调用过 ui_ready（用于启动 2s 兜底判断，避免把失焦隐藏误判成「未收到」）。
    ui_ready_received: AtomicBool,
    /// 缓存的 DPI scale（f64 bits，0 = 未设置）。托盘图标渲染来自侧车线程时不能跨线程
    /// 查窗口，统一读缓存；主线程在窗口创建/scaleFactorChanged 时写入。
    scale_factor_bits: AtomicU64,
    hide_generation: AtomicU64,
    suppressed_until: Mutex<Option<Instant>>,
    tray_rect: Mutex<Option<(i32, i32, i32, i32)>>,
    pub tray: Mutex<Option<TrayIcon<Wry>>>,
    pub tray_state: Mutex<TrayState>,
    /// Rust 侧独立维护的托盘状态镜像（不依赖 WebView，隐藏态 TrySuspend 后仍更新）。
    pub tray_mirror: Mutex<TrayMirror>,
    pub sidecar: Arc<Sidecar>,
    pub windows11: bool,
    pub windows_build: u32,
    /// 窗口 acrylic 是否真正启用（A14）：透明效果关闭/远程会话/PB_DISABLE_ACRYLIC=1
    /// 时为 false，前端据此提高面板底色不透明度，保证无模糊时的对比度。
    acrylic: AtomicBool,
    pub pb_show_on_start: bool,
    pub pb_hide_after_ms: Option<u64>,
    pb_hide_armed: AtomicBool,
}

impl AppState {
    pub fn new(
        paths: Paths,
        cfg: AppConfig,
        sidecar: Arc<Sidecar>,
        windows_build: u32,
    ) -> Self {
        let windows11 = windows_build == 0 || windows_build >= 22000;
        Self {
            paths,
            pinned: AtomicBool::new(cfg.pinned),
            config: Mutex::new(cfg),
            window_visible: AtomicBool::new(false),
            ui_ready_received: AtomicBool::new(false),
            scale_factor_bits: AtomicU64::new(0),
            hide_generation: AtomicU64::new(0),
            suppressed_until: Mutex::new(None),
            tray_rect: Mutex::new(None),
            tray: Mutex::new(None),
            tray_state: Mutex::new(TrayState::default()),
            tray_mirror: Mutex::new(TrayMirror::default()),
            sidecar,
            windows11,
            windows_build,
            // Win10 一律没有 DWM acrylic：初始即为 false，apply_platform 会按
            // 透明效果/远程会话/PB_DISABLE_ACRYLIC 再校准 Win11 的情况。
            acrylic: AtomicBool::new(windows11),
            pb_show_on_start: env_flag("PB_SHOW_ON_START"),
            pb_hide_after_ms: env_ms("PB_HIDE_AFTER_MS"),
            pb_hide_armed: AtomicBool::new(false),
        }
    }

    pub fn pinned(&self) -> bool {
        self.pinned.load(Ordering::SeqCst)
    }

    pub fn window_visible(&self) -> bool {
        self.window_visible.load(Ordering::SeqCst)
    }

    pub fn set_window_visible(&self, visible: bool) {
        self.window_visible.store(visible, Ordering::SeqCst);
    }

    /// acrylic 是否启用（A14）。windowEffects 不可用时为 false。
    pub fn acrylic(&self) -> bool {
        self.acrylic.load(Ordering::SeqCst)
    }

    pub fn set_acrylic(&self, enabled: bool) {
        self.acrylic.store(enabled, Ordering::SeqCst);
    }

    pub fn mark_ui_ready(&self) {
        self.ui_ready_received.store(true, Ordering::SeqCst);
    }

    pub fn ui_ready_received(&self) -> bool {
        self.ui_ready_received.load(Ordering::SeqCst)
    }

    pub fn set_scale_factor(&self, scale: f64) {
        if scale > 0.0 {
            self.scale_factor_bits
                .store(scale.to_bits(), Ordering::SeqCst);
        }
    }

    pub fn cached_scale_factor(&self) -> Option<f64> {
        let bits = self.scale_factor_bits.load(Ordering::SeqCst);
        if bits == 0 {
            None
        } else {
            Some(f64::from_bits(bits))
        }
    }

    /// 短暂取托盘句柄克隆，**调用方不得在持有 tray mutex 的情况下调用任何 tray API**。
    pub fn tray_clone(&self) -> Option<TrayIcon<Wry>> {
        self.tray.lock().unwrap().clone()
    }

    /// 托盘状态的唯一写入口：rust（stdout 镜像）与 frontend（set_tray_state）共用，
    /// 按 payload 去重后更新图标并记录来源，二者幂等。
    /// 这里只改内存状态 + 非阻塞投递主线程，不碰任何 tray API / tray 锁。
    pub fn update_tray_from_devices(&self, app: &AppHandle, payload: TrayState, source: &str) {
        let changed = {
            let mut current = self.tray_state.lock().unwrap();
            if *current == payload {
                false
            } else {
                *current = payload.clone();
                true
            }
        };
        if changed {
            crate::log_info!(
                "tray-state source={source} level={:?} charging={} online={} kind={}",
                payload.level,
                payload.charging,
                payload.online,
                payload.kind
            );
            crate::tray::update_tray_icon(app, source);
        } else {
            crate::log_debug!(
                "tray-state source={source} unchanged level={:?} online={}",
                payload.level,
                payload.online
            );
        }
    }

    /// 递增隐藏代际：任何一次显示/隐藏/恢复焦点都会取消排队中的失焦隐藏。
    pub fn bump_hide_generation(&self) -> u64 {
        self.hide_generation.fetch_add(1, Ordering::SeqCst) + 1
    }

    pub fn hide_generation(&self) -> u64 {
        self.hide_generation.load(Ordering::SeqCst)
    }

    pub fn suppress_for_ms(&self, ms: u64) {
        let until = Instant::now() + Duration::from_millis(ms);
        *self.suppressed_until.lock().unwrap() = Some(until);
    }

    pub fn is_suppressed(&self) -> bool {
        match *self.suppressed_until.lock().unwrap() {
            Some(until) => Instant::now() < until,
            None => false,
        }
    }

    pub fn set_tray_rect(&self, rect: (i32, i32, i32, i32)) {
        *self.tray_rect.lock().unwrap() = Some(rect);
    }

    pub fn tray_rect(&self) -> Option<(i32, i32, i32, i32)> {
        *self.tray_rect.lock().unwrap()
    }

    pub fn app_state_payload(&self) -> serde_json::Value {
        json!({
            "pinned": self.pinned(),
            // A14：acrylic 不可用（透明效果关闭/远程会话/PB_DISABLE_ACRYLIC=1）时为 false，
            // 前端可据此把面板底色调实（html[data-acrylic="off"]）。
            "acrylic": self.acrylic(),
            "platform": {
                "windows11": self.windows11,
                "build": self.windows_build,
            },
            "build": env!("APP_BUILD"),
            "version": env!("CARGO_PKG_VERSION"),
        })
    }

    pub fn emit_app_state(&self, app: &AppHandle) {
        let payload = self.app_state_payload();
        let _ = app.emit("app-state", payload.clone());
        crate::log_debug!("app-state emitted {payload}");
    }

    pub fn set_pinned(&self, app: &AppHandle, pinned: bool) -> bool {
        self.pinned.store(pinned, Ordering::SeqCst);
        let saved = {
            let mut cfg = self.config.lock().unwrap();
            cfg.pinned = pinned;
            cfg.save(&self.paths.app_config)
        };
        match saved {
            Ok(()) => crate::log_info!(
                "app.json saved pinned={pinned} path={}",
                self.paths.app_config.display()
            ),
            Err(e) => crate::log_warn!("app.json 保存失败: {e}"),
        }
        self.emit_app_state(app);
        true
    }

    /// PB_HIDE_AFTER_MS：首次显示后 n ms 走完整隐藏四步（一次性，用于隐藏态 CPU 采样）。
    pub fn arm_pb_hide(&self, app: &AppHandle) {
        let Some(ms) = self.pb_hide_after_ms else {
            return;
        };
        if self.pb_hide_armed.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::log_info!("PB_HIDE_AFTER_MS={ms} armed (one-shot after first show)");
        let handle = app.clone();
        std::thread::Builder::new()
            .name("pb-hide-after".into())
            .spawn(move || {
                std::thread::sleep(Duration::from_millis(ms));
                let h2 = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    crate::panel::hide_panel(&h2, "PB_HIDE_AFTER_MS", true);
                });
            })
            .ok();
    }
}

/// PB_QUIT_AFTER_MS：到点走正常退出路径（shutdown 握手 → Job 清理 → 退出）。
pub fn schedule_env_timers(app: &AppHandle) {
    if let Some(ms) = env_ms("PB_QUIT_AFTER_MS") {
        crate::log_info!("PB_QUIT_AFTER_MS={ms} armed");
        let handle = app.clone();
        std::thread::Builder::new()
            .name("pb-quit-after".into())
            .spawn(move || {
                std::thread::sleep(Duration::from_millis(ms));
                request_quit(&handle, "PB_QUIT_AFTER_MS");
            })
            .ok();
    }
}

/// 正常退出路径：先与 collector 握手，再退出（协议 §2/§6.2）。
pub fn request_quit(app: &AppHandle, reason: &str) {
    crate::log_info!("quit requested reason={reason}");
    let handle = app.clone();
    let spawned = std::thread::Builder::new()
        .name("quit".into())
        .spawn(move || {
            if let Some(state) = handle.try_state::<AppState>() {
                state.sidecar.shutdown(Duration::from_secs(2), true);
            }
            // 留一点时间让 sidecar 退出日志/Job 清理日志落盘
            std::thread::sleep(Duration::from_millis(120));
            crate::log_info!("quit collector stopped, exiting");
            handle.exit(0);
        });
    if spawned.is_err() {
        if let Some(state) = app.try_state::<AppState>() {
            state.sidecar.shutdown(Duration::from_secs(2), true);
        }
        app.exit(0);
    }
}
