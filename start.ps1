# start.ps1 - Windows 原生啟動腳本
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# 支援手動觸發設定嚮導: .\start.ps1 setup [args...]
if ($args.Count -gt 0 -and ($args[0] -eq "setup" -or $args[0] -eq "--setup")) {
    $remainingArgs = $args[1..($args.Length - 1)]
    python scripts/setup_wizard.py @remainingArgs
    exit 0
}

$TargetEnv = if ($env:ENV_FILE) { $env:ENV_FILE } else { ".env" }

# 初次啟動檢查：若尚未建立設定檔，引導啟動設定嚮導
if (-not (Test-Path $TargetEnv)) {
    Write-Host "⚠️ 尚未偵測到 $TargetEnv 設定檔！" -ForegroundColor Yellow
    $choice = Read-Host "是否立即啟動初始化設定嚮導？(Y/n)"
    if ($choice -match '^[Yy]$' -or [string]::IsNullOrWhiteSpace($choice)) {
        python scripts/setup_wizard.py
    } else {
        Write-Host "💡 已略過設定，使用預設配置啟動。" -ForegroundColor Cyan
    }
}

$Port = if ($env:ASSISTANT_PORT) { $env:ASSISTANT_PORT } elseif ($env:PORT) { $env:PORT } else { "8899" }

Write-Host "=== 正在啟動 Personal Assistant ===" -ForegroundColor Green
Write-Host "工作目錄: $ScriptDir"
Write-Host "設定檔案: $TargetEnv"
Write-Host "監聽埠號: $Port"

uv run uvicorn main:app --host 0.0.0.0 --port $Port
