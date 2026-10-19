//! 统一路径：用户数据目录 `%APPDATA%\PeripheralBattery`（安装目录与数据分离）。
//!
//! 背景（review P1-01）：0.1.0 的默认安装目录就是数据目录
//! `%LOCALAPPDATA%\PeripheralBattery`，卸载时只删程序文件，`app.json` /
//! `config.json` / `aliases.json` / `history.json` / `logs` / `diagnostics`
//! 全部残留，而且安装器里的“删除应用数据”删的是 WebView2 profile，不是真数据。
//!
//! 0.1.1 起：
//! - 用户数据放在 `%APPDATA%\PeripheralBattery`，安装目录（默认
//!   `%LOCALAPPDATA%\PeripheralBattery`）只放程序文件；
//! - `PB_DATA_DIR` 可覆盖数据目录。解析后把最终目录回写到同名环境变量，
//!   collector sidecar（Python `collector/settings.py` 的 `_env_data_dir()`）
//!   因此继承同一目录；
//! - 首次启动把 0.1.0 的旧数据目录 `%LOCALAPPDATA%\PeripheralBattery` 迁移过来：
//!   只复制目标缺失的文件（不覆盖新数据），迁移一次后写 marker；迁移失败不阻塞
//!   启动，只在 app.log 记录一条。
//!
//! 卸载清理见 `src-tauri/nsis/installer-hooks.nsh`（同一数据目录常量）。

use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

/// 数据目录环境变量：Rust 与 collector 共用；Rust 解析后回写供 sidecar 继承。
pub const DATA_DIR_ENV: &str = "PB_DATA_DIR";
/// 数据目录名（`%APPDATA%\PeripheralBattery` / 旧 `%LOCALAPPDATA%\PeripheralBattery`）。
pub const DATA_DIR_NAME: &str = "PeripheralBattery";
/// 迁移完成标记：存在即不再扫描旧目录（避免每次启动重复复制）。
pub const MIGRATION_MARKER: &str = "migration-v0.1.1.json";

/// 0.1.0 数据目录里的普通文件（collector.pid.json 是过期 pid，不迁移）。
const MIGRATED_FILES: &[&str] = &["config.json", "app.json", "aliases.json", "history.json"];
/// 0.1.0 数据目录里的子目录。
const MIGRATED_DIRS: &[&str] = &["logs", "diagnostics"];

#[derive(Clone, Debug)]
#[allow(dead_code)]
pub struct Paths {
    pub data_dir: PathBuf,
    /// collector 的配置（collector 独占读写；Rust 只在首次迁移 pinned 时读取）。
    pub config: PathBuf,
    /// app 自身配置（pinned 等），与 collector 的 config.json 分开，避免 RMW 竞态。
    pub app_config: PathBuf,
    pub aliases: PathBuf,
    pub history: PathBuf,
    pub logs_dir: PathBuf,
    pub app_log: PathBuf,
    pub collector_log: PathBuf,
    pub collector_pid: PathBuf,
    pub diagnostics: PathBuf,
}

/// 首次迁移结果（用于日志与单测；`source` 为空表示未发生迁移）。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MigrationReport {
    pub source: PathBuf,
    pub target: PathBuf,
    pub copied: usize,
    pub skipped_existing: usize,
    pub errors: usize,
    pub marker_written: bool,
}

impl MigrationReport {
    pub fn attempted(&self) -> bool {
        !self.source.as_os_str().is_empty()
    }

    pub fn summary(&self) -> String {
        format!(
            "legacy={} -> {} copied={} skipped_existing={} errors={} marker={}",
            self.source.display(),
            self.target.display(),
            self.copied,
            self.skipped_existing,
            self.errors,
            if self.marker_written {
                "written"
            } else {
                "not-written"
            }
        )
    }
}

