' launch-felix-hidden.vbs -- truly silent launcher for launch-felix.ps1.
'
' Passing -WindowStyle Hidden to powershell.exe still lets Windows create and
' briefly show a console window before PowerShell's own startup code gets
' around to applying that flag -- a well-known race that causes a visible
' console flash on some systems. WshShell.Run's window-style parameter
' (0 = SW_HIDE) is applied by the OS at process-creation time instead, so no
' window is ever shown in the first place.
Dim fso, scriptDir, psPath, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psPath = fso.BuildPath(scriptDir, "launch-felix.ps1")

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & psPath & """"

CreateObject("WScript.Shell").Run cmd, 0, False
