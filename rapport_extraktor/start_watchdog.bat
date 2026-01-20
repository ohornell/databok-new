@echo off
REM Startar PDF-namngivning watchdog i bakgrunden
REM Övervakar vitrolife_reports mappen

cd /d "c:\Users\OskarHörnell\databok-new\rapport_extraktor"
pythonw rename_pdf.py "c:\Users\OskarHörnell\databok-new\vitrolife_reports" --watch
