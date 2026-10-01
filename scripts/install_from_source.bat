@echo off
chcp 65001 >nul
setlocal EnableExtensions
rem ---------------------------------------------------------------------------
rem WorkReport 소스 설치 (exe 다운로드가 막힌 PC용)
rem   가상환경(.venv) 만들기, 설치, 바탕화면 바로 가기 만들기, 실행
rem   사용법: scripts\install_from_source.bat [--no-launch] [--no-shortcut]
rem   Python 을 직접 지정하려면: set WORKREPORT_PYTHON=C:\Program Files\Python311\python.exe
rem ---------------------------------------------------------------------------

rem shift 는 %0 도 밀어내므로 스크립트 위치를 먼저 기억한다
set "SCRIPT_DIR=%~dp0"
set "LAUNCH=1"
set "SHORTCUT=1"
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-launch" set "LAUNCH=0"
if /i "%~1"=="--no-shortcut" set "SHORTCUT=0"
shift
goto args
:args_done

cd /d "%SCRIPT_DIR%.."
set "ROOT=%CD%"
echo WorkReport 설치 폴더: %ROOT%
echo.

rem 1. Python 3.11 이상 찾기
set "PY="
if defined WORKREPORT_PYTHON call :probe "%WORKREPORT_PYTHON%"
if not defined PY call :probe "py -3.12"
if not defined PY call :probe "py -3.11"
if not defined PY call :probe "py -3"
if not defined PY call :probe "python"
if not defined PY goto no_python
echo [1/4] Python: %PY%

rem 2. 가상환경
if exist ".venv\Scripts\python.exe" goto venv_ready
echo [2/4] 가상환경을 만드는 중...
%PY% -m venv .venv
if errorlevel 1 goto venv_fail
:venv_ready
set "VPY=%ROOT%\.venv\Scripts\python.exe"

rem 3. 설치
echo [3/4] 필요한 패키지를 설치하는 중... 처음에는 몇 분 걸려요.
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -e ".[whisper]" --disable-pip-version-check
if not errorlevel 1 goto installed
echo.
echo 로컬 Whisper 없이 다시 설치해요. 음성 인식은 설정에서 Azure 또는 CLOVA 를 고르면 돼요.
"%VPY%" -m pip install -e . --disable-pip-version-check
if errorlevel 1 goto pip_fail
:installed
"%VPY%" -c "import workreport" >nul 2>&1
if errorlevel 1 goto pip_fail

rem 4. 바탕화면 바로 가기
if "%SHORTCUT%"=="0" goto shortcut_done
echo [4/4] 바탕화면에 WorkReport 바로 가기를 만드는 중...
if not exist "packaging\workreport.ico" "%VPY%" packaging\make_icon.py packaging\workreport.ico >nul 2>&1
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\WorkReport.lnk'); $s.TargetPath='%ROOT%\.venv\Scripts\pythonw.exe'; $s.Arguments='-m workreport'; $s.WorkingDirectory='%ROOT%'; if (Test-Path '%ROOT%\packaging\workreport.ico') { $s.IconLocation='%ROOT%\packaging\workreport.ico' }; $s.Save()"
if errorlevel 1 echo 바로 가기를 만들지 못했어요. 이 폴더에서 .venv\Scripts\pythonw.exe -m workreport 로 실행하세요.
:shortcut_done

echo.
echo 설치를 마쳤어요. 다음부터는 바탕화면의 WorkReport 를 눌러 실행하세요.
if "%LAUNCH%"=="0" goto done
start "" "%ROOT%\.venv\Scripts\pythonw.exe" -m workreport
echo WorkReport 를 시작했어요. 화면 오른쪽 아래 트레이에서도 찾을 수 있어요.
goto done

:no_python
echo [오류] Python 3.11 이상을 찾지 못했어요.
echo        https://www.python.org/downloads/windows/ 에서 설치한 뒤 이 파일을 다시 실행하세요.
echo        설치 첫 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
goto fail

:venv_fail
echo [오류] 가상환경을 만들지 못했어요. 폴더에 쓰기 권한이 있는지 확인하세요: %ROOT%
goto fail

:pip_fail
echo.
echo [오류] 패키지를 설치하지 못했어요.
echo        회사 네트워크라면 프록시 설정이 필요할 수 있어요. IT 팀에 프록시 주소를 받은 뒤
echo        명령 프롬프트에서 set HTTPS_PROXY=http://프록시주소:포트 를 입력하고
echo        같은 창에서 이 파일을 다시 실행하세요.
echo        pypi.org, files.pythonhosted.org 접속이 허용되어야 해요.
goto fail

:fail
if not defined CI pause
exit /b 1

:done
if not defined CI pause
exit /b 0

rem 사용 가능한 Python 3.11 이상이면 PY 에 기록한다
:probe
if exist "%~1" goto probe_path
%~1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
:probe_path
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set PY="%~1"
exit /b 0
