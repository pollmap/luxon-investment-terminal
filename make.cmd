@echo off
setlocal

if "%1"=="" goto help
if "%1"=="dev" goto dev
if "%1"=="stop-dev" goto stopdev
if "%1"=="api" goto api
if "%1"=="web" goto web
if "%1"=="test" goto test
if "%1"=="build" goto build
if "%1"=="verify" goto verify

:help
echo Usage: make ^<dev^|stop-dev^|api^|web^|test^|build^|verify^>
exit /b 0

:dev
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1"
exit /b %ERRORLEVEL%

:stopdev
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stop-dev.ps1"
exit /b %ERRORLEVEL%

:api
python -m uvicorn services.api.main:app --reload --port 8000
exit /b %ERRORLEVEL%

:web
pnpm --filter @personal-fastgraphs/web dev
exit /b %ERRORLEVEL%

:test
python -m pytest
if errorlevel 1 exit /b %ERRORLEVEL%
pnpm --filter @personal-fastgraphs/web test
exit /b %ERRORLEVEL%

:build
pnpm build
exit /b %ERRORLEVEL%

:verify
python -m pytest
if errorlevel 1 exit /b %ERRORLEVEL%
pnpm build
if errorlevel 1 exit /b %ERRORLEVEL%
pnpm --filter @personal-fastgraphs/web test
exit /b %ERRORLEVEL%
