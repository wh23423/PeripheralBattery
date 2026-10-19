//! app 自身配置：`%APPDATA%\PeripheralBattery\app.json`（S-01 / v0.2.0）。
//!
//! 保存系统 / 进程层设置：`pinned`、`opacity`、`autostart`、`notify_low`、
//! `notify_full`（schema 2）。采集层设置（低电阈值、轮询档位等）仍由
//! collector 独占 `config.json`，Rust 不写它，避免两端 RMW 竞态互相覆盖
//! （audit P2-5）。首次启动时若 `app.json` 不存在、旧 `config.json` 里有
//! `pinned`，迁移一次到 `app.json`（不改动 collector 的其它字段）。
//!
//! 兼容性：旧版 `{"pinned": true}` 仍可读；opacity 越界在读取时 clamp；
//! 坏 JSON 回落到默认值并记录日志（不阻塞启动）。

use std::fs;
use std::io::Write;
use std::path::Path;

use serde::{Deserialize, Serialize};

/// 当前 app.json schema 版本。v0.1.2 没有 schema 字段（等价 schema 1）。
pub const CONFIG_SCHEMA: u32 = 2;
pub const OPACITY_MIN: u8 = 50;
pub const OPACITY_MAX: u8 = 100;
pub const OPACITY_DEFAULT: u8 = 60;

fn default_schema() -> u32 {
    CONFIG_SCHEMA
}
fn default_opacity() -> u8 {
    OPACITY_DEFAULT
}
fn default_notify() -> bool {
    true
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    pub schema: u32,
    pub pinned: bool,
    /// 面板底色不透明度（50..=100，100 = 完全不透明）。
    pub opacity: u8,
    /// 用户意图：登录 Windows 后自动启动（注册表实际状态见 autostart::status）。
    pub autostart: bool,
    pub notify_low: bool,
    pub notify_full: bool,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            schema: default_schema(),
            pinned: false,
            opacity: default_opacity(),
            autostart: false,
            notify_low: default_notify(),
            notify_full: default_notify(),
        }
    }
}

impl AppConfig {
    /// 把数值字段收敛到合法区间（读取旧/被手工编辑的文件时使用）。
    pub fn normalize(&mut self) -> bool {
        let mut changed = false;
        if self.schema != CONFIG_SCHEMA {
            self.schema = CONFIG_SCHEMA;
            changed = true;
        }
        if self.opacity < OPACITY_MIN {
            self.opacity = OPACITY_MIN;
            changed = true;
        } else if self.opacity > OPACITY_MAX {
            self.opacity = OPACITY_MAX;
            changed = true;
        }
        changed
    }

