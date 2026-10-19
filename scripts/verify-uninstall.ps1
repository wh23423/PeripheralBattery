#Requires -Version 7.0
<#
.SYNOPSIS
  NSIS 安装/卸载自动化验收：静默安装 → 写入数据 → 静默卸载 → 断言残留状态。

.DESCRIPTION
  review P1-01 / P2-02 的验收脚本（Lead 用；普通开发不需要跑）。默认把应用
  静默安装到 %TEMP%\PB-uninstall-verify-* 的自定义目录（不碰默认安装路径），
  然后：
    1. 确认 INSTDIR、主程序、collector sidecar、卸载器、注册表安装位置；
    2. 在 INSTDIR 里伪造 0.1.0 风格的用户数据（app.json/config.json/logs）；
    3. 静默卸载（uninstall.exe /S）；
    4. 断言 INSTDIR 递归删除、HKCU 注册表（当前 publisher + 0.1.0 vendor）无残留、
       WebView2 profile（%LOCALAPPDATA%\com.peripheralbattery.app）已清理；
    5. 默认不删除真实用户数据（%APPDATA%\PeripheralBattery），只断言“保留”；
       加 -DeleteAppData -ConfirmDeleteAppData 才测试 /DELETEAPPDATA 删除路径
       （会在真实数据目录写一个 sentinel，卸载后断言其消失）。

  安全保护（默认拒绝，需 -Force 才继续）：
    - 已有 PeripheralBattery 安装记录（HKCU Uninstall 键）；
    - 默认安装目录 %LOCALAPPDATA%\PeripheralBattery 已存在；
    - peripheral-battery / collector 进程在运行。
  因为测试安装会覆盖同名注册表项并删除 WebView2 profile，请勿在“正在使用本应用”
  的机器上直接跑；开发机建议先卸载旧版或在干净虚拟机里跑。

.PARAMETER SetupPath
  NSIS 安装包路径；默认取 src-tauri\target\release\bundle\nsis\*-setup.exe 最新一个。

.PARAMETER TestDir
  测试安装目录；默认 %TEMP%\PB-uninstall-verify-<时间戳>。不能含空格（NSIS /D= 无引号）。

.PARAMETER DeleteAppData
  测试 /DELETEAPPDATA 路径：卸载时请求删除用户数据。

.PARAMETER ConfirmDeleteAppData
  显式确认会删除真实 %APPDATA%\PeripheralBattery 数据（与 -DeleteAppData 搭配）。

.PARAMETER JsonPath
  可选：把逐项断言结果写成 JSON。

.PARAMETER KeepArtifacts
  失败后保留测试目录便于排查（默认尽力清理）。

.PARAMETER Force
  跳过“已有安装/进程”安全检查（仅在你确认可覆盖本机注册表时使用）。

.EXAMPLE
  pwsh -File scripts\verify-uninstall.ps1
  pwsh -File scripts\verify-uninstall.ps1 -JsonPath $env:TEMP\pb-uninstall.json
  pwsh -File scripts\verify-uninstall.ps1 -DeleteAppData -ConfirmDeleteAppData
