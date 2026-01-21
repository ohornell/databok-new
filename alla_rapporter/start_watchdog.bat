@echo off
REM Startar PDF-namngivning watchdog i bakgrunden
REM Övervakar alla undermappar i alla_rapporter och döper om mappar automatiskt

cd /d "c:\Users\OskarHörnell\databok-new\alla_rapporter"
pythonw rename_pdf.py --auto
