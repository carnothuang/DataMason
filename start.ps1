#Requires -Version 5.1
<#
.SYNOPSIS
    DataMason 快速启动：前后端依赖安装、编译与部署。

.DESCRIPTION
    统一封装项目所需的专属 venv（.venv）与前端（web）构建流程：
      dev   启动 Flask 后端(8765) + Vite 前端(5173)，Vite 实时编译，日常开发/测试用。
      prod  先构建前端(tsc --noEmit && vite build)，再由后端 waitress 单端口托管 dist。
      build 仅安装依赖并构建前端（后端为纯 Python，无需编译）。

.PARAMETER Mode
    运行模式：dev（默认）/ prod / build。

.PARAMETER Port
    后端端口，默认 8765。dev 模式下 Vite 代理固定指向 8765，改端口需同步改 web/vite.config.ts。

.PARAMETER WebPort
    前端 dev server 端口，默认 5173。

.PARAMETER SkipInstall
    跳过依赖安装检查/安装。

.PARAMETER SkipBuild
    prod 模式跳过前端构建（复用已有 web/dist）。

.PARAMETER NoBrowser
    启动后不自动打开浏览器。

.EXAMPLE
    ./start.ps1
    开发模式：启动前后端。

.EXAMPLE
    ./start.ps1 -Mode prod
    生产模式：构建前端并单端口托管。

.EXAMPLE
    ./start.ps1 -Mode dev -SkipInstall
    跳过依赖检查，直接启动开发服务。
#>
[CmdletBinding()]
param(
    [ValidateSet("dev", "prod", "build")]
    [string]$Mode = "dev",

    [int]$Port = 8765,

    [int]$WebPort = 5173,

    [switch]$SkipInstall,

    [switch]$SkipBuild,

    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

# 中文控制台输出（Windows 默认 GBK 会乱码）
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$Root       = $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$WebDir     = Join-Path $Root "web"
$ShellExe   = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host "  OK  $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message){ Write-Host "  !!  $Message" -ForegroundColor Yellow }

function Assert-Prereqs {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "未找到专属虚拟环境：$VenvPython`n请先在项目根目录执行：E:\python\3.12.9\python.exe -m venv .venv"
    }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw "未找到 Node.js，请安装 Node >= 18。"
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "未找到 npm，请确认 Node.js 安装完整。"
    }
}

function Install-Deps {
    if ($SkipInstall) { Write-Warn2 "已跳过依赖检查 (-SkipInstall)"; return }

    & $VenvPython -c "import flask, sqlalchemy, pydantic, httpx, cryptography, sqlglot, openpyxl, waitress" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "安装后端依赖 (requirements.txt)"
        & $VenvPython -m pip install -r (Join-Path $Root "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "后端依赖安装失败。" }
    } else {
        Write-Ok "后端依赖已就绪"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $WebDir "node_modules"))) {
        Write-Step "安装前端依赖 (npm install)"
        npm --prefix $WebDir install
        if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败。" }
    } else {
        Write-Ok "前端依赖已就绪"
    }
}

function Build-Web {
    Write-Step "构建前端 (tsc --noEmit && vite build)"
    npm --prefix $WebDir run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
    Write-Ok "前端构建完成 -> web/dist"
}

function Start-Terminal([string]$Title, [string]$Command) {
    $wrapped = "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    Start-Process -FilePath $ShellExe -ArgumentList "-NoExit", "-Command", $wrapped -WorkingDirectory $Root | Out-Null
}

function Wait-Health([int]$HealthPort, [int]$TimeoutSec = 45) {
    $url = "http://127.0.0.1:$HealthPort/api/v1/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri $url -TimeoutSec 3
            if ($resp.status -eq "ok") { return $resp }
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    return $null
}

Write-Host "DataMason 快速启动  Mode=$Mode" -ForegroundColor Magenta
Assert-Prereqs
Install-Deps

switch ($Mode) {
    "build" {
        Build-Web
        Write-Ok "编译完成（后端为纯 Python，无需编译）"
    }

    "dev" {
        if ($Port -ne 8765) {
            Write-Warn2 "dev 模式下 Vite 代理固定指向 8765，改 -Port 需同步修改 web/vite.config.ts"
        }
        Write-Step "启动后端 Flask (http://127.0.0.1:$Port)"
        Start-Terminal "DataMason-Backend" "& '$VenvPython' -m flask --app server.app:create_app run --port $Port"
        $health = Wait-Health $Port
        if ($health) { Write-Ok "后端就绪（版本 $($health.version)）" }
        else { Write-Warn2 "后端健康检查超时，请查看 DataMason-Backend 窗口日志" }

        Write-Step "启动前端 Vite (http://127.0.0.1:$WebPort)"
        Start-Terminal "DataMason-Frontend" "npm --prefix '$WebDir' run dev -- --port $WebPort --host 127.0.0.1"

        $url = "http://127.0.0.1:$WebPort"
        if (-not $NoBrowser) { Start-Process $url | Out-Null }
        Write-Host "`n前端: $url`n后端: http://127.0.0.1:$Port" -ForegroundColor Green
    }

    "prod" {
        if ($SkipBuild) { Write-Warn2 "已跳过前端构建 (-SkipBuild)" } else { Build-Web }

        Write-Step "启动后端 waitress (http://127.0.0.1:$Port)"
        Start-Terminal "DataMason-Prod" "& '$VenvPython' scripts\serve_walkthrough.py $Port"
        $health = Wait-Health $Port
        if ($health) { Write-Ok "服务就绪（版本 $($health.version)）" }
        else { Write-Warn2 "健康检查超时，请查看 DataMason-Prod 窗口日志" }

        $url = "http://127.0.0.1:$Port"
        if (-not $NoBrowser) { Start-Process $url | Out-Null }
        Write-Host "`n访问: $url" -ForegroundColor Green
    }
}
