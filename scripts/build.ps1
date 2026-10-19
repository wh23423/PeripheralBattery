<#
.SYNOPSIS
  完整构建：collector sidecar + Tauri app（默认 release + NSIS 安装包 + 校验和/构建信息 + 可选签名）。

.DESCRIPTION
  顺序（BUILD_PLAN §6，review P1-03/P2-04 增强）：
    1. scripts\build-collector.ps1 → dist\collector\（PyInstaller onedir）
    2. npx tauri build          → 打包 dist\collector 到安装包 collector\
    3. scripts\sign.ps1         → 有证书则 Authenticode 签名 setup/app/collector，无证书安全跳过
    4. 生成 SHA256SUMS.txt + build-info.json（版本 / git sha / 时间 / 每个文件哈希 / signed 状态）

  签名配置（任一，未配置则跳过并记录 signed:false）：
    PB_SIGN_THUMBPRINT       证书指纹（证书须在 Cert:\CurrentUser\My / LocalMachine\My）
    PB_SIGN_CERT_PATH        PFX 路径（搭配 PB_SIGN_CERT_PASSWORD）
    PB_SIGN_CERT_PASSWORD    PFX 密码
    PB_SIGN_TIMESTAMP_URL    时间戳服务（默认 http://timestamp.digicert.com）
  签名需要证书；本仓库不包含证书，公开发布包默认 NotSigned（Release Notes 如实说明）。

.PARAMETER Debug
  debug 构建（验收用），默认 release。debug 构建不做签名/打包。

.PARAMETER NoBundle
  只编译可执行文件，不生成 NSIS 安装包。

.PARAMETER SkipCollector
  跳过 PyInstaller（直接用现有 dist\collector；collector 未就绪时用）。

.PARAMETER NoSign
  即使配置了证书也跳过签名（本地快速验证用），build-info 会记录 signed:false。

.EXAMPLE
  .\scripts\build.ps1
  .\scripts\build.ps1 -Debug -NoBundle -SkipCollector
  $env:PB_SIGN_THUMBPRINT="<thumbprint>"; .\scripts\build.ps1
