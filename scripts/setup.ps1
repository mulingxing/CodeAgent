$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsDir = Join-Path $projectRoot '.tools'
$uvExe = Join-Path $toolsDir 'uv\uv.exe'
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $toolsDir 'python'

New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
if (-not (Test-Path -LiteralPath $uvExe)) {
    $archive = Join-Path $toolsDir 'uv.zip'
    Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.7/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $toolsDir 'uv') -Force
}

Push-Location $projectRoot
try {
    & $uvExe python install 3.12.16 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw '项目内 Python 安装失败。' }
    $pythonExe = Join-Path $env:UV_PYTHON_INSTALL_DIR 'cpython-3.12.16-windows-x86_64-none\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw '找不到项目内 Python。' }
    & $uvExe sync --locked --python $pythonExe --extra dev
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    Write-Host '环境准备完成。请在项目根目录的 .env 中填写 DeepSeek API Key。'
} finally {
    Pop-Location
}
