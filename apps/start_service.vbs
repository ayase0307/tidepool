' AI-Pool service hidden autostart (no console, no window)
' Runs start_service.cmd (pythonw + log redirects) fully hidden.
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\AI-Pool\apps\start_service.cmd""", 0, False
