<#
.SYNOPSIS
    Downloads and installs Fakturama 2.2.0 (with bundled Java) for this automation.

.DESCRIPTION
    Fakturama ships as an install4j-wrapped MSI that requires administrator
    rights, so running this will raise one UAC prompt. Click "Yes" once.

    After it finishes, Fakturama.exe is normally at
        C:\Program Files\Fakturama2\Fakturama.exe
    which the automation discovers on its own. If you install somewhere else,
    set FAKTURAMA_EXE or pass --fakturama <path>.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_fakturama.ps1
#>

[CmdletBinding()]
param(
    [string] $Version = "2.2.0",
    [string] $DownloadDir = "$env:USERPROFILE\FakturamaSetup"
)

$ErrorActionPreference = "Stop"

$fileName = "Installer_Fakturama_windows-x64_${Version}_with_jre.msi"
$url      = "https://files.fakturama.info/release/v$Version/$fileName"
$msiPath  = Join-Path $DownloadDir $fileName

Write-Host "Fakturama $Version installer" -ForegroundColor Cyan

if (-not (Test-Path $DownloadDir)) {
    New-Item -ItemType Directory -Path $DownloadDir -Force | Out-Null
}

if (Test-Path $msiPath) {
    $sizeMb = [math]::Round((Get-Item $msiPath).Length / 1MB, 1)
    Write-Host "  already downloaded ($sizeMb MB): $msiPath"
} else {
    Write-Host "  downloading from $url"
    $previous = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $url -OutFile $msiPath
    } finally {
        $ProgressPreference = $previous
    }
    $sizeMb = [math]::Round((Get-Item $msiPath).Length / 1MB, 1)
    Write-Host "  downloaded $sizeMb MB"
}

Write-Host ""
Write-Host "  Installing. Windows will ask for administrator permission -" -ForegroundColor Yellow
Write-Host "  click 'Yes' on the UAC prompt to continue." -ForegroundColor Yellow
Write-Host ""

$logPath = Join-Path $DownloadDir "fakturama-install.log"
$process = Start-Process msiexec.exe `
    -ArgumentList '/i', "`"$msiPath`"", '/qb', '/l*v', "`"$logPath`"" `
    -Verb RunAs -Wait -PassThru

if ($process.ExitCode -ne 0) {
    Write-Host "Installer exited with code $($process.ExitCode)." -ForegroundColor Red
    Write-Host "Verbose log: $logPath"
    exit $process.ExitCode
}

$candidates = @(
    "C:\Program Files\Fakturama2\Fakturama.exe",
    "C:\Program Files\Fakturama\Fakturama.exe",
    "C:\Program Files (x86)\Fakturama2\Fakturama.exe"
)
$found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($found) {
    Write-Host ""
    Write-Host "Installed: $found" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Launch Fakturama once by hand and complete its first-run setup"
    Write-Host "     (it creates its database and asks for a workspace folder)."
    Write-Host "  2. Close it, then run:"
    Write-Host "       python -m fakturama_automation --image samples\order_sample.png"
} else {
    Write-Host "Install reported success but Fakturama.exe was not found in the usual places." -ForegroundColor Yellow
    Write-Host "Set FAKTURAMA_EXE to its path, or pass --fakturama <path>."
    exit 1
}
