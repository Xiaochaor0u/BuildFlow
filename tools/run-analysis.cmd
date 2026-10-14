@echo off
REM ============================================================================
REM  BuildFlow: read real Jenkins build history and write HTML/CSV reports.
REM
REM  Usage (run from the repository root):
REM      tools\run-analysis.cmd
REM      tools\run-analysis.cmd --limit 100 --outdir reports
REM
REM  Credentials are read from tools\.env.cmd, which is git-ignored. Copy
REM  tools\.env.example.cmd to tools\.env.cmd and fill in the values:
REM      set JENKINS_URL=http://127.0.0.1:8080
REM      set JENKINS_USER=admin
REM      set JENKINS_TOKEN=<API Token>
REM      set JENKINS_JOB=buildflow-ci
REM ============================================================================
setlocal

set "REPO_ROOT=%~dp0.."
set "PYTHON_EXE=D:\Python\python.exe"

if exist "%REPO_ROOT%\tools\.env.cmd" (
    call "%REPO_ROOT%\tools\.env.cmd"
) else if exist "D:\work\buildflow\env.cmd" (
    call "D:\work\buildflow\env.cmd"
)

if "%JENKINS_URL%"=="" (
    echo [ERROR] JENKINS_URL is not set. Create tools\.env.cmd - see tools\.env.example.cmd
    exit /b 2
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python interpreter not found: %PYTHON_EXE%
    echo         Edit PYTHON_EXE in this script, or put python on PATH.
    exit /b 2
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo [INFO] Reading real Jenkins build history and generating reports...
"%PYTHON_EXE%" "%REPO_ROOT%\tools\buildflow_analyzer\run.py" %*

endlocal