    pub fn load(path: &Path) -> (Self, Option<String>) {
        match fs::read_to_string(path) {
            Ok(text) => match serde_json::from_str::<AppConfig>(&text) {
                Ok(mut cfg) => {
                    let normalized = cfg.normalize();
                    let note = normalized.then(|| "app.json 数值越界/schema 升级，已收敛到合法值".to_string());
                    (cfg, note)
                }
                Err(e) => (
                    AppConfig::default(),
                    Some(format!("app.json 解析失败({e})，使用默认值")),
                ),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (AppConfig::default(), None),
            Err(e) => (
                AppConfig::default(),
                Some(format!("app.json 读取失败({e})，使用默认值")),
            ),
        }
    }

    /// 读 app.json；不存在则从旧 config.json 迁移 pinned 并落盘 app.json。
    /// 旧 schema（缺 schema 字段或 schema < 2）读取后补默认值并写回一次。
    pub fn load_with_migration(app_path: &Path, legacy_config_path: &Path) -> (Self, Option<String>) {
        if app_path.is_file() {
            let raw = fs::read_to_string(app_path).ok();
            let mut legacy_schema = false;
            let mut missing_fields: Vec<&str> = Vec::new();
            if let Some(text) = raw.as_deref() {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(text) {
                    legacy_schema = v
                        .get("schema")
                        .and_then(|s| s.as_u64())
                        .map(|s| s < 2)
                        .unwrap_or(true);
                    // §5.5：首次运行补齐新字段并写回一次；手写/半截 app.json 也一并收敛。
                    for key in [
                        "schema",
                        "pinned",
                        "opacity",
                        "autostart",
                        "notify_low",
                        "notify_full",
                    ] {
                        if v.get(key).is_none() {
                            missing_fields.push(key);
                        }
                    }
                }
            }
            let (mut cfg, mut note) = Self::load(app_path);
            let normalized = cfg.normalize();
            if legacy_schema || normalized || !missing_fields.is_empty() {
                match cfg.save(app_path) {
                    Ok(()) => {
                        let mut notes = Vec::new();
                        if let Some(n) = note.take() {
                            notes.push(n);
                        }
                        if legacy_schema {
                            notes.push("schema 1 → 2 迁移，补齐新字段".to_string());
                        }
                        if !missing_fields.is_empty() {
                            notes.push(format!(
                                "补齐缺失字段 {}",
                                missing_fields.join(",")
                            ));
                        }
                        notes.push(format!("rewrote {}", app_path.display()));
                        note = Some(notes.join("; "));
                    }
                    Err(e) => note = Some(format!("app.json 迁移写回失败: {e}")),
                }
            }
            return (cfg, note);
        }
        let mut cfg = AppConfig::default();
        let mut notes: Vec<String> = Vec::new();
        match fs::read_to_string(legacy_config_path) {
            Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                Ok(v) => {
                    if let Some(p) = v.get("pinned").and_then(|x| x.as_bool()) {
                        cfg.pinned = p;
                        notes.push(format!("migrated pinned={p} from config.json"));
                    } else {
                        notes.push("config.json 无 pinned 字段，app.json 使用默认值".to_string());
                    }
                }
                Err(e) => notes.push(format!(
                    "旧 config.json 解析失败({e})，app.json 使用默认值"
                )),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                notes.push("app.json 与 config.json 均不存在，创建默认 app.json".to_string());
            }
            Err(e) => notes.push(format!("旧 config.json 读取失败({e})，app.json 使用默认值")),
        }
        match cfg.save(app_path) {
            Ok(()) => notes.push(format!("created {}", app_path.display())),
            Err(e) => notes.push(format!("app.json 写入失败: {e}")),
        }
        (cfg, Some(notes.join("; ")))
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let text = serde_json::to_string_pretty(self)
            .map_err(std::io::Error::other)?;
        let tmp = path.with_extension("json.tmp");
        {
            let mut f = fs::File::create(&tmp)?;
            f.write_all(text.as_bytes())?;
            f.write_all(b"\n")?;
            f.sync_all()?;
        }
        fs::rename(&tmp, path)
    }
}

#[cfg(test)]
mod tests {
    use super::{AppConfig, CONFIG_SCHEMA, OPACITY_MAX, OPACITY_MIN};
    use std::fs;

    fn tmp_dir(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "pb-appconfig-test-{}-{}-{tag}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn migrates_pinned_from_legacy_config_without_touching_it() {
        let dir = tmp_dir("migrate");
        let legacy = dir.join("config.json");
        let app = dir.join("app.json");
        let legacy_text = r#"{"pinned": true, "poll": {"mouse": 5}, "threshold": 20}"#;
        fs::write(&legacy, legacy_text).unwrap();

        let (cfg, note) = AppConfig::load_with_migration(&app, &legacy);
        assert!(cfg.pinned);
        // 新字段使用默认值
        assert_eq!(cfg.schema, CONFIG_SCHEMA);
        assert_eq!(cfg.opacity, 60);
        assert!(!cfg.autostart);
        assert!(cfg.notify_low);
        assert!(cfg.notify_full);
        assert!(note.unwrap_or_default().contains("migrated pinned=true"));
        assert!(app.is_file());
        // collector 的其它字段原样保留
        assert_eq!(fs::read_to_string(&legacy).unwrap(), legacy_text);

        // 第二次启动直接读 app.json，不再依赖旧 config.json
        fs::remove_file(&legacy).unwrap();
        let (cfg2, note2) = AppConfig::load_with_migration(&app, &legacy);
        assert!(cfg2.pinned);
        assert!(note2.is_none());
    }