impl Paths {
    /// 解析全部路径；必要时做一次 0.1.0 → 0.1.1 数据迁移。
    pub fn resolve() -> Self {
        let (data_dir, explicit_override) = resolve_data_dir();
        let logs_dir = data_dir.join("logs");
        let diagnostics = data_dir.join("diagnostics");
        let _ = fs::create_dir_all(&logs_dir);
        let _ = fs::create_dir_all(&diagnostics);

        // 首次启动迁移：仅在用户未显式指定数据目录时进行（测试/便携模式不应被污染）。
        if !explicit_override {
            if let Some(legacy) = legacy_data_dir() {
                let marker = data_dir.join(MIGRATION_MARKER);
                if legacy != data_dir && legacy.is_dir() && !marker.is_file() {
                    let report = migrate_legacy_data(&legacy, &data_dir);
                    if report.attempted() {
                        append_log_line(&logs_dir.join("app.log"), &report.summary());
                    }
                }
            }
        }

        // 让 collector sidecar 继承同一数据目录（Command 默认继承父进程环境变量）。
        // Rust 2021 edition 下 set_var 是安全函数；main 在创建线程前调用。
        env::set_var(DATA_DIR_ENV, &data_dir);

        Self {
            config: data_dir.join("config.json"),
            app_config: data_dir.join("app.json"),
            aliases: data_dir.join("aliases.json"),
            history: data_dir.join("history.json"),
            app_log: logs_dir.join("app.log"),
            collector_log: logs_dir.join("collector.log"),
            collector_pid: data_dir.join("collector.pid.json"),
            logs_dir,
            diagnostics,
            data_dir,
        }
    }

    /// 项目根：从当前目录 / 可执行文件所在目录向上寻找 `src-tauri/tauri.conf.json`。
    pub fn project_root() -> Option<PathBuf> {
        if let Ok(v) = env::var("PB_PROJECT_ROOT") {
            let p = PathBuf::from(v);
            if p.join("collector").is_dir() || p.join("src-tauri").is_dir() {
                return Some(p);
            }
        }
        let mut starts: Vec<PathBuf> = Vec::new();
        if let Ok(cwd) = env::current_dir() {
            starts.push(cwd);
        }
        if let Ok(exe) = env::current_exe() {
            if let Some(parent) = exe.parent() {
                starts.push(parent.to_path_buf());
            }
        }
        for start in starts {
            for anc in start.ancestors() {
                if anc.join("src-tauri").join("tauri.conf.json").is_file()
                    || anc.join("collector").join("__main__.py").is_file()
                {
                    return Some(anc.to_path_buf());
                }
            }
        }
        None
    }
}

/// 解析数据目录：`PB_DATA_DIR` 优先，否则 `%APPDATA%\PeripheralBattery`。
/// 返回 `(目录, 是否显式覆盖)`。
fn resolve_data_dir() -> (PathBuf, bool) {
    if let Some(raw) = env::var_os(DATA_DIR_ENV) {
        let trimmed = raw.to_string_lossy();
        if !trimmed.trim().is_empty() {
            return (absolutize(PathBuf::from(raw)), true);
        }
    }
    (default_data_dir(), false)
}

/// `%APPDATA%\PeripheralBattery`；环境变量缺失时退回 `%USERPROFILE%\AppData\Roaming`。
fn default_data_dir() -> PathBuf {
    let base = env::var_os("APPDATA")
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("USERPROFILE")
                .map(|p| PathBuf::from(p).join("AppData").join("Roaming"))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    base.join(DATA_DIR_NAME)
}

/// 0.1.0 的数据目录：`%LOCALAPPDATA%\PeripheralBattery`（默认安装目录同路径）。
fn legacy_data_dir() -> Option<PathBuf> {
    let base = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("USERPROFILE")
                .map(|p| PathBuf::from(p).join("AppData").join("Local"))
        })?;
    Some(base.join(DATA_DIR_NAME))
}

fn absolutize(path: PathBuf) -> PathBuf {
    if path.is_absolute() {
        path
    } else {
        env::current_dir()
            .map(|cwd| cwd.join(&path))
            .unwrap_or(path)
    }
}

