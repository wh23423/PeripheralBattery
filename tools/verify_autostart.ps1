#Requires -Version 7.0
<#
.SYNOPSIS
  F-01 开机静默启动验收（Lead 用；普通开发不需要跑）。

.DESCRIPTION
  验证 v0.2.0 的 F-01「开机静默启动」端到端行为：
    1. 模拟用户在设置里开启「开机启动」：往
       HKCU\Software\Microsoft\Windows\CurrentVersion\Run 写入
       REG_SZ 值 PeripheralBattery = "<exe>" --autostart；
    2. 用 `--autostart` 启动当前构建的 exe，等待 3–5 s，断言进程存活且
       主窗口不可见（面板窗口 class=Tauri Window 的 IsWindowVisible=false；
    3. 再启动一次 `--autostart`，断言单实例逻辑让它直接 exit(0)、机器上
       始终只有 1 个 app 进程（不激活已有窗口）；
    4. 结束测试进程，删除 Run 值并断言删除成功；
    5. 应用侧端到端（场景 4）：隔离数据目录的 app.json 置 autostart=true，
       启动 exe（--hidden），断言应用启动 reconcile 自己写入 Run 值
       （引号路径 + --autostart）；再改为 autostart=false（并预置
       StartupApproved 记录）启动，断言应用自己删除 Run 与
       StartupApproved 两值。目标 exe 版本 < 0.2.0 时该段 SKIP + WARN；
    6. 脚本结束（含异常/中断）前把 HKCU Run 与
       HKCU\...\Explorer\StartupApproved\Run 的同名值恢复为运行前快照。

  测试使用独立的临时数据目录（%TEMP%\pb-autostart-verify-*，通过
  PB_DATA_DIR 传给被测进程），不会读写真实用户数据；测试进程退出后目录
  自动清理（失败时保留便于排查）。

风险与安全说明：
  - 只写 HKCU（当前用户）两个注册表值，不需要管理员权限，不会弹 UAC；
  - 脚本会启动并强制结束 peripheral-battery.exe 测试进程；
  - 若检测到已有 peripheral-battery 进程在运行，脚本直接 SKIP（exit 0），
    以免干扰你正在使用的托盘应用、并避免单实例断言失真；
  - 恢复逻辑放在 finally 中：正常结束、断言失败、Ctrl+C / 异常都会恢复
    注册表快照；若进程被强杀则可能残留测试值，重跑一次即可恢复快照。
  - 不修改任何其他注册表项；不触碰真实 %APPDATA%\PeripheralBattery 数据。

.PARAMETER ExePath
  被测 exe 路径。省略时依次尝试：
    src-tauri\target\release\peripheral-battery.exe
    src-tauri\target\debug\peripheral-battery.exe
  显式指定但文件不存在时不再回退，直接 SKIP。

.PARAMETER WaitSeconds
  启动后等待窗口/进程稳定的秒数，默认 4（建议 3–5）。

.PARAMETER DryRun
  只做定位/快照/计划打印，不写注册表、不启动进程，退出码 0。
  适合语法或环境自检（如 `pwsh -File tools\verify_autostart.ps1 -DryRun`）。

.PARAMETER JsonPath
  可选：把逐项断言结果写成 UTF-8 JSON（便于归档到 design/verify/）。

.EXAMPLE
  pwsh -File tools\verify_autostart.ps1
  pwsh -File tools\verify_autostart.ps1 -ExePath src-tauri\target\release\peripheral-battery.exe
  pwsh -File tools\verify_autostart.ps1 -DryRun
  pwsh -File tools\verify_autostart.ps1 -JsonPath design\verify\f01-autostart.json
#>
[CmdletBinding()]
param(
    [string]$ExePath = "",
    [ValidateRange(2, 30)]
    [int]$WaitSeconds = 4,
    [switch]$DryRun,
    [string]$JsonPath = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$approvedKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$valueName = 'PeripheralBattery'
$processName = 'peripheral-battery'

$script:checks = [System.Collections.Generic.List[object]]::new()
$script:failures = [System.Collections.Generic.List[string]]::new()
$script:startedPids = [System.Collections.Generic.List[int]]::new()

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = "")
    $mark = if ($Ok) { 'PASS' } else { 'FAIL' }
    $color = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1} {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
    $script:checks.Add([pscustomobject]@{ name = $Name; ok = $Ok; detail = $Detail })
    if (-not $Ok) { $script:failures.Add("$Name | $Detail") }
}