#>
param(
    [switch]$Debug,
    [switch]$NoBundle,
    [switch]$SkipCollector,
    [switch]$NoSign
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Get-Sha256Hex {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-GitInfo {
    $info = @{ sha = "unknown"; branch = ""; dirty = $null }
    try {
        $sha = (& git rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $sha) { $info.sha = ($sha | Select-Object -First 1).Trim() }
        $short = (& git rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $short) { $info.short = ($short | Select-Object -First 1).Trim() }
        if (-not $info.short) {
            $info.short = if ($info.sha.Length -ge 7) { $info.sha.Substring(0, 7) } else { $info.sha }
        }
        $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $branch) { $info.branch = ($branch | Select-Object -First 1).Trim() }
        $porcelain = (& git status --porcelain 2>$null)
        if ($LASTEXITCODE -eq 0) { $info.dirty = [bool]($porcelain -and $porcelain.Trim().Length -gt 0) }
    } catch {
        Write-Warning "读取 git 信息失败（按 unknown 处理）：$_"
    }
    return $info
}

if ($SkipCollector) {
    Write-Host "跳过 collector 构建（使用现有 dist\collector）"
} else {
    $collectorMain = Join-Path $root "collector\__main__.py"
    $collectorEntry = Join-Path $root "collector_entry.py"
    if (-not (Test-Path $collectorMain) -or -not (Test-Path $collectorEntry)) {
        throw "collector 未就绪（缺 collector\__main__.py 或 collector_entry.py），无法打包 sidecar"
    }
    & (Join-Path $PSScriptRoot "build-collector.ps1")
    if ($LASTEXITCODE -ne 0) { throw "collector 构建失败" }
}

# 不允许把占位/空目录打进安装包：PyInstaller onedir 必须有 collector.exe + _internal。
$collectorExe = Join-Path $root "dist\collector\collector.exe"
$collectorInternal = Join-Path $root "dist\collector\_internal"
if (-not (Test-Path $collectorExe)) {
    throw "dist\collector\collector.exe 不存在；先运行 scripts\build-collector.ps1 再打包"
}
if (-not (Test-Path $collectorInternal)) {
    throw "dist\collector\_internal 不存在；PyInstaller onedir 产物不完整，拒绝打包"
}

$tauriArgs = @("tauri", "build")
if ($Debug) { $tauriArgs += "--debug" }
if ($NoBundle) { $tauriArgs += "--no-bundle" }

Write-Host "npx $($tauriArgs -join ' ')"
& npx.cmd @tauriArgs
if ($LASTEXITCODE -ne 0) { throw "tauri build 失败（exit=$LASTEXITCODE）" }

$outDir = if ($Debug) { "src-tauri\target\debug" } else { "src-tauri\target\release" }
$appExe = Join-Path $root "$outDir\peripheral-battery.exe"
Write-Host ("构建完成：{0}" -f $appExe)

$config = Get-Content -LiteralPath (Join-Path $root "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$version = $config.version

# 清理旧版本安装包：避免发布时把上一次构建的 setup.exe 当成本次产物。
if (-not $NoBundle -and -not $Debug) {
    $bundleNsisDir = Join-Path $root "src-tauri\target\release\bundle\nsis"
    if (Test-Path -LiteralPath $bundleNsisDir) {
        Get-ChildItem -Path $bundleNsisDir -Filter "*-setup.exe" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike "*_${version}_*-setup.exe" } |
            ForEach-Object {
                Write-Host ("清理旧版本安装包：{0}" -f $_.FullName)
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            }
    }
}

$setupExe = $null
if (-not $NoBundle -and -not $Debug) {
    $setupExe = Get-ChildItem -Path "src-tauri\target\release\bundle\nsis" -Filter "*-setup.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($setupExe) { $setupExe = $setupExe.FullName }
    Get-ChildItem -Recurse -File "src-tauri\target\release\bundle" -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".exe", ".msi", ".zip" } |
        ForEach-Object { Write-Host ("安装包: {0}" -f $_.FullName) }
}

