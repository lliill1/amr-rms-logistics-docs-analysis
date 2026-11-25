@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "APP_ROOT=%~dp0"
set "LOG_DIR=%~dp0logs"
set "PID_FILE=%~dp0logs\service.pid"
set "ENV_CONFIG=%~dp0python-env.path"

:menu
cls
echo ==========================================
echo       DocAnalyzer AMR/RMS - управление
echo ==========================================
echo 1. Запустить сервис
echo 2. Проверить статус сервиса
echo 3. Остановить сервис
echo 4. Перезапустить сервис
echo 5. Удалить рабочие данные и отчеты
echo 6. Настроить папку Python-окружения и загрузки библиотек
echo 0. Выход
echo.
set "menu_choice="
choice /c 0123456 /n /m "Выберите действие (0-6): "
if errorlevel 7 goto configure_python_env
if errorlevel 6 goto clean_data
if errorlevel 5 goto restart_service
if errorlevel 4 goto stop_service
if errorlevel 3 goto show_status
if errorlevel 2 goto start_service
goto :eof

:start_service
call :prepare_environment
if errorlevel 1 (
    pause
    goto menu
)

call :owned_process_running
if not errorlevel 1 (
    echo Сервис уже запущен через run.bat.
    pause
    goto menu
)
call :http_service_running
if not errorlevel 1 (
    echo На порту 8501 уже отвечает приложение.
    echo Оно запущено не через этот run.bat; остановите его отдельно.
    pause
    goto menu
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
echo Запуск сервиса...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$python = Join-Path $env:PYTHON_ENV 'Scripts\python.exe'; $script = Join-Path $env:APP_ROOT 'app.py'; $arguments = '-m streamlit run ' + [char]34 + $script + [char]34 + ' --server.address=127.0.0.1 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false'; $stdout = Join-Path $env:LOG_DIR 'service.log'; $stderr = Join-Path $env:LOG_DIR 'service-error.log'; $p = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $env:APP_ROOT -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru; Set-Content -LiteralPath $env:PID_FILE -Value $p.Id -Encoding ASCII; Write-Output ('PID: ' + $p.Id)"
if errorlevel 1 (
    echo Не удалось запустить сервис.
    pause
    goto menu
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ready = $false; for ($i = 0; $i -lt 20; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}; Start-Sleep -Seconds 1 }; if ($ready) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo Сервис запущен, но пока не отвечает. Проверьте журналы через пункт 2.
) else (
    echo Сервис работает: http://127.0.0.1:8501
    start "" "http://127.0.0.1:8501"
)
pause
goto menu

:show_status
call :owned_process_running
if errorlevel 1 (
    call :http_service_running
    if errorlevel 1 (
        echo Сервис не работает.
    ) else (
        echo На порту 8501 отвечает приложение, не запущенное через этот run.bat.
        pause
        goto menu
    )
) else (
    call :http_service_running
    if errorlevel 1 (
        echo Процесс сервиса запущен, но веб-интерфейс не отвечает.
    ) else (
        echo Сервис работает: http://127.0.0.1:8501
        pause
        goto menu
    )
)
echo.
choice /c 12 /n /m "Показать последние строки журналов и состояние процесса? (1=да, 2=нет): "
if errorlevel 2 goto menu
goto display_logs

:display_logs
echo.
echo --- Журнал работы: logs\service.log ---
if exist "%LOG_DIR%\service.log" (
    powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:LOG_DIR 'service.log') -Tail 100"
) else (
    echo Журнал пока не создан.
)
echo.
echo --- Журнал ошибок: logs\service-error.log ---
if exist "%LOG_DIR%\service-error.log" (
    powershell -NoProfile -Command "Get-Content -LiteralPath (Join-Path $env:LOG_DIR 'service-error.log') -Tail 100"
) else (
    echo Журнал ошибок пока не создан.
)
echo.
call :owned_process_running
if errorlevel 1 (
    echo Состояние процесса: управляемый процесс не найден.
) else (
    echo Состояние процесса: процесс сервиса запущен.
)
pause
goto menu

:stop_service
if not exist "%PID_FILE%" (
    echo Нет сохраненного идентификатора процесса. Управляемый сервис не запущен.
    pause
    goto menu
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $id = 0; if (-not [int]::TryParse((Get-Content -Raw -LiteralPath $env:PID_FILE), [ref]$id)) { Write-Error 'Некорректный PID-файл.'; exit 1 }; $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id); $root = $env:APP_ROOT.TrimEnd('\'); if ($p -and $p.CommandLine -like '*streamlit*' -and $p.CommandLine -like '*app.py*' -and $p.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) { Stop-Process -Id $id -Force; Remove-Item -LiteralPath $env:PID_FILE -Force; Write-Output 'Сервис остановлен.'; exit 0 }; Remove-Item -LiteralPath $env:PID_FILE -Force; Write-Output 'Процесс сервиса не найден; устаревший PID-файл удален.'; exit 1"
if errorlevel 1 echo Если приложение все еще работает, оно не было запущено этим run.bat.
pause
goto menu

:restart_service
if exist "%PID_FILE%" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $id = 0; if ([int]::TryParse((Get-Content -Raw -LiteralPath $env:PID_FILE), [ref]$id)) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id); $root = $env:APP_ROOT.TrimEnd('\'); if ($p -and $p.CommandLine -like '*streamlit*' -and $p.CommandLine -like '*app.py*' -and $p.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) { Stop-Process -Id $id -Force } }; Remove-Item -LiteralPath $env:PID_FILE -Force -ErrorAction SilentlyContinue"
    timeout /t 2 /nobreak >nul
)
call :prepare_environment
if errorlevel 1 (
    pause
    goto menu
)
call :http_service_running
if not errorlevel 1 (
    echo На порту 8501 уже отвечает приложение, не управляемое этим run.bat.
    echo Оно не было остановлено. Освободите порт и повторите перезапуск.
    pause
    goto menu
)
goto start_service

