//! app 自身配置：`%LOCALAPPDATA%\PeripheralBattery\app.json`（只存 `pinned`）。
//!
//! `config.json` 是采集层配置，由 collector 独占读写；Rust 不再写它，
//! 避免两端 RMW 竞态互相覆盖（audit P2-5）。首次启动时若 `app.json` 不存在、
//! 旧 `config.json` 里有 `pinned`，迁移一次到 `app.json`（不改动 collector 的其它字段）。

use std::fs;
use std::io::Write;
use std::path::Path;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct AppConfig {
    #[serde(default)]
    pub pinned: bool,
}

impl AppConfig {
    pub fn load(path: &Path) -> (Self, Option<String>) {
        match fs::read_to_string(path) {
            Ok(text) => match serde_json::from_str::<AppConfig>(&text) {
                Ok(cfg) => (cfg, None),
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
    pub fn load_with_migration(app_path: &Path, legacy_config_path: &Path) -> (Self, Option<String>) {
        if app_path.is_file() {
            return Self::load(app_path);
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
    use super::AppConfig;
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
}
