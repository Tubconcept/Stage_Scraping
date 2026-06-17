@echo off
REM ============================================================================
REM Script d'installation initiale du projet Stage_Scraping (Windows).
REM Étapes : uv sync → playwright install chromium → vérification des imports.
REM Prérequis : uv installé (https://docs.astral.sh/uv/)
REM Après installation : uv run python main.py
REM ============================================================================
chcp 65001 >nul
title Installation des dépendances — Stage Scraping

echo.
echo ============================================
echo   Installation des dependances du projet
echo ============================================
echo.

:: Vérifier que uv est installé
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERREUR] uv n'est pas installe ou pas dans le PATH.
    echo.
    echo Installez uv avec la commande suivante dans PowerShell :
    echo   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo.
    pause
    exit /b 1
)

echo [1/3] Synchronisation des dependances via uv...
uv sync
if %errorlevel% neq 0 (
    echo.
    echo [ERREUR] uv sync a echoue. Verifiez votre connexion internet.
    pause
    exit /b 1
)

echo.
echo [2/3] Installation du navigateur Playwright (Chromium)...
uv run playwright install chromium
if %errorlevel% neq 0 (
    echo.
    echo [ATTENTION] L'installation de Chromium a echoue.
    echo Reessayez manuellement : uv run playwright install chromium
)

echo.
echo [3/3] Verification de l'installation...
uv run python -c "import botasaurus, playwright, bs4, dotenv; print('  Toutes les dependances sont OK')"
if %errorlevel% neq 0 (
    echo.
    echo [ATTENTION] Certaines dependances semblent manquantes.
    echo Reessayez en executant ce fichier une nouvelle fois.
)

echo.
echo ============================================
echo   Installation terminee !
echo   Lancez le projet avec : uv run python main.py
echo ============================================
echo.
pause