:clean_data
echo Будут удалены содержимое папок data и out: база данных, загруженные и
echo распакованные документы, а также созданные отчеты.
echo Код, виртуальное окружение и журналы в папке logs сохранятся.
choice /c 12 /n /m "Продолжить удаление? (1=да, 2=нет): "
if errorlevel 2 (
    echo Удаление отменено.
    pause
    goto menu
)
call :owned_process_running
if not errorlevel 1 (
    echo Сначала остановите сервис, чтобы не повредить базу данных.
    pause
    goto menu
)
call :http_service_running
if not errorlevel 1 (
    echo На порту 8501 работает приложение. Остановите его перед очисткой.
    pause
    goto menu
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; foreach ($name in @('data','out')) { $dir = Join-Path $env:APP_ROOT $name; if (Test-Path -LiteralPath $dir) { Get-ChildItem -LiteralPath $dir -Force | Where-Object { -not ($name -eq 'data' -and $_.Name -eq '.gitkeep') } | Remove-Item -Recurse -Force } }; Write-Output 'Рабочие данные и отчеты удалены.'"
if errorlevel 1 (
    echo Не удалось полностью удалить данные. Проверьте сообщения выше.
) else (
    echo Журналы и виртуальное окружение не удалялись.
)
pause
goto menu

:prepare_environment
call :load_python_env
if not exist "%PYTHON%" (
    echo Создание виртуального окружения...
    py -3.12 -m venv "%PYTHON_ENV%" >nul 2>&1
    if errorlevel 1 py -3.13 -m venv "%PYTHON_ENV%" >nul 2>&1
    if errorlevel 1 python -m venv "%PYTHON_ENV%"
    if errorlevel 1 (
        echo Не удалось создать окружение. Установите Python 3.12 и повторите запуск.
        exit /b 1
    )
    "%PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo Не удалось обновить pip.
        exit /b 1
    )
)
fc /b requirements.txt "%PYTHON_ENV%\requirements.installed.txt" >nul 2>&1
if errorlevel 1 (
    echo Установка библиотек из requirements.txt...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Установка библиотек не удалась. Проверьте интернет и сообщения выше.
        exit /b 1
    )
    copy /y requirements.txt "%PYTHON_ENV%\requirements.installed.txt" >nul
)
exit /b 0

:configure_python_env
call :load_python_env
echo Текущая папка Python-окружения и кэша pip:
echo %PYTHON_ENV%
echo.
echo 1. Ввести путь вручную
echo 2. Выбрать папку в Проводнике
echo 0. Отмена
choice /c 012 /n /m "Выберите действие (0-2): "
if errorlevel 3 goto configure_python_env_browse
if errorlevel 2 goto configure_python_env_manual
goto menu

:configure_python_env_manual
set "PYTHON_ENV_NEW_PATH="
set /p "PYTHON_ENV_NEW_PATH=Введите полный путь к папке: "
if not defined PYTHON_ENV_NEW_PATH (
    echo Путь не указан; настройка не изменена.
    pause
    goto menu
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%APP_ROOT%scripts\configure_python_env.ps1" -Mode Manual -ConfigFile "%ENV_CONFIG%"
if errorlevel 1 (
    echo Не удалось сохранить путь. Проверьте его и повторите попытку.
    pause
    goto menu
)
goto python_env_saved

:configure_python_env_browse
set "PYTHON_ENV_CURRENT=%PYTHON_ENV%"
powershell -NoProfile -ExecutionPolicy Bypass -STA -File "%APP_ROOT%scripts\configure_python_env.ps1" -Mode Browse -ConfigFile "%ENV_CONFIG%"
if errorlevel 2 (
    echo Выбор папки отменен.
    pause
    goto menu
)
if errorlevel 1 (
    echo Не удалось открыть диалог или сохранить путь.
    pause
    goto menu
)
goto python_env_saved

:python_env_saved
call :load_python_env
echo Папка сохранена: %PYTHON_ENV%
echo Она будет использована при следующем запуске. Существующее окружение не перемещается.
pause
goto menu

:load_python_env
set "PYTHON_ENV=%APP_ROOT%.venv"
if exist "%ENV_CONFIG%" set /p "PYTHON_ENV="<"%ENV_CONFIG%"
set "PYTHON=%PYTHON_ENV%\Scripts\python.exe"
set "PIP_CACHE_DIR=%PYTHON_ENV%\pip-cache"
exit /b 0

:owned_process_running
powershell -NoProfile -ExecutionPolicy Bypass -Command "$id = 0; if (-not (Test-Path -LiteralPath $env:PID_FILE) -or -not [int]::TryParse((Get-Content -Raw -LiteralPath $env:PID_FILE), [ref]$id)) { exit 1 }; $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id); $root = $env:APP_ROOT.TrimEnd('\'); if ($p -and $p.CommandLine -like '*streamlit*' -and $p.CommandLine -like '*app.py*' -and $p.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) { exit 0 }; exit 1"
exit /b %errorlevel%

:http_service_running
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
exit /b %errorlevel%
