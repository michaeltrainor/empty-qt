@echo off
rem Build OpenUSD with imaging (Hydra Storm, UsdImagingGL) against this
rem repository's .venv Python, for the Hydra viewport work described in
rem docs/usd-architecture.md. Windows only; needs Visual Studio 2022 or 2026
rem with the C++ workload, CMake, and git. Takes about 15 minutes on 32 cores.
rem
rem   scripts\build_usd_imaging.cmd [USD_ROOT]
rem
rem USD_ROOT (default %USERPROFILE%\dev\openusd) receives src\ (shallow clone of
rem the tag below), deps\, build\, and inst\. Afterwards run the viewer with:
rem
rem   set PYTHONPATH=%USD_ROOT%\inst\Lib\site-packages
rem   set PATH=%USD_ROOT%\inst\lib;%USD_ROOT%\inst\bin;%PATH%
rem   uv run --with PyOpenGL scripts\hydra_spike.py path\to\stage.usd
rem
rem The installed pxr package shadows the usd-core wheel in .venv while
rem PYTHONPATH is set; both are built for the same CPython 3.13.

setlocal
set USD_TAG=v26.11.1
set REPO=%~dp0..
if "%~1"=="" (set USD_ROOT=%USERPROFILE%\dev\openusd) else (set USD_ROOT=%~1)
set PY=%REPO%\.venv\Scripts\python.exe

if not exist "%PY%" (
    echo .venv not found; run "uv sync --all-packages" first.
    exit /b 1
)
"%PY%" -c "import jinja2" 2>nul || (
    echo jinja2 missing from .venv; it ships with the docs group, run "uv sync --all-packages".
    exit /b 1
)

where cl >nul 2>nul
if errorlevel 1 (
    for %%V in (18 2022) do (
        for %%E in (Community Professional Enterprise BuildTools) do (
            if exist "%ProgramFiles%\Microsoft Visual Studio\%%V\%%E\VC\Auxiliary\Build\vcvars64.bat" (
                call "%ProgramFiles%\Microsoft Visual Studio\%%V\%%E\VC\Auxiliary\Build\vcvars64.bat" || exit /b 1
                goto :have_cl
            )
        )
    )
    echo No Visual Studio x64 toolchain found; run from a Developer Command Prompt.
    exit /b 1
)
:have_cl

if not exist "%USD_ROOT%" mkdir "%USD_ROOT%"
if not exist "%USD_ROOT%\src\build_scripts\build_usd.py" (
    git clone --depth 1 --branch %USD_TAG% https://github.com/PixarAnimationStudios/OpenUSD.git "%USD_ROOT%\src" || exit /b 1
)

rem --usd-imaging builds Hd, Storm, and UsdImaging; plain --imaging leaves
rem UsdImaging out. usdview is skipped because .venv has no PyOpenGL.
"%PY%" "%USD_ROOT%\src\build_scripts\build_usd.py" ^
    --build-variant release --usd-imaging --python --no-usdview --materialx ^
    --no-tests --no-examples --no-tutorials --no-docs ^
    --build "%USD_ROOT%\build" --src "%USD_ROOT%\deps" "%USD_ROOT%\inst"
exit /b %errorlevel%
