' Launch PromptPolish silently (no console window).
Option Explicit
Dim fso, shell, base
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base
shell.Run "pythonw.exe """ & base & "\prompt_polish.pyw""", 0, False
