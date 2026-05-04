@echo off
setlocal

set "PYTHON_HOME=C:\Users\28217\AppData\Local\Programs\Python\Python312"
set "TCL_LIBRARY=%PYTHON_HOME%\tcl\tcl8.6"
set "TK_LIBRARY=%PYTHON_HOME%\tcl\tk8.6"
set "PATH=%PYTHON_HOME%;%PYTHON_HOME%\DLLs;%PATH%"

cd /d "%~dp0"
python overlay_slideshow.py %*
