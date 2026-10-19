@echo off
copy /Y "D:\desktop\电量显示插件\review\lead\mutex-test.ps1" "%TEMP%\pb-review-mutex.ps1" >nul
runas /trustlevel:0x20000 "powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\pb-review-mutex.ps1"
