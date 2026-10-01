@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

echo ========================================
echo   OneBot - Environment Check
echo ========================================

:: ---- Check npm ----
where npm >nul 2>&1
if !errorlevel! neq 0 (
    echo [npm] Not found, installing Node.js LTS ...
    winget install OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    if !errorlevel! neq 0 (
        echo [npm] winget install failed. Please install manually: https://nodejs.org/
        pause
        exit /b 1
    )
    set "PATH=%PATH%;C:\Program Files\nodejs"
    where npm >nul 2>&1
    if !errorlevel! neq 0 (
        echo [npm] Still not found after install. Restart terminal and try again.
        pause
        exit /b 1
    )
    echo [npm] Installed OK
) else (
    echo [npm] OK
)

:: ---- Check Claude Code ----
where claude >nul 2>&1
if !errorlevel! neq 0 (
    echo [Claude Code] Not found, installing via npm ...
    call npm install -g @anthropic-ai/claude-code
    if !errorlevel! neq 0 (
        echo [Claude Code] Install failed. Try manually: npm install -g @anthropic-ai/claude-code
        pause
        exit /b 1
    )
    where claude >nul 2>&1
    if !errorlevel! neq 0 (
        echo [Claude Code] Still not found after install. Restart terminal and try again.
        pause
        exit /b 1
    )
    echo [Claude Code] Installed OK
) else (
    echo [Claude Code] OK
)

echo ========================================
echo   Environment OK, launching tool
echo ========================================
echo.

echo Select tool:
echo 1) Claude
echo 2) Codex
set /p choice=Enter option (1/2):

set http_proxy=http://127.0.0.1:7892
set https_proxy=http://127.0.0.1:7892
set OPENAI_API_KEY=sk-WZVb95ezwXvNY20PbgteeLpRyoyD0gRaX2xleB7zYsnXUnds
set OPENAI_BASE_URL=https://iwwi.eu.cc/v1
set ANTHROPIC_AUTH_TOKEN=sk-sfXxMUCamzq5WwiTIL1zLSUzZwYVD4jJjDHo12SPRZKV04KQ
set ANTHROPIC_BASE_URL=https://api.justwoker.icu

if "%choice%"=="1" (
    set ANTHROPIC_MODEL=claude-opus-4-8
    echo Launching Claude...
    claude --dangerously-skip-permissions
) else if "%choice%"=="2" (
    set OPENAI_MODEL=deepseek-v4-pro
    echo Launching Codex...
    codex --full-auto
) else (
    echo Invalid option, enter 1 or 2
    exit /b 1
)