/// 把 0.1.0 数据目录复制到新目录（只补缺失文件，绝不覆盖已有数据）。
///
/// - 旧目录不存在 / 新旧目录相同：直接返回（`attempted()==false`）。
/// - 迁移标记已存在：不再重复扫描（返回 `attempted()==false`）。
/// - 单文件失败只累加 `errors` 并继续；`errors == 0` 时才写 marker，
///   否则下次启动会重试补缺失文件。
pub fn migrate_legacy_data(legacy: &Path, data_dir: &Path) -> MigrationReport {
    let mut report = MigrationReport {
        source: legacy.to_path_buf(),
        target: data_dir.to_path_buf(),
        ..Default::default()
    };
    if legacy == data_dir || !legacy.is_dir() || !data_dir.is_dir() {
        report.source = PathBuf::new();
        return report;
    }
    if data_dir.join(MIGRATION_MARKER).is_file() {
        report.source = PathBuf::new();
        return report;
    }

    for name in MIGRATED_FILES {
        let src = legacy.join(name);
        if !src.is_file() {
            continue;
        }
        let dst = data_dir.join(name);
        if dst.exists() {
            report.skipped_existing += 1;
            continue;
        }
        match fs::copy(&src, &dst) {
            Ok(_) => report.copied += 1,
            Err(_) => report.errors += 1,
        }
    }

    for name in MIGRATED_DIRS {
        let src = legacy.join(name);
        if !src.is_dir() {
            continue;
        }
        let dst = data_dir.join(name);
        if fs::create_dir_all(&dst).is_err() {
            report.errors += 1;
            continue;
        }
        copy_missing_tree(&src, &dst, &mut report);
    }

    if report.errors == 0 {
        let payload = serde_json::json!({
            "schema": 1,
            "migrated_from": legacy.display().to_string(),
            "migrated_to": data_dir.display().to_string(),
            "migrated_at": crate::platform::local_timestamp(),
            "copied": report.copied,
            "skipped_existing": report.skipped_existing,
        })
        .to_string();
        report.marker_written = fs::write(data_dir.join(MIGRATION_MARKER), payload).is_ok();
    }
    report
}

/// 递归复制 `src` 下目标不存在的文件（`logs/`、`diagnostics/`）。
fn copy_missing_tree(src: &Path, dst: &Path, report: &mut MigrationReport) {
    let entries = match fs::read_dir(src) {
        Ok(e) => e,
        Err(_) => {
            report.errors += 1;
            return;
        }
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let file_type = match entry.file_type() {
            Ok(t) => t,
            Err(_) => {
                report.errors += 1;
                continue;
            }
        };
        let target = dst.join(entry.file_name());
        if file_type.is_dir() {
            if fs::create_dir_all(&target).is_err() {
                report.errors += 1;
                continue;
            }
            copy_missing_tree(&path, &target, report);
        } else if file_type.is_file() {
            if target.exists() {
                report.skipped_existing += 1;
                continue;
            }
            match fs::copy(&path, &target) {
                Ok(_) => report.copied += 1,
                Err(_) => report.errors += 1,
            }
        }
    }
}

