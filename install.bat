@echo off
echo ========================================
echo AART - Amazon Automated Rostering Tool
echo ========================================
echo.

:: Try to find Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not on your PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Installing required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install packages. Check the errors above.
    pause
    exit /b 1
)

echo.
echo Launching AART...
echo (Browser will open automatically)
echo.
echo Press Ctrl+C to stop the server
echo.

python -m streamlit run app_roster_weekly.py

pause
