@echo off
chcp 65001 >nul
rem Запуск DocAnalyzer AMR/RMS в Windows. При первом запуске создаётся виртуальное окружение.
cd /d "%~dp0"

if not exist .venv (
    echo Создаю виртуальное окружение...
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
