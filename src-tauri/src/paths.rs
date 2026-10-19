//! 统一路径：`%LOCALAPPDATA%\PeripheralBattery\...`（协议 §6）。

use std::env;
use std::fs;
use std::path::PathBuf;

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

impl Paths {
    pub fn resolve() -> Self {
        let base = env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .or_else(|| {
                env::var_os("USERPROFILE").map(|p| {
                    PathBuf::from(p)
                        .join("AppData")
                        .join("Local")
                })
            })
            .unwrap_or_else(|| PathBuf::from("."));
        let data_dir = base.join("PeripheralBattery");
        let logs_dir = data_dir.join("logs");
        let diagnostics = data_dir.join("diagnostics");
        let _ = fs::create_dir_all(&logs_dir);
        let _ = fs::create_dir_all(&diagnostics);
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