function Add-Info {
    param([string]$Message)
    Write-Host ("  [INFO] " + $Message) -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- 注册表快照
function Get-PbRegSnapshot {
    param([string]$Key, [string]$Name)
    $item = Get-Item -LiteralPath $Key -ErrorAction SilentlyContinue
    if (-not $item) {
        return [pscustomobject]@{ Key = $Key; Name = $Name; KeyExisted = $false; Existed = $false; Kind = $null; Value = $null }
    }
    if (@($item.GetValueNames()) -notcontains $Name) {
        return [pscustomobject]@{ Key = $Key; Name = $Name; KeyExisted = $true; Existed = $false; Kind = $null; Value = $null }
    }
    return [pscustomobject]@{
        Key        = $Key
        Name       = $Name
        KeyExisted = $true
        Existed    = $true
        Kind       = $item.GetValueKind($Name)
        Value      = $item.GetValue($Name)
    }
}

function Restore-PbRegSnapshot {
    param($Snapshot)
    if ($null -eq $Snapshot) { return }
    try {
        if ($Snapshot.Existed) {
            if (-not (Test-Path -LiteralPath $Snapshot.Key)) { New-Item -Path $Snapshot.Key -Force | Out-Null }
            New-ItemProperty -LiteralPath $Snapshot.Key -Name $Snapshot.Name -PropertyType $Snapshot.Kind -Value $Snapshot.Value -Force | Out-Null
        } else {
            if (Test-Path -LiteralPath $Snapshot.Key) {
                Remove-ItemProperty -LiteralPath $Snapshot.Key -Name $Snapshot.Name -ErrorAction SilentlyContinue
                if (-not $Snapshot.KeyExisted) {
                    $item = Get-Item -LiteralPath $Snapshot.Key -ErrorAction SilentlyContinue
                    if ($item -and @($item.GetValueNames()).Count -eq 0 -and @($item.GetSubKeyNames()).Count -eq 0) {
                        Remove-Item -LiteralPath $Snapshot.Key -Force -ErrorAction SilentlyContinue
                    }
                }
            }
        }
    } catch {
        Write-Warning ("恢复注册表快照失败（{0}\{1}）：{2}" -f $Snapshot.Key, $Snapshot.Name, $_)
    }
}

# ---------------------------------------------------------------- 注册表 / 状态辅助
# 只读查询：某个注册表值当前是否存在（用于等待应用启动后的 reconcile 结果）
function Test-PbRegValue {
    param([string]$Key, [string]$Name)
    $item = Get-Item -LiteralPath $Key -ErrorAction SilentlyContinue
    return [bool]($item -and (@($item.GetValueNames()) -contains $Name))
}

function Get-PbRunValue {
    $item = Get-Item -LiteralPath $runKey -ErrorAction SilentlyContinue
    if (-not $item -or (@($item.GetValueNames()) -notcontains $valueName)) { return $null }
    return [string]$item.GetValue($valueName)
}

# 轮询 HKCU Run\PeripheralBattery，直到「存在/不存在」与期望一致或超时（应用启动后异步 reconcile）
function Wait-PbRunValueState {
    param([bool]$Exists, [int]$TimeoutSeconds = 10)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        $present = Test-PbRegValue -Key $runKey -Name $valueName
        if ($present -eq $Exists) {
            return [pscustomobject]@{ Ok = $true; Present = $present; Value = $(if ($present) { Get-PbRunValue } else { $null }) }
        }
        if ((Get-Date) -ge $deadline) {
            return [pscustomobject]@{ Ok = $false; Present = $present; Value = $(if ($present) { Get-PbRunValue } else { $null }) }
        }
        Start-Sleep -Milliseconds 300
    }
}

# 改写隔离数据目录里的 app.json（场景 4 用；不触碰真实 %APPDATA%）
function Set-PbAppJson {
    param([bool]$Autostart)
    $obj = [ordered]@{
        schema      = 2
        pinned      = $false
        opacity     = 60
        autostart   = $Autostart
        notify_low  = $true
        notify_full = $true
    }
    [System.IO.File]::WriteAllText((Join-Path $tmpDataDir 'app.json'), ($obj | ConvertTo-Json -Compress), $utf8NoBom)
}

# ---------------------------------------------------------------- 进程 / 窗口
function Get-PbProcesses {
    return @(Get-Process -Name $processName -ErrorAction SilentlyContinue)
}

