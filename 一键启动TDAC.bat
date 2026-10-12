@echo off
setlocal EnableExtensions

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title 泰国电子入境卡 TDAC 一键启动

echo ================================================
echo   泰国电子入境卡（TDAC）一键启动
echo ================================================
echo.

if not exist ".build\venv\Scripts\python.exe" (
  echo [错误] 找不到项目虚拟环境：.build\venv\Scripts\python.exe
  echo 请先按 README 的「从源码运行」一节创建虚拟环境（python -m venv .build\venv）。
  echo.
  set "EXIT_CODE=1"
  goto :finish
)

if not exist "tdac_fill.py" (
  echo [错误] 找不到主程序：tdac_fill.py
  echo 请确认此 BAT 文件位于项目根目录。
  echo.
  set "EXIT_CODE=1"
  goto :finish
)

echo [1/2] 检查 Python 主程序...
".build\venv\Scripts\python.exe" -m py_compile tdac_fill.py
if errorlevel 1 (
  echo [错误] 主程序语法检查失败，未启动浏览器。
  echo.
  set "EXIT_CODE=1"
  goto :finish
)

echo [2/2] 启动 TDAC 自动填写流程...
echo 参数：%*
echo.
".build\venv\Scripts\python.exe" tdac_fill.py %*
set "EXIT_CODE=%ERRORLEVEL%"

:finish
echo.
if "%EXIT_CODE%"=="0" (
  echo 流程结束：请查看浏览器中的预览页、输出文件夹和 logs 文件夹。
) else (
  echo 流程结束，但返回码为 %EXIT_CODE%。请查看上面的错误信息和 logs 文件夹。
)
echo.
pause
endlocal & exit /b %EXIT_CODE%
