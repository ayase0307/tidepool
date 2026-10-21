' AI-Pool desktop console launcher (fully hidden, no console window)
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
pythonw = "C:\AI-Pool\venv\Scripts\pythonw.exe"
script = "C:\AI-Pool\apps\pool_desktop.py"
If Not fso.FileExists(pythonw) Then pythonw = "pythonw.exe"
sh.Run """" & pythonw & """ """ & script & """", 0, False