    #[test]
    fn pinned_roundtrip_and_default_when_missing() {
        let dir = tmp_dir("roundtrip");
        let legacy = dir.join("config.json");
        let app = dir.join("app.json");
        let (cfg, _) = AppConfig::load_with_migration(&app, &legacy);
        assert!(!cfg.pinned);
        cfg.save(&app).unwrap();
        let (loaded, note) = AppConfig::load(&app);
        assert!(!loaded.pinned);
        assert!(note.is_none());

        let mut cfg = loaded;
        cfg.pinned = true;
        cfg.save(&app).unwrap();
        let (loaded, _) = AppConfig::load(&app);
        assert!(loaded.pinned);
    }

    #[test]
    fn legacy_v1_json_without_schema_is_migrated_and_rewritten() {
        let dir = tmp_dir("legacy-v1");
        let app = dir.join("app.json");
        let legacy = dir.join("config.json");
        // v0.1.2 形态：只有 pinned，没有 schema / 新字段
        fs::write(&app, r#"{"pinned": true}"#).unwrap();
        let (cfg, note) = AppConfig::load_with_migration(&app, &legacy);
        assert!(cfg.pinned, "旧 pinned 必须保留");
        assert_eq!(cfg.schema, CONFIG_SCHEMA);
        assert_eq!(cfg.opacity, 60);
        assert!(!cfg.autostart);
        assert!(cfg.notify_low && cfg.notify_full);
        let note = note.unwrap_or_default();
        assert!(note.contains("schema 1 → 2"), "note={note}");
        // 写回后包含全部字段，且第二次读取不再迁移
        let text = fs::read_to_string(&app).unwrap();
        assert!(text.contains("\"schema\": 2"), "text={text}");
        assert!(text.contains("\"notify_low\": true"), "text={text}");
        let (_, note2) = AppConfig::load_with_migration(&app, &legacy);
        assert!(note2.is_none(), "第二次启动不应重复迁移: {note2:?}");
    }

    #[test]
    fn opacity_is_clamped_and_unknown_fields_are_ignored() {
        let dir = tmp_dir("clamp");
        let app = dir.join("app.json");
        let legacy = dir.join("config.json");
        fs::write(
            &app,
            r#"{"schema": 2, "opacity": 5, "future_field": {"x": 1}, "notify_low": false}"#,
        )
        .unwrap();
        let (cfg, _) = AppConfig::load_with_migration(&app, &legacy);
        assert_eq!(cfg.opacity, OPACITY_MIN);
        assert!(!cfg.notify_low);
        assert!(cfg.notify_full);

        fs::write(&app, r#"{"schema": 2, "opacity": 250}"#).unwrap();
        let (cfg, _) = AppConfig::load(&app);
        assert_eq!(cfg.opacity, OPACITY_MAX);
    }

    #[test]
    fn schema2_with_missing_fields_is_backfilled_once() {
        let dir = tmp_dir("backfill");
        let app = dir.join("app.json");
        let legacy = dir.join("config.json");
        // schema 已是 2，但缺 opacity/autostart/notify_*：首次读取应补齐并写回。
        fs::write(&app, r#"{"schema": 2, "pinned": true}"#).unwrap();
        let (cfg, note) = AppConfig::load_with_migration(&app, &legacy);
        assert!(cfg.pinned);
        assert_eq!(cfg.opacity, 60);
        let note = note.unwrap_or_default();
        assert!(note.contains("补齐缺失字段"), "note={note}");
        let text = fs::read_to_string(&app).unwrap();
        assert!(text.contains("\"opacity\": 60"), "text={text}");
        assert!(text.contains("\"autostart\": false"), "text={text}");
        assert!(text.contains("\"notify_full\": true"), "text={text}");
        let (_, note2) = AppConfig::load_with_migration(&app, &legacy);
        assert!(note2.is_none(), "补齐后不应再次重写: {note2:?}");
    }

    #[test]
    fn broken_json_falls_back_to_defaults() {
        let dir = tmp_dir("broken");
        let app = dir.join("app.json");
        fs::write(&app, "{ not json").unwrap();
        let (cfg, note) = AppConfig::load(&app);
        assert_eq!(cfg.opacity, 60);
        assert!(note.unwrap_or_default().contains("解析失败"));
    }
}
