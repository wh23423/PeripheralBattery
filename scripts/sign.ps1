<#
.SYNOPSIS
  对 PeripheralBattery 发布产物做 Authenticode 签名（无证书时安全跳过）。

.DESCRIPTION
  review P1-03 要求 setup.exe / app.exe / collector.exe 可签名并带时间戳。
  本仓库不内置证书；配置以下任一环境变量后由 scripts\build.ps1 自动调用本脚本：

    PB_SIGN_THUMBPRINT       证书指纹（证书须在 Cert:\CurrentUser\My / LocalMachine\My）
    PB_SIGN_CERT_PATH        PFX 证书文件路径（与 PB_SIGN_CERT_PASSWORD 搭配）
    PB_SIGN_CERT_PASSWORD    PFX 密码
    PB_SIGN_TIMESTAMP_URL    时间戳服务（默认 http://timestamp.digicert.com）

  未配置任何证书时打印“未配置证书，跳过签名”，退出码 0，并把 signed:false
  写入 -StatusPath（供 build-info.json 记录）。配置了证书但签名失败时退出码 1，
  由 build.ps1 终止发布，避免“以为签了其实没签”。

  ⚠ PFX 密码通过 signtool /p 参数传递，进程命令行在签名瞬间可见；发布机上请用
  专用证书/专用账户，并在签名后清理环境变量。

.PARAMETER Files
  要签名的文件（一个或多个）。

.PARAMETER StatusPath
  可选：签名状态 JSON 输出路径（build.ps1 用）。

.EXAMPLE
  pwsh -File scripts\sign.ps1 -Files .\dist\collector\collector.exe -StatusPath $env:TEMP\sign.json

.NOTES
  退出码：0 = 已签名或未配置证书跳过；1 = 配置了证书但签名/校验失败。
#>
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Files,
    [string]$StatusPath = "",
    [string]$TimestampUrl = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$Thumbprint = ""
)

$ErrorActionPreference = "Stop"

# 兼容 `pwsh -File sign.ps1 -Files "a;b;c"` 的调用方式（-File 模式下数组参数只绑定
# 第一个 token，因此 build.ps1 用分号分隔传参，这里再拆开）。
$fileList = @()
foreach ($item in $Files) {
    if ($null -eq $item) { continue }
    if ($item -match ';') {
        $fileList += @($item -split ';')
    } else {
        $fileList += $item
    }
}
$fileList = @($fileList | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($fileList.Count -eq 0) {
    throw "没有待签名文件（-Files 为空）"
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Write-StatusJson {
    param([hashtable]$Status)
    if (-not $StatusPath) { return }
    try {
        Write-Utf8NoBom -Path $StatusPath -Text ($Status | ConvertTo-Json -Depth 6)
    } catch {
        Write-Warning "无法写入签名状态文件 $StatusPath : $_"
    }
}

function Fail-Signing {
    param([string]$Reason, [hashtable]$Status)
    $Status.signed = $false
    $Status.reason = $Reason
    Write-StatusJson -Status $Status
    Write-Host "签名失败：$Reason" -ForegroundColor Red
    exit 1
}

# 环境变量兜底
if (-not $PfxPath) { $PfxPath = $env:PB_SIGN_CERT_PATH }
if (-not $PfxPassword) { $PfxPassword = $env:PB_SIGN_CERT_PASSWORD }
if (-not $Thumbprint) { $Thumbprint = $env:PB_SIGN_THUMBPRINT }
if (-not $TimestampUrl) { $TimestampUrl = $env:PB_SIGN_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = "http://timestamp.digicert.com" }

$status = @{
    signed      = $false
    mode        = "none"
    reason      = ""
    signtool    = ""
    timestamp   = $TimestampUrl
    signed_at   = (Get-Date).ToUniversalTime().ToString("o")
    files       = @()
}

# ---------------------------------------------------------------- 未配置证书
if (-not $Thumbprint -and -not $PfxPath) {
    $status.reason = "no-certificate-configured"
    Write-StatusJson -Status $status
    Write-Host "未配置代码签名证书（PB_SIGN_CERT_PATH + PB_SIGN_CERT_PASSWORD 或 PB_SIGN_THUMBPRINT）。"
    Write-Host "跳过签名；发布产物为 NotSigned（build-info.json 会记录 signed:false）。"
    Write-Host "配置方式见 README「发布与签名」一节。"
    exit 0
}

if ($Thumbprint) {
    $status.mode = "thumbprint"
} else {
    $status.mode = "pfx"
}

# ---------------------------------------------------------------- 前置校验
if ($status.mode -eq "thumbprint") {
    $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Thumbprint } |
        Select-Object -First 1
    if (-not $cert) {
        Fail-Signing -Reason "证书指纹 $Thumbprint 不在当前用户/本机证书存储中" -Status $status
    }
    Write-Host "使用证书：$($cert.Subject)（$Thumbprint）"
} else {
    if (-not (Test-Path -LiteralPath $PfxPath)) {
        Fail-Signing -Reason "PFX 证书文件不存在：$PfxPath" -Status $status
    }
    Write-Host "使用 PFX 证书：$PfxPath"
}

# signtool 定位：PATH → Windows SDK 最新 x64
$signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $signtool) {
    $sdkRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path -LiteralPath $sdkRoot) {
        $signtool = Get-ChildItem -LiteralPath $sdkRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
    }
}
if (-not $signtool) {
    Fail-Signing -Reason "找不到 signtool.exe（请安装 Windows 10/11 SDK，或把 signtool 加入 PATH）" -Status $status
}
$status.signtool = $signtool
Write-Host "signtool：$signtool"

# ---------------------------------------------------------------- 逐个签名
$results = @()
foreach ($file in $fileList) {
    if (-not (Test-Path -LiteralPath $file)) {
        Fail-Signing -Reason "待签名文件不存在：$file" -Status $status
    }
    $resolved = (Resolve-Path -LiteralPath $file).Path
    $sigArgs = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampUrl)
    if ($status.mode -eq "thumbprint") {
        $sigArgs += @("/sha1", $Thumbprint)
    } else {
        $sigArgs += @("/f", $PfxPath)
        if ($PfxPassword) { $sigArgs += @("/p", $PfxPassword) }
    }
    $sigArgs += $resolved

    Write-Host "签名：$resolved"
    & $signtool @sigArgs
    if ($LASTEXITCODE -ne 0) {
        Fail-Signing -Reason "signtool 签名失败（exit=$LASTEXITCODE）：$resolved" -Status $status
    }

    $sig = Get-AuthenticodeSignature -FilePath $resolved
    $results += [ordered]@{
        path   = $resolved
        status = [string]$sig.Status
        signer = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { "" }
    }
    if ($sig.Status -ne "Valid") {
        Fail-Signing -Reason "签名后校验状态不是 Valid（$($sig.Status)）：$resolved" -Status $status
    }
    Write-Host "  校验：$($sig.Status)  $($sig.SignerCertificate.Subject)"
}

$status.signed = $true
$status.reason = "signed-ok"
$status.files = $results
Write-StatusJson -Status $status
Write-Host "签名完成：$($results.Count) 个文件全部 Valid（时间戳 $TimestampUrl）。" -ForegroundColor Green
exit 0
