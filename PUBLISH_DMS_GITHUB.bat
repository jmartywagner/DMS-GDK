@echo off
setlocal EnableExtensions
title DMS-GDK - PUBLICATION GITHUB V1

cd /d "%~dp0"

echo ============================================================
echo   DMS-GDK - PUBLICATION GITHUB V1.0.0
echo ============================================================
echo.
echo Depot cible :
echo https://github.com/jmartywagner/DMS-GDK
echo.
echo Ce script publie le contenu du dossier courant.
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo ERREUR : Git n'est pas installe ou n'est pas dans le PATH.
    echo.
    echo Installe Git for Windows puis relance ce fichier.
    start "" "https://git-scm.com/download/win"
    pause
    exit /b 1
)

if not exist "README.md" (
    echo ERREUR : place PUBLISH_DMS_GITHUB.bat directement dans
    echo le dossier RELEASE_PUBLIC_V1 avant de le lancer.
    pause
    exit /b 1
)

echo [1/6] Initialisation locale...
if exist ".git" rmdir /s /q ".git"
git init -b main
if errorlevel 1 goto :fail

git config user.name "Jonathan Marty-Wagner"
git config user.email "jmartywagner@gmail.com"

echo [2/6] Preparation du depot...
git add -A
if errorlevel 1 goto :fail

echo [3/6] Creation du commit Public V1...
git commit -m "DMS-GDK Public V1.0.0"
if errorlevel 1 goto :fail

echo [4/6] Connexion au depot GitHub...
git remote add origin "https://github.com/jmartywagner/DMS-GDK.git"
if errorlevel 1 goto :fail

echo [5/6] Envoi vers GitHub...
echo.
echo GitHub peut ouvrir une fenetre de connexion la premiere fois.
echo.
git push -u origin main --force
if errorlevel 1 goto :fail

echo [6/6] Verification...
echo.
echo ============================================================
echo   PUBLICATION TERMINEE
echo ============================================================
echo.
echo Depot :
echo https://github.com/jmartywagner/DMS-GDK
echo.
echo IMPORTANT :
echo Le depot est actuellement PRIVE.
echo Apres le smoke test final, ouvre Settings puis General,
echo descends dans Danger Zone et choisis Change repository visibility
echo pour le passer en PUBLIC.
echo.
start "" "https://github.com/jmartywagner/DMS-GDK"
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   ECHEC DE LA PUBLICATION
echo ============================================================
echo.
echo Aucun fichier source DMS original n'a ete modifie.
echo Copie le message d'erreur affiche ci-dessus si tu as besoin d'aide.
echo.
pause
exit /b 1