/// 迁移发生在 `logging::init` 之前，这里直接以 app.log 的同一行格式追加一条。
/// 失败时退回 stderr，绝不阻塞启动。
fn append_log_line(app_log: &Path, message: &str) {
    let line = format!(
        "{} [INFO] data dir migration: {message}",
        crate::platform::local_timestamp()
    );
    let ok = OpenOptions::new()
        .create(true)
        .append(true)
        .open(app_log)
        .and_then(|mut f| f.write_all(format!("{line}\n").as_bytes()))
        .is_ok();
    if !ok {
        eprintln!("{line}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static SEQ: AtomicU64 = AtomicU64::new(0);

    /// 每个用例独立的临时目录（测试并行安全）。
    fn temp_dir(tag: &str) -> PathBuf {
        let seq = SEQ.fetch_add(1, Ordering::SeqCst);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let dir = env::temp_dir().join(format!(
            "pb-paths-{tag}-{}-{seq}-{nanos}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    fn write(path: &Path, body: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, body).unwrap();
    }

    #[test]
    fn migrates_files_and_dirs_then_writes_marker() {
        let base = temp_dir("migrate");
        let legacy = base.join("Local").join("PeripheralBattery");
        let data = base.join("Roaming").join("PeripheralBattery");
        fs::create_dir_all(&data).unwrap();

        for (name, body) in [
            ("config.json", r#"{"poll":{}}"#),
            ("app.json", r#"{"pinned":true}"#),
            ("aliases.json", r#"{"a":"b"}"#),
            ("history.json", r#"[]"#),
        ] {
            write(&legacy.join(name), body);
        }
        write(&legacy.join("logs").join("app.log"), "old log\n");
        write(&legacy.join("diagnostics").join("diag.txt"), "old diag\n");
        write(&legacy.join("collector.pid.json"), r#"{"pid":123}"#);

        let report = migrate_legacy_data(&legacy, &data);
        assert!(report.attempted());
        assert_eq!(report.copied, 6, "{report:?}");
        assert_eq!(report.errors, 0);
        assert!(report.marker_written);
        assert_eq!(
            fs::read_to_string(data.join("app.json")).unwrap(),
            r#"{"pinned":true}"#
        );
        assert_eq!(
            fs::read_to_string(data.join("logs").join("app.log")).unwrap(),
            "old log\n"
        );
        // 过期 pid 文件不迁移
        assert!(!data.join("collector.pid.json").exists());

        // 第二次调用：marker 已存在 → 不重复迁移
        write(&legacy.join("history.json"), r#"["new"]"#);
        write(&legacy.join("extra.json"), "{}");
        let again = migrate_legacy_data(&legacy, &data);
        assert!(!again.attempted());
        assert_eq!(again.copied, 0);
        assert_eq!(
            fs::read_to_string(data.join("history.json")).unwrap(),
            r#"[]"#
        );
        assert!(!data.join("extra.json").exists());

        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn does_not_overwrite_existing_target_files() {
        let base = temp_dir("no-overwrite");
        let legacy = base.join("old");
        let data = base.join("new");
        write(&legacy.join("config.json"), r#"{"from":"legacy"}"#);
        write(&legacy.join("app.json"), r#"{"pinned":false}"#);
        write(&data.join("config.json"), r#"{"from":"newer"}"#);

        let report = migrate_legacy_data(&legacy, &data);
        assert_eq!(report.copied, 1); // 只有 app.json 补过去
        assert_eq!(report.skipped_existing, 1);
        assert_eq!(
            fs::read_to_string(data.join("config.json")).unwrap(),
            r#"{"from":"newer"}"#
        );
        assert_eq!(
            fs::read_to_string(data.join("app.json")).unwrap(),
            r#"{"pinned":false}"#
        );
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn missing_legacy_dir_is_noop() {
        let base = temp_dir("no-legacy");
        let data = base.join("new");
        fs::create_dir_all(&data).unwrap();
        let report = migrate_legacy_data(&base.join("nope"), &data);
        assert!(!report.attempted());
        assert_eq!(report.copied, 0);
        assert!(!data.join(MIGRATION_MARKER).exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn same_directory_is_noop() {
        let base = temp_dir("same");
        write(&base.join("app.json"), "{}");
        let report = migrate_legacy_data(&base, &base);
        assert!(!report.attempted());
        assert_eq!(report.copied, 0);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn data_dir_name_and_env_contract_are_stable() {
        // README / NSIS hooks / collector settings.py 都依赖这两个常量。
        assert_eq!(DATA_DIR_NAME, "PeripheralBattery");
        assert_eq!(DATA_DIR_ENV, "PB_DATA_DIR");
    }
}
