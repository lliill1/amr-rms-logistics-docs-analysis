@echo off
chcp 65001 >nul
rem DocAnalyzer AMR/RMS: old Streamlit interface.
rem First run creates the .venv environment; when requirements.txt changes, libraries are updated automatically.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    if exist .venv rmdir /s /q .venv
    echo Creating virtual environment...
    py -3.12 -m venv .venv 2>nul || py -3.13 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :fail_create
    .venv\Scripts\python -m pip install --upgrade pip
)

fc /b requirements.txt .venv\requirements.installed.txt >nul 2>&1
if errorlevel 1 (
    echo Installing libraries from requirements.txt...
    .venv\Scripts\python -m pip install -r requirements.txt
    if errorlevel 1 goto :fail_install
    copy /y requirements.txt .venv\requirements.installed.txt >nul
)

.venv\Scripts\streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
goto :eof

:fail_create
echo.
echo Could not create the environment. Install Python 3.12:  py install 3.12
echo then run run.bat again.
if exist .venv rmdir /s /q .venv
pause
goto :eof

:fail_install
echo.
echo Library installation failed (see the messages above).
echo Check the internet connection and run run.bat again.
echo If the error mentions "Microsoft Visual C++" or "Failed building wheel", the Python version is too new:
echo   delete the .venv folder, run  py install 3.12  and run run.bat again.
pause