function Stop-PbProcess {
    param($Process)
    if ($null -eq $Process) { return }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction Stop
            $Process.WaitForExit(5000) | Out-Null
        }
    } catch { }
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and (Get-PbProcesses).Count -gt 0) { Start-Sleep -Milliseconds 200 }
}

$hasIsWindowVisible = $false
try {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class PbWin32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
}
'@
    $hasIsWindowVisible = $true
} catch {
    Write-Warning "Win32 P/Invoke 不可用，窗口可见性仅按 MainWindowHandle 判断：$_"
}

# 返回某进程的全部顶层窗口（含隐藏窗口）。用来精确断言 Tauri 面板的可见性；
# 不能用 Process.MainWindowHandle —— Tao 的 "Tao Thread Event Target" 消息窗口
# 本身是可见顶层窗口，会被 .NET 当成“主窗口”，造成静默启动的假阳性。
function Get-PbTopLevelWindows {
    param([int]$TargetPid)
    $result = New-Object System.Collections.ArrayList
    $callback = [PbWin32+EnumWindowsProc]{
        param($hwnd, $lparam)
        $wpid = [uint32]0
        [PbWin32]::GetWindowThreadProcessId($hwnd, [ref]$wpid) | Out-Null
        if ([int]$wpid -eq $TargetPid) {
            $cls = New-Object System.Text.StringBuilder 256
            [PbWin32]::GetClassName($hwnd, $cls, 256) | Out-Null
            $txt = New-Object System.Text.StringBuilder 256
            [PbWin32]::GetWindowText($hwnd, $txt, 256) | Out-Null
            $null = $result.Add([pscustomobject]@{
                Handle  = $hwnd
                Visible = [PbWin32]::IsWindowVisible($hwnd)
                Class   = $cls.ToString()
                Title   = $txt.ToString()
            })
        }
        return $true
    }
    [PbWin32]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
    return $result
}

# ---------------------------------------------------------------- 定位 exe
Write-Host "== F-01 开机静默启动验收 ==" -ForegroundColor Cyan
$exe = $null
$requested = $ExePath
$candidates = @()
if ($requested) {
    $candidates += $requested
} else {
    $candidates += (Join-Path $root 'src-tauri\target\release\peripheral-battery.exe')
    $candidates += (Join-Path $root 'src-tauri\target\debug\peripheral-battery.exe')
}
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $exe = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if (-not $exe) {
    Write-Host ''
    Write-Host "SKIP 找不到已构建的 peripheral-battery.exe（依次尝试 release / debug）。" -ForegroundColor Yellow
    Write-Host "     请先构建：pwsh -File scripts\build.ps1 -Debug -NoBundle（或 scripts\build.ps1），"
    Write-Host "     或用 -ExePath 指定 exe。"
    exit 0
}

$exeItem = Get-Item -LiteralPath $exe
$exeVersion = $exeItem.VersionInfo.FileVersion
$exeSha = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host ("exe      : {0}" -f $exe)
Write-Host ("version  : {0}" -f $(if ($exeVersion) { $exeVersion } else { '（无 FileVersion）' }))
# 版本门槛：--autostart 与应用侧 autostart reconcile 都是 v0.2.0 新增能力；旧版 exe 无法验证场景 4。
$exeVerNum = $null
if ($exeVersion) {
    try { $exeVerNum = [version](($exeVersion -split '[^0-9.]')[0]) } catch { $exeVerNum = $null }
}
$appSideSupported = -not ($exeVerNum -and $exeVerNum -lt [version]'0.2.0')
if (-not ($exeVerNum -and $exeVerNum -ge [version]'0.2.0')) {
    Write-Host ("WARN 该 exe 版本 {0} 不是 v0.2.0，可能不含 --autostart（F-01）实现；请先重新构建，否则真实执行会 FAIL。" -f $exeVersion) -ForegroundColor Yellow
}
if (-not $appSideSupported) {
    Write-Host ("WARN 目标 exe 版本 {0} < 0.2.0，不含 F-01 应用侧逻辑；场景 4（应用侧写/删注册表）会 SKIP。" -f $exeVersion) -ForegroundColor Yellow
}
Write-Host ("sha256   : {0}" -f $exeSha)
Write-Host ("size     : {0:N1} MiB" -f ($exeItem.Length / 1MB))

