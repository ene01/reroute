@echo off
title Reroute - public link
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_public.ps1"
if errorlevel 1 pause
