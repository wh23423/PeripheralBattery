<#
.SYNOPSIS
  构建 collector sidecar（PyInstaller --onedir）→ dist\collector\。

.DESCRIPTION
  产物结构（BUILD_PLAN §6.1）：
    dist\collector\collector.exe
    dist\collector\_internal\...
  tauri.conf.json 的 bundle.resources 把 dist\collector\ 整个映射到安装包的 collector\，
  运行时 resource_dir()\collector\collector.exe 启动（不用 externalBin，否则 _internal 丢失）。

.PARAMETER Python
  指定 Python 解释器；默认优先项目 .venv\Scripts\python.exe，其次 PB_PYTHON，最后系统 Python 3.12。
#>
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Python) {
    $venvPy = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $Python = $venvPy
    } elseif ($env:PB_PYTHON) {
        $Python = $env:PB_PYTHON
    } else {
        $Python = "C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe"
    }
}
if (-not (Test-Path $Python)) {
    throw "Python 不存在: $Python（可用 -Python 或 PB_PYTHON 指定）"
}

$entry = Join-Path $root "collector_entry.py"
if (-not (Test-Path $entry)) {
    throw "未找到 collector_entry.py —— PyInstaller 入口（collector/__main__.py 是包内相对导入，不能直接当脚本跑）"
}
if (-not (Test-Path (Join-Path $root "collector\__main__.py"))) {
    throw "未找到 collector\__main__.py —— collector 尚未就绪，无法打包 sidecar"
}

# 确保 PyInstaller 可用
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 PyInstaller ..."
    & $Python -m pip install --disable-pip-version-check pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
}

$distRoot = Join-Path $root "dist"
$dist = Join-Path $distRoot "collector"
$work = Join-Path $root "build\pyinstaller"
if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
New-Item -ItemType Directory -Force -Path $distRoot, $work | Out-Null

# cwd = 项目根 + --paths 项目根：collector 包内 `from collector... / from .providers` 才能被分析到。
# --collect-submodules collector 保证 providers 等动态导入的子模块进包。
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name collector `
    --distpath $distRoot `
    --workpath $work `
    --specpath $work `
    --paths $root `
    --collect-submodules collector `
    --hidden-import collector `
    $entry
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败（exit=$LASTEXITCODE）" }

$exe = Join-Path $dist "collector.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller 产物缺失: $exe" }
$internal = Join-Path $dist "_internal"
if (-not (Test-Path $internal)) { Write-Warning "未发现 _internal 目录，检查 PyInstaller 版本" }

$size = (Get-ChildItem -Recurse -File $dist | Measure-Object -Property Length -Sum).Sum
Write-Host ("collector built: {0}（{1:N1} MB）" -f $exe, ($size / 1MB))
