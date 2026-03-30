@echo off
rem 强制切换到 UTF-8 代码页，避免 PowerShell/cmd 调用时中文提示乱码
chcp 65001 >nul
setlocal enabledelayedexpansion

set "PROJECT_ROOT=%~dp0.."
for %%I in ("%PROJECT_ROOT%") do set "PROJECT_ROOT=%%~fI"

set "APP_NAME=CPA-Codex-Manager"
set "DIST_DIR=%PROJECT_ROOT%\dist"
set "BUILD_DIR=%PROJECT_ROOT%\build"
set "SPEC_FILE=%PROJECT_ROOT%\CPA-Codex-Manager-Desktop.spec"
set "BUILD_MODE=%~1"
if not defined BUILD_MODE set "BUILD_MODE=onefile"
set "BUILD_MODE=%BUILD_MODE:"=%"
if /I not "%BUILD_MODE%"=="onefile" if /I not "%BUILD_MODE%"=="onedir" (
  echo 用法: %~n0 [onefile^|onedir]
  exit /b 1
)
set "ICON_ICO=%PROJECT_ROOT%\assets\icon.ico"
set "ICON_PNG=%PROJECT_ROOT%\assets\icon.png"
set "ICON_JPG=%PROJECT_ROOT%\assets\icon.jpg"
set "ICON_SOURCE="
set "OUTPUT_HINT=%DIST_DIR%\%APP_NAME%.exe"

if /I "%BUILD_MODE%"=="onedir" (
  set "OUTPUT_HINT=%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe"
)

echo [1/5] 清理旧产物
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"

echo [2/5] 检查依赖
py -3 -m PyInstaller --version >nul 2>&1 || (
  echo 未检测到 PyInstaller，请先执行: py -3 -m pip install pyinstaller
  exit /b 1
)
py -3 -c "import webview" >nul 2>&1 || (
  echo 未检测到 pywebview，请先安装项目依赖后再打包。
  exit /b 1
)

if not exist "%ICON_ICO%" (
  if exist "%ICON_JPG%" (
    set "ICON_SOURCE=%ICON_JPG%"
  ) else if exist "%ICON_PNG%" (
    set "ICON_SOURCE=%ICON_PNG%"
  )

  if defined ICON_SOURCE (
    echo 未找到 assets\icon.ico，尝试根据图标源文件自动生成...
    py -3 -c "from PIL import Image" >nul 2>&1 || py -3 -m pip install pillow
    py -3 "%PROJECT_ROOT%\scripts\generate_windows_icon.py" "!ICON_SOURCE!" "%ICON_ICO%"
  ) else (
    echo 未找到 assets\icon.ico / icon.jpg / icon.png，将使用默认 EXE 图标。
  )
)

echo [3/5] 构建 Windows EXE ^(模式: %BUILD_MODE%^)
cd /d "%PROJECT_ROOT%"
set "APP_BUILD_MODE=%BUILD_MODE%"
py -3 -m PyInstaller --noconfirm --clean "%SPEC_FILE%"
if errorlevel 1 exit /b 1

if /I "%BUILD_MODE%"=="onedir" (
  echo [4/5] 打包便携分发 ZIP
  powershell -NoProfile -Command "Compress-Archive -Path '%DIST_DIR%\%APP_NAME%\*' -DestinationPath '%DIST_DIR%\%APP_NAME%-windows-portable.zip' -Force"
  if errorlevel 1 exit /b 1
  set "OUTPUT_HINT=%DIST_DIR%\%APP_NAME%-windows-portable.zip"
) else (
  echo [4/5] onefile 模式无需额外封装
)

echo [5/5] 完成
if exist "%OUTPUT_HINT%" (
  echo 输出: %OUTPUT_HINT%
) else if exist "%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe" (
  echo EXE: %DIST_DIR%\%APP_NAME%\%APP_NAME%.exe
) else if exist "%DIST_DIR%\%APP_NAME%.exe" (
  echo EXE: %DIST_DIR%\%APP_NAME%.exe
) else if exist "%DIST_DIR%\%APP_NAME%" (
  echo 目录: %DIST_DIR%\%APP_NAME%
) else (
  echo 请检查 dist 目录中的输出文件。
)

echo.
echo 分发建议:
echo   - 默认 onefile: 直接发送 %APP_NAME%.exe，更适合普通用户。
echo   - onedir: 发送自动生成的 portable.zip，避免用户漏拷 _internal。
echo   - 如需正式安装包，可再用 Inno Setup / NSIS 对上述产物二次封装。