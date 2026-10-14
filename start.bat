@echo off
rem Starts the overlay without a console window. Log goes to bot.log next to this file.
cd /d "%~dp0"
start "" pyw -3.12 bot.py overlay %*
