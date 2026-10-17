@echo off
rem V7610 pullback shadow signal report - read-only chart queries, no orders
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
"C:\Python311_32\python.exe" -B tools\v7610_pullback_shadow_report.py
pause