# 现场快照：脚本结束/异常时恢复
$runSnap = Get-PbRegSnapshot -Key $runKey -Name $valueName
$approvedSnap = Get-PbRegSnapshot -Key $approvedKey -Name $valueName
Write-Host ("当前 HKCU Run\{0} = {1}" -f $valueName, $(if ($runSnap.Existed) { "'" + $runSnap.Value + "'" } else { '（不存在）' }))
Write-Host ("当前 StartupApproved\Run\{0} = {1}" -f $valueName, $(if ($approvedSnap.Existed) { '存在（脚本只读快照，不改写）' } else { '（不存在）' }))
Write-Host ''

if ($DryRun) {
    Write-Host '-- DryRun：不写注册表、不启动进程 --' -ForegroundColor Yellow
    Write-Host ("  计划：写入 Run 值 '""{0}"" --autostart'" -f $exe)
    Write-Host ("  计划：启动 exe --autostart（独立数据目录），等待 {0}s，断言进程存活 + 主窗口不可见" -f $WaitSeconds)
    Write-Host '  计划：第二次 --autostart，断言单实例 exit(0) 且无第二个进程'
    Write-Host '  计划：结束进程 → 删除 Run 值 → 断言删除成功'
    Write-Host '  计划：场景4 应用侧：app.json autostart=true 启动（--hidden）→ 断言应用写入 Run 值；改 false 再启动 → 断言应用删除 Run + StartupApproved'
    if (-not $appSideSupported) { Write-Host '  （场景4 会 SKIP：目标 exe 版本 < 0.2.0）' -ForegroundColor Yellow }
    Write-Host '  计划：finally 恢复 HKCU Run / StartupApproved\Run 快照'
    Write-Host ''
    if ((Get-PbProcesses).Count -gt 0) {
        Write-Host ("DRYRUN 提示：当前已有 {0} 个 {1} 进程在运行，真实执行时会 SKIP。" -f (Get-PbProcesses).Count, $processName) -ForegroundColor Yellow
    }
    exit 0
}

# ---------------------------------------------------------------- 前置检查
$existing = Get-PbProcesses
if ($existing.Count -gt 0) {
    Write-Host ("SKIP 已有 {0} 个 {1} 进程在运行（pid: {2}）。" -f $existing.Count, $processName, (($existing | ForEach-Object { $_.Id }) -join ', ')) -ForegroundColor Yellow
    Write-Host '     单实例/静默启动场景需要干净环境：请退出正在使用的托盘应用后重跑。'
    exit 0
}

