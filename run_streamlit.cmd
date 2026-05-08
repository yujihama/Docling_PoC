@echo off
cd /d "%~dp0"
if "%STREAMLIT_PORT%"=="" set STREAMLIT_PORT=8501
".venv\Scripts\python.exe" -m streamlit run app.py --server.port %STREAMLIT_PORT% --server.headless true --browser.gatherUsageStats false > streamlit.stdout.log 2> streamlit.stderr.log