# ---------------------------------------------------------------- 签名
$signTargets = @()
foreach ($candidate in @($appExe, $collectorExe, $setupExe)) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $signTargets += $candidate }
}
$signStatus = @{ signed = $false; reason = "not-run"; mode = "none" }
if ($Debug) {
    $signStatus.reason = "debug-build"
    Write-Host "debug 构建：跳过签名。"
} elseif ($NoSign) {
    $signStatus.reason = "disabled-by-nosign"
    Write-Host "指定 -NoSign：跳过签名。"
} else {
    $statusPath = Join-Path $env:TEMP ("pb-sign-status-{0}.json" -f $PID)
    if (Test-Path -LiteralPath $statusPath) { Remove-Item -LiteralPath $statusPath -Force }
    # 优先 pwsh 7（本仓库脚本按 UTF-8 无 BOM 编写，Windows PowerShell 5.1 可能读坏中文）；
    # 某些环境 pwsh 不在 PATH，按标准安装目录兜底。
    $shell = (Get-Command pwsh.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $shell) {
        foreach ($candidate in @(
            (Join-Path $env:ProgramFiles "PowerShell\7\pwsh.exe"),
            (Join-Path $env:ProgramFiles "PowerShell\7-preview\pwsh.exe")
        )) {
            if (Test-Path -LiteralPath $candidate) { $shell = $candidate; break }
        }
    }
    if (-not $shell) {
        $shell = (Get-Command powershell.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    }
    if (-not $shell) { throw "找不到 pwsh/powershell，无法调用 scripts\sign.ps1" }
    Write-Host "签名子进程解释器：$shell"

    Write-Host ""
    Write-Host "== 代码签名（scripts\sign.ps1）=="
    & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "sign.ps1") `
        -Files ($signTargets -join ';') -StatusPath $statusPath
    $signExit = $LASTEXITCODE
    if (Test-Path -LiteralPath $statusPath) {
        try {
            $signStatus = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        } catch {
            Write-Warning "解析签名状态失败：$_"
        }
        Remove-Item -LiteralPath $statusPath -Force -ErrorAction SilentlyContinue
    }
    if ($signExit -eq 1) { throw "代码签名失败（见上方 sign.ps1 输出）" }
    if ($signExit -ne 0) { throw "scripts\sign.ps1 异常退出（exit=$signExit）" }
    if (-not $signStatus.signed) {
        Write-Host ("签名状态：signed=false（{0}）" -f $signStatus.reason)
    } else {
        Write-Host "签名状态：signed=true"
        Write-Host "注意：这是构建后签名——setup.exe 本身已签名，但安装包内的 app/collector 副本" 
        Write-Host "      仍是构建时未签名的版本；如需整链签名，证书接入 bundle.windows.signCommand。"
    }
}

# ---------------------------------------------------------------- SHA256SUMS + build-info
$git = Get-GitInfo
$builtAtUtc = (Get-Date).ToUniversalTime()
$builtAtLocal = (Get-Date)

$artifacts = @()
if (Test-Path -LiteralPath $appExe) {
    $artifacts += [ordered]@{ name = "peripheral-battery.exe"; path = $appExe; role = "app" }
}
if (Test-Path -LiteralPath $collectorExe) {
    $artifacts += [ordered]@{ name = "collector.exe"; path = $collectorExe; role = "collector-sidecar" }
}
if ($setupExe -and (Test-Path -LiteralPath $setupExe)) {
    $artifacts += [ordered]@{ name = (Split-Path -Leaf $setupExe); path = $setupExe; role = "nsis-installer" }
}

$hashedArtifacts = @()
$sumLines = @()
foreach ($artifact in $artifacts) {
    $hash = Get-Sha256Hex -Path $artifact.path
    $size = (Get-Item -LiteralPath $artifact.path).Length
    $hashedArtifacts += [ordered]@{
        name   = $artifact.name
        role   = $artifact.role
        path   = $artifact.path
        sha256 = $hash
        size   = $size
    }
    $sumLines += ("{0}  {1}" -f $hash, $artifact.name)
}

$artifactDir = if ($setupExe -and -not $NoBundle -and -not $Debug) {
    Split-Path -Parent $setupExe
} else {
    Join-Path $root $outDir
}
$sumsPath = Join-Path $artifactDir "SHA256SUMS.txt"
$buildInfoPath = Join-Path $artifactDir "build-info.json"
Write-Utf8NoBom -Path $sumsPath -Text (($sumLines -join "`n") + "`n")

$buildInfo = [ordered]@{
    schema        = 1
    product       = "PeripheralBattery"
    version       = $version
    git_sha       = $git.sha
    git_short     = $git.short
    git_branch    = $git.branch
    git_dirty     = $git.dirty
    built_at      = $builtAtUtc.ToString("o")
    built_at_local = $builtAtLocal.ToString("yyyy-MM-dd HH:mm:ss zzz")
    profile       = if ($Debug) { "debug" } else { "release" }
    no_bundle     = [bool]$NoBundle
    signed        = [bool]$signStatus.signed
    sign_mode     = $signStatus.mode
    sign_reason   = $signStatus.reason
    artifacts     = $hashedArtifacts
}
Write-Utf8NoBom -Path $buildInfoPath -Text ($buildInfo | ConvertTo-Json -Depth 8)

Write-Host ""
Write-Host "== 发布产物 =="
foreach ($row in $hashedArtifacts) {
    Write-Host ("  {0}`n    sha256 {1}  ({2} bytes)" -f $row.path, $row.sha256, $row.size)
}
Write-Host ("SHA256SUMS : {0}" -f $sumsPath)
Write-Host ("build-info : {0}" -f $buildInfoPath)