#>
[CmdletBinding()]
param(
    [string]$SetupPath = "",
    [string]$TestDir = "",
    [string]$JsonPath = "",
    [switch]$DeleteAppData,
    [switch]$ConfirmDeleteAppData,
    [switch]$KeepArtifacts,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$checks = [System.Collections.Generic.List[object]]::new()
function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = "")
    $script:checks.Add([pscustomobject]@{ name = $Name; ok = $Ok; detail = $Detail })
    $mark = if ($Ok) { "PASS" } else { "FAIL" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1} {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

function Test-RegKey {
    param([string]$Path)
    return Test-Path -LiteralPath $Path
}

$regPublisher = "HKCU:\Software\wh23423\PeripheralBattery"
$regPublisherRoot = "HKCU:\Software\wh23423"
$regLegacy = "HKCU:\Software\peripheralbattery"
$regLegacyProduct = "HKCU:\Software\peripheralbattery\PeripheralBattery"
$regUninstall = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PeripheralBattery"
$dataDir = Join-Path $env:APPDATA "PeripheralBattery"
$legacyDataDir = Join-Path $env:LOCALAPPDATA "PeripheralBattery"
$webviewProfile = Join-Path $env:LOCALAPPDATA "com.peripheralbattery.app"
$defaultInstallDir = Join-Path $env:LOCALAPPDATA "PeripheralBattery"

if ($DeleteAppData -and -not $ConfirmDeleteAppData) {
    throw "测试 /DELETEAPPDATA 会删除真实用户数据（$dataDir）；请同时加 -ConfirmDeleteAppData 明确确认。"
}

# ---------------------------------------------------------------- 定位安装包
if (-not $SetupPath) {
    $candidate = Get-ChildItem -Path "src-tauri\target\release\bundle\nsis" -Filter "*-setup.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $candidate) {
        throw "找不到 NSIS 安装包（src-tauri\target\release\bundle\nsis\*-setup.exe）；请先运行 scripts\build.ps1 -OnlyBundle？或手动指定 -SetupPath。"
    }
    $SetupPath = $candidate.FullName
}
$SetupPath = (Resolve-Path -LiteralPath $SetupPath).Path
$setupHash = (Get-FileHash -LiteralPath $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "安装包: $SetupPath"
Write-Host "SHA256: $setupHash"
Write-Host ""

# ---------------------------------------------------------------- 安全检查
Write-Host "== 安全检查 =="
$running = @(Get-Process -Name "peripheral-battery", "collector" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    if (-not $Force) { throw "peripheral-battery/collector 进程正在运行（$($running.Count) 个）；请先退出，或加 -Force。" }
    Write-Warning "有 $($running.Count) 个相关进程在运行（-Force 继续）。"
}
if ((Test-RegKey $regUninstall) -or (Test-RegKey $regPublisher) -or (Test-RegKey $regLegacyProduct)) {
    if (-not $Force) {
        throw "检测到已存在的安装记录（$regUninstall / $regPublisher / $regLegacyProduct）；测试安装会覆盖并删除它。请先卸载旧版，或加 -Force。"
    }
    Write-Warning "检测到已有安装记录，将覆盖（-Force）。"
}
if (Test-Path -LiteralPath $defaultInstallDir) {
    if (-not $Force) {
        throw "默认安装目录已存在：$defaultInstallDir；测试会删除该目录里的旧数据/程序。请先卸载，或加 -Force。"
    }
    Write-Warning "默认安装目录已存在（-Force 继续）：$defaultInstallDir"
}
Write-Host ""

# ---------------------------------------------------------------- 测试目录
if (-not $TestDir) {
    $TestDir = Join-Path $env:TEMP ("PB-uninstall-verify-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}
if ($TestDir -match '\s') { throw "测试目录不能含空格（NSIS /D= 参数不支持引号）：$TestDir" }
if (Test-Path -LiteralPath $TestDir) { Remove-Item -LiteralPath $TestDir -Recurse -Force }
$dataDirExistedBefore = Test-Path -LiteralPath $dataDir
$webviewProfileExistedBefore = Test-Path -LiteralPath $webviewProfile
$sentinel = Join-Path $dataDir ".verify-uninstall-sentinel"
if ($DeleteAppData) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    Set-Content -LiteralPath $sentinel -Value "verify-uninstall $PID $(Get-Date -Format o)" -Encoding utf8NoBOM
}

$installed = $false
$uninstalled = $false
$failed = $false
try {
    # ------------------------------------------------------------ 静默安装
    Write-Host "== 1/4 静默安装（/S /NS /D=$TestDir）=="
    $install = Start-Process -FilePath $SetupPath -ArgumentList @("/S", "/NS", "/D=$TestDir") -Wait -PassThru
    Add-Check "安装退出码 = 0" ($install.ExitCode -eq 0) "exit=$($install.ExitCode)"
    $installed = Test-Path -LiteralPath (Join-Path $TestDir "peripheral-battery.exe")

    Write-Host "== 2/4 安装结果断言 =="
    Add-Check "INSTDIR 存在" (Test-Path -LiteralPath $TestDir) $TestDir
    Add-Check "主程序 peripheral-battery.exe 已安装" (Test-Path -LiteralPath (Join-Path $TestDir "peripheral-battery.exe"))
    Add-Check "collector sidecar 已安装" (Test-Path -LiteralPath (Join-Path $TestDir "collector\collector.exe"))
    Add-Check "卸载器 uninstall.exe 已生成" (Test-Path -LiteralPath (Join-Path $TestDir "uninstall.exe"))

    $recordedLocation = ""
    if (Test-RegKey $regPublisher) {
        $recordedLocation = [string](Get-Item -LiteralPath $regPublisher).GetValue("")
    }
    Add-Check "注册表记录 INSTDIR（$regPublisher）" ($recordedLocation -eq $TestDir) "value=$recordedLocation"
    Add-Check "未安装 0.1.0 旧 vendor 键（$regLegacyProduct）" (-not (Test-RegKey $regLegacyProduct))
    Add-Check "Add/Remove Programs 项存在" (Test-RegKey $regUninstall)

    # 伪造 0.1.0 风格的用户数据，验证 RMDir /r（旧版默认路径=安装目录）
    Set-Content -LiteralPath (Join-Path $TestDir "app.json") -Value '{"pinned":false}' -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $TestDir "config.json") -Value '{"poll":{}}' -Encoding utf8NoBOM
    New-Item -ItemType Directory -Force -Path (Join-Path $TestDir "logs") | Out-Null
    Set-Content -LiteralPath (Join-Path $TestDir "logs\app.log") -Value "old data" -Encoding utf8NoBOM

    # ------------------------------------------------------------ 静默卸载
    Write-Host "== 3/4 静默卸载（/S $(if ($DeleteAppData) { '/DELETEAPPDATA' } else { '（保留用户数据）' })）=="
    $uninstallArgs = @("/S")
    if ($DeleteAppData) { $uninstallArgs += "/DELETEAPPDATA" }
    $un = Start-Process -FilePath (Join-Path $TestDir "uninstall.exe") -ArgumentList $uninstallArgs -Wait -PassThru
    Add-Check "卸载退出码 = 0" ($un.ExitCode -eq 0) "exit=$($un.ExitCode)"
    $uninstalled = $true

    $deadline = (Get-Date).AddSeconds(20)
    while ((Test-Path -LiteralPath $TestDir) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }

    # ------------------------------------------------------------ 残留断言
    Write-Host "== 4/4 卸载残留断言 =="
    Add-Check "INSTDIR 已递归删除（RMDir /r）" (-not (Test-Path -LiteralPath $TestDir)) $TestDir
    Add-Check "HKCU publisher 安装位置键已删除（$regPublisher）" (-not (Test-RegKey $regPublisher))
    Add-Check "HKCU publisher 根键已删除（$regPublisherRoot）" (-not (Test-RegKey $regPublisherRoot))
    Add-Check "HKCU 0.1.0 vendor 键已删除（$regLegacy）" (-not (Test-RegKey $regLegacy))
    Add-Check "Add/Remove Programs 项已删除（$regUninstall）" (-not (Test-RegKey $regUninstall))
    Add-Check "WebView2 profile 已清理（$webviewProfile）" (-not (Test-Path -LiteralPath $webviewProfile)) `
        ("卸载前存在={0}" -f $webviewProfileExistedBefore)

    if ($DeleteAppData) {
        Add-Check "用户数据已按 /DELETEAPPDATA 删除（sentinel 消失）" (-not (Test-Path -LiteralPath $sentinel))
        if (-not $dataDirExistedBefore) {
            Add-Check "用户数据目录（测试前不存在）未残留" (-not (Test-Path -LiteralPath $dataDir))
        }
        if (Test-Path -LiteralPath $legacyDataDir) {
            Write-Host "  [INFO] 0.1.0 旧数据目录仍存在（自定义安装目录场景不属默认路径残留）：$legacyDataDir" -ForegroundColor Yellow
        }
    } else {
        if ($dataDirExistedBefore) {
            Add-Check "未请求删除时保留用户数据目录（$dataDir）" (Test-Path -LiteralPath $dataDir)
        } else {
            Add-Check "未请求删除时不会创建用户数据目录" (-not (Test-Path -LiteralPath $dataDir))
        }
    }
} catch {
    Write-Host ""
    Write-Host "执行异常：$_" -ForegroundColor Red
    $failed = $true
} finally {
    # 尽力清理半成品（避免把测试安装留在机器上）
    if ($installed -and -not $uninstalled -and (Test-Path -LiteralPath (Join-Path $TestDir "uninstall.exe"))) {
        Write-Warning "安装成功但卸载未完成，尝试抢救性卸载……"
        try {
            $un = Start-Process -FilePath (Join-Path $TestDir "uninstall.exe") -ArgumentList @("/S") -Wait -PassThru
        } catch {
            Write-Warning "抢救性卸载失败：$_"
        }
    }
    if ((-not $KeepArtifacts) -and (Test-Path -LiteralPath $TestDir)) {
        Remove-Item -LiteralPath $TestDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ((-not $DeleteAppData) -and (Test-Path -LiteralPath $sentinel)) {
        Remove-Item -LiteralPath $sentinel -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- 汇总
$failedChecks = @($checks | Where-Object { -not $_.ok })
$passed = ($failedChecks.Count -eq 0) -and (-not $failed)
Write-Host ""
Write-Host "== 结果 ==" -ForegroundColor Cyan
Write-Host ("  检查项：{0} 通过 / {1} 失败" -f ($checks.Count - $failedChecks.Count), $failedChecks.Count)
if ($failedChecks.Count -gt 0) {
    $failedChecks | ForEach-Object { Write-Host ("  FAIL: {0} {1}" -f $_.name, $_.detail) -ForegroundColor Red }
}
Write-Host ("  总体：{0}" -f $(if ($passed) { "PASS" } else { "FAIL" })) -ForegroundColor $(if ($passed) { "Green" } else { "Red" })

if ($JsonPath) {
    $result = [ordered]@{
        timestamp            = (Get-Date).ToString("o")
        setup                = $SetupPath
        setup_sha256         = $setupHash
        test_dir             = $TestDir
        delete_app_data      = [bool]$DeleteAppData
        passed               = $passed
        checks               = $checks
    }
    $dir = Split-Path -Parent $JsonPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($JsonPath, ($result | ConvertTo-Json -Depth 8), $encoding)
    Write-Host ("  JSON：{0}" -f $JsonPath)
}

exit $(if ($passed) { 0 } else { 1 })