$tmpDataDir = Join-Path $env:TEMP ("pb-autostart-verify-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$keepArtifacts = $false
$runValueWritten = $false
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$envSaved = @{}
foreach ($name in @('PB_DATA_DIR', 'PB_QUIT_AFTER_MS', 'PB_LOG_LEVEL')) {
    $envSaved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$firstProc = $null
$secondProc = $null
$firstAlive = $false
$exitCode = 1

try {
    # 隔离数据目录：不读写真实 %APPDATA%\PeripheralBattery；app.json 里
    # autostart=true 模拟“用户已开启开机启动”，这样启动时 reconcile 不会
    # 把脚本写入的 Run 值当成“用户已关闭”而删掉。
    New-Item -ItemType Directory -Force -Path (Join-Path $tmpDataDir 'logs') | Out-Null
    $appJson = '{"schema":2,"pinned":false,"opacity":60,"autostart":true,"notify_low":true,"notify_full":true}'
    [System.IO.File]::WriteAllText((Join-Path $tmpDataDir 'app.json'), $appJson, $utf8NoBom)

    $env:PB_DATA_DIR = $tmpDataDir
    $env:PB_LOG_LEVEL = 'info'
    $env:PB_QUIT_AFTER_MS = '45000'   # 兜底：脚本异常时 app 最多活 45s，避免永久驻留

    # ------------------------------------------------------------ 场景 1：写入 Run 值（模拟用户开启）
    $runCommand = '"' + $exe + '" --autostart'
    if (-not (Test-Path -LiteralPath $runKey)) { New-Item -Path $runKey -Force | Out-Null }
    New-ItemProperty -LiteralPath $runKey -Name $valueName -PropertyType String -Value $runCommand -Force | Out-Null
    $runValueWritten = $true
    $readBack = $null
    $readBackItem = Get-Item -LiteralPath $runKey -ErrorAction SilentlyContinue
    if ($readBackItem -and (@($readBackItem.GetValueNames()) -contains $valueName)) {
        $readBack = $readBackItem.GetValue($valueName)
    }
    Add-Check 'F-01 场景1：写入 Run 值（模拟用户开启自启动）' ($readBack -eq $runCommand) ("Run\{0} = {1}" -f $valueName, $readBack)

    # ------------------------------------------------------------ 场景 1：--autostart 静默启动
    $launchAt = Get-Date
    $firstProc = Start-Process -FilePath $exe -ArgumentList '--autostart' -PassThru
    $script:startedPids.Add($firstProc.Id)
    Start-Sleep -Seconds $WaitSeconds
    $firstProc.Refresh()
    $firstAlive = -not $firstProc.HasExited
    Add-Check 'F-01 场景1：--autostart 进程存活' $firstAlive ("pid={0} elapsed={1:F1}s" -f $firstProc.Id, ((Get-Date) - $launchAt).TotalSeconds)

    $panelHandle = [IntPtr]::Zero
    $panelVisible = $false
    $panelEvidence = 'n/a'
    if ($firstAlive) {
        if ($hasIsWindowVisible) {
            # 只认应用自己的面板窗口（class=Tauri Window / 标题=外设电量），
            # 忽略 Tao 消息窗口、托盘窗口、IME 等附属窗口。面板尚未创建也视为不可见。
            $panels = @(Get-PbTopLevelWindows -TargetPid $firstProc.Id | Where-Object {
                $_.Class -eq 'Tauri Window' -or $_.Title -like '外设电量*'
            })
            if ($panels.Count -gt 0) {
                $panelHandle = $panels[0].Handle
                $panelVisible = [bool]$panels[0].Visible
                $panelEvidence = ("panel class={0} title={1} visible={2}" -f $panels[0].Class, $panels[0].Title, $panels[0].Visible)
            } else {
                $panelEvidence = 'panel window 未枚举到（视为不可见）'
            }
        } else {
            $panelHandle = $firstProc.MainWindowHandle
            $panelVisible = $panelHandle -ne [IntPtr]::Zero
            $panelEvidence = ("fallback MainWindowHandle={0}" -f $panelHandle)
        }
    }
    Add-Check 'F-01 场景1：主窗口不可见（静默启动）' ($firstAlive -and -not $panelVisible) `
        ("panelHandle={0} {1}" -f $panelHandle, $panelEvidence)

    # 日志断言（info 级）：主进程应在启动早期记录 autostart silent launch
    $logFile = Join-Path $tmpDataDir 'logs\app.log'
    $logText = ''
    $logDeadline = (Get-Date).AddSeconds($WaitSeconds + 3)
    while ((Get-Date) -lt $logDeadline) {
        if (Test-Path -LiteralPath $logFile) {
            $logText = [string](Get-Content -LiteralPath $logFile -Raw -ErrorAction SilentlyContinue)
            if ($logText -match 'autostart silent launch') { break }
        }
        Start-Sleep -Milliseconds 300
    }
    Add-Check 'F-01 场景1：日志记录 autostart silent launch' ($logText -match 'autostart silent launch') ("log={0}" -f $logFile)

    # ------------------------------------------------------------ 场景 2：第二次 --autostart → 单实例 exit(0)
    if ($firstAlive) {
        $secondProc = Start-Process -FilePath $exe -ArgumentList '--autostart' -PassThru
        $script:startedPids.Add($secondProc.Id)
        $secondExited = $secondProc.WaitForExit(10000)
        $secondCode = if ($secondExited -and $secondProc.HasExited) { $secondProc.ExitCode } else { $null }
        Add-Check 'F-01 场景2：第二次 --autostart 自行退出（单实例）' $secondExited ("pid={0} exited={1}" -f $secondProc.Id, $secondExited)
        Add-Check 'F-01 场景2：第二次 --autostart exit=0' ($secondExited -and $secondCode -eq 0) ("exit={0}" -f $secondCode)
        Start-Sleep -Milliseconds 500
        $aliveNow = Get-PbProcesses
        Add-Check 'F-01 场景2：没有第二个 app 进程残留' ($aliveNow.Count -eq 1) ("processes={0} pids={1}" -f $aliveNow.Count, (($aliveNow | ForEach-Object { $_.Id }) -join ','))
    } else {
        Add-Check 'F-01 场景2：第二次 --autostart 自行退出（单实例）' $false '跳过：第一实例未存活'
        Add-Check 'F-01 场景2：第二次 --autostart exit=0' $false '跳过：第一实例未存活'
        Add-Check 'F-01 场景2：没有第二个 app 进程残留' $false '跳过：第一实例未存活'
    }

    # ------------------------------------------------------------ 场景 3：清理进程 + 删除 Run 值
    #   脚本侧的清理路径（模拟卸载/手动清理）；应用自己按设置写/删见场景 4。
    $killOk = $true
    foreach ($procId in @($script:startedPids)) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p -and -not $p.HasExited) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                $p.WaitForExit(5000) | Out-Null
            } catch {
                $killOk = $false
                Add-Info ("结束进程 {0} 失败：{1}" -f $procId, $_)
            }
        }
    }
    $killDeadline = (Get-Date).AddSeconds(8)
    while ((Get-PbProcesses).Count -gt 0 -and (Get-Date) -lt $killDeadline) {
        Start-Sleep -Milliseconds 250
    }
    $remaining = Get-PbProcesses
    Add-Check 'F-01 场景3：测试进程已全部结束' ($killOk -and $remaining.Count -eq 0) ("remaining={0}" -f $remaining.Count)

    Remove-ItemProperty -LiteralPath $runKey -Name $valueName -ErrorAction SilentlyContinue
    $runValueWritten = $false
    $afterDelete = Get-Item -LiteralPath $runKey -ErrorAction SilentlyContinue
    $stillThere = $afterDelete -and (@($afterDelete.GetValueNames()) -contains $valueName)
    Add-Check 'F-01 场景3：删除 Run 值后断言不存在' (-not $stillThere) ("Run\{0} 已删除" -f $valueName)
    Add-Info ("StartupApproved\Run\{0} 当前状态：{1}" -f $valueName, $(if ($approvedSnap.Existed) { '运行前已存在（结束时恢复快照）' } else { '不存在（脚本未写入）' }))

    # ------------------------------------------------------------ 场景 4：应用侧注册表写/删（真实设置路径）
    #   场景 1–3 是「脚本写、脚本删」的注册表级验收；本段验证应用自己按
    #   app.json.autostart 做启动 reconcile（写/删），并覆盖「关闭设置后两值清理」。
    if (-not $appSideSupported) {
        Write-Host ("WARN 目标 exe 版本 {0} < 0.2.0，不含 F-01 应用侧逻辑；SKIP 场景 4（应用侧写/删注册表）。" -f $exeVersion) -ForegroundColor Yellow
        Add-Info '场景 4 SKIP：旧版 exe'
    } else {
        # 4a) autostart=true → 启动应用 → 应用启动 reconcile 应写入 Run 值
        Set-PbAppJson -Autostart $true
        $writeProc = Start-Process -FilePath $exe -ArgumentList '--hidden' -PassThru
        $script:startedPids.Add($writeProc.Id)
        $writeWait = Wait-PbRunValueState -Exists $true -TimeoutSeconds 10
        $writeDetail = "value={0} proc_exited={1}" -f $(if ($null -eq $writeWait.Value) { '（不存在）' } else { $writeWait.Value }), $writeProc.HasExited
        Add-Check 'F-01 场景4：应用按设置写入 HKCU Run 值' $writeWait.Ok $writeDetail
        $writtenValue = [string]$writeWait.Value
        Add-Check 'F-01 场景4：Run 值以引号路径开头且含 --autostart' ($writeWait.Ok -and ($writtenValue -match '^\s*"') -and ($writtenValue -match '--autostart')) ("value={0}" -f $writtenValue)
        $parsedPath = if ($writtenValue -match '^\s*"([^"]+)"\s+--autostart\s*$') { $Matches[1] } else { $null }
        $pathMatch = $false
        if ($parsedPath) {
            try {
                $pathMatch = [string]::Equals([System.IO.Path]::GetFullPath($parsedPath), [System.IO.Path]::GetFullPath($exe), 'OrdinalIgnoreCase')
            } catch {
                $pathMatch = $false
            }
        }
        Add-Check 'F-01 场景4：Run 值的路径指向被测 exe' $pathMatch ("run_path={0} exe={1}" -f $parsedPath, $exe)
        Stop-PbProcess $writeProc
        Start-Sleep -Milliseconds 300

        # 4b) autostart=false + 预置 StartupApproved 记录 → 启动应用 → 应用启动
        #     reconcile（intent=false 且 Run 存在）应删除 Run 与 StartupApproved 两值。
        if (-not (Test-Path -LiteralPath $approvedKey)) { New-Item -Path $approvedKey -Force | Out-Null }
        $approvedTestBytes = [byte[]](0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
        New-ItemProperty -LiteralPath $approvedKey -Name $valueName -PropertyType Binary -Value $approvedTestBytes -Force | Out-Null
        $runBeforeDelete = Get-PbRunValue
        Set-PbAppJson -Autostart $false
        $deleteProc = Start-Process -FilePath $exe -ArgumentList '--hidden' -PassThru
        $script:startedPids.Add($deleteProc.Id)
        $deleteWait = Wait-PbRunValueState -Exists $false -TimeoutSeconds 10
        $approvedGone = $false
        if ($deleteWait.Ok) {
            $approvedDeadline = (Get-Date).AddSeconds(3)
            while ((Get-Date) -lt $approvedDeadline -and (Test-PbRegValue -Key $approvedKey -Name $valueName)) {
                Start-Sleep -Milliseconds 250
            }
            $approvedGone = -not (Test-PbRegValue -Key $approvedKey -Name $valueName)
        }
        Add-Check 'F-01 场景4：关闭设置后应用删除 Run 值' ($deleteWait.Ok -and ($null -ne $runBeforeDelete)) ("before={0} now={1}" -f $runBeforeDelete, $(if ($null -eq $deleteWait.Value) { '（不存在）' } else { $deleteWait.Value }))
        Add-Check 'F-01 场景4：关闭设置后应用同时删除 StartupApproved 值' $approvedGone 'StartupApproved\Run'
        Stop-PbProcess $deleteProc
    }

    $exitCode = if ($script:failures.Count -eq 0) { 0 } else { 1 }
    if ($exitCode -ne 0) { $keepArtifacts = $true }
} catch {
    Write-Host ''
    Write-Host ("执行异常：{0}" -f $_) -ForegroundColor Red
    $script:failures.Add("执行异常: $_")
    $exitCode = 1
    $keepArtifacts = $true
} finally {
    # 恢复环境变量
    foreach ($name in $envSaved.Keys) {
        $saved = $envSaved[$name]
        if ($null -eq $saved) {
            Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path ("Env:" + $name) -Value $saved -ErrorAction SilentlyContinue
        }
    }
    # 结束脚本启动的进程（按 pid 精确结束，不动无关进程）
    foreach ($procId in @($script:startedPids)) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p -and -not $p.HasExited) {
            try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch { }
        }
    }
    # 恢复注册表快照（含脚本写入的测试值）
    Restore-PbRegSnapshot $runSnap
    Restore-PbRegSnapshot $approvedSnap
    if ($runValueWritten) {
        Remove-ItemProperty -LiteralPath $runKey -Name $valueName -ErrorAction SilentlyContinue
    }
    if ($tmpDataDir -and (Test-Path -LiteralPath $tmpDataDir)) {
        if ($keepArtifacts) {
            Write-Warning ("测试数据目录保留：{0}" -f $tmpDataDir)
        } else {
            Remove-Item -LiteralPath $tmpDataDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# ---------------------------------------------------------------- 汇总
Write-Host ''
Write-Host '== 结果 ==' -ForegroundColor Cyan
Write-Host ("  检查项：{0} 通过 / {1} 失败" -f ($script:checks.Count - $script:failures.Count), $script:failures.Count)
foreach ($f in $script:failures) { Write-Host ("  FAIL: " + $f) -ForegroundColor Red }
Write-Host ("  总体：{0}" -f $(if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' })) -ForegroundColor $(if ($exitCode -eq 0) { 'Green' } else { 'Red' })

if ($JsonPath) {
    $result = [ordered]@{
        timestamp    = (Get-Date).ToString('o')
        exe          = $exe
        exe_sha256   = $exeSha
        exe_version  = $exeVersion
        wait_seconds = $WaitSeconds
        passed       = ($exitCode -eq 0)
        checks       = $script:checks
    }
    $dir = Split-Path -Parent $JsonPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($JsonPath, ($result | ConvertTo-Json -Depth 6), $utf8NoBom)
    Write-Host ("  JSON：{0}" -f $JsonPath)
}

exit $exitCode
