@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================================
echo    MASOFA AGENT MASOFA_212.BAT (Administrator)
echo ==========================================================

:: Administrator huquqini tekshirish va talab qilish
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo [!] Administrator huquqi talab qilinmoqda... UAC oynasi ochilmoqda...
    goto UACPrompt
) else (
    goto gotAdmin
)

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )
    pushd "%CD%"
    CD /D "%~dp0"

echo [+] Administrator huquqi olindi. Python qidirilmoqda...

:: Papka tayyorlash
set "INSTALL_DIR=C:\MasofaAgent"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /y agent.py "%INSTALL_DIR%\agent.py" >nul

:: Python topish (Registry va System path orqali)
for /f "delims=" %%i in ('powershell -NoProfile -Command "$py = $null; foreach ($hive in @('HKLM:\\SOFTWARE\\Python\\PythonCore', 'HKCU:\\SOFTWARE\\Python\\PythonCore')) { if (Test-Path $hive) { foreach ($v in Get-ChildItem $hive -ErrorAction SilentlyContinue) { $e = (Get-ItemProperty (Join-Path $v.PSPath 'InstallPath') -Name 'ExecutablePath' -ErrorAction SilentlyContinue).ExecutablePath; if ($e -and (Test-Path $e)) { $py = $e; break } } } if ($py) { break } }; if (-not $py) { foreach($p in @('C:\\Python312\\python.exe','C:\\Python311\\python.exe','C:\\Python310\\python.exe','C:\\Python39\\python.exe', "$env:ProgramFiles\\Python312\\python.exe", "$env:ProgramFiles\\Python311\\python.exe")) { if (Test-Path $p) { $py = $p; break } } }; if (-not $py) { try { $pyPath = & py -3 -c 'import sys; print(sys.executable)' 2>$null; if ($pyPath) { $py = $pyPath.Trim() } } catch {} }; write-output $py"') do set PYTHON_EXE=%%i

if "%PYTHON_EXE%"=="" (
    echo [XATO] Python topilmadi! Iltimos, python.org saytidan Python o'rnating.
    pause
    exit /b 1
)

echo [+] Python topildi: %PYTHON_EXE%
echo [+] Kutubxonalar o'rnatilmoqda / yangilanmoqda...
"%PYTHON_EXE%" -m pip install --upgrade pip websockets pyautogui pillow >nul 2>&1

echo [+] Agent ishga tushmoqda...
cd /d "%INSTALL_DIR%"
"%PYTHON_EXE%" agent.py %*

pause
