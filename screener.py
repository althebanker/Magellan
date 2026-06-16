@echo off
REM Double-click to build today's deck from live data and open it.
REM First run: run-quick.bat does a fast ~2-min test instead.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Install from https://python.org ^(tick "Add to PATH"^) then re-run. & pause & exit /b)
echo Installing dependencies (first run only)...
python -m pip install --quiet yfinance pandas numpy
echo Scanning the market - this can take a few minutes...
python screener.py %*
pause
