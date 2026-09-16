@echo off
cd /d E:\claude-workspace\invoice-auditor
echo [*] Starting Invoice Auditor Ecosystem...

:: 1. Start PostgreSQL
echo [+] Starting PostgreSQL...
"E:\claude-workspace\tools\pgsql\bin\pg_ctl.exe" -D "E:\claude-workspace\tools\pgsql\data" start

:: 2. Start FastAPI Backend in a new window
echo [+] Launching FastAPI Backend...
start cmd /k "cd /d E:\claude-workspace\invoice-auditor && call .venv\Scripts\activate.bat && python -m uvicorn invoice_auditor.main:create_app --factory --reload --port 8000"

:: 3. Start Vite Frontend in a new window (Entering 'frontend' folder first)
echo [+] Launching Vite Frontend...
start cmd /k "cd /d E:\claude-workspace\invoice-auditor\frontend && npm run dev"

echo [SUCCESS] Sabhi servers launch ho gaye hain!
pause