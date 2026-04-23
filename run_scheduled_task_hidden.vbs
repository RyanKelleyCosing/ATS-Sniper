Option Explicit

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

If WScript.Arguments.Count < 4 Then
    WScript.Quit 1
End If

Dim shellExe, scriptPath, workingDir, taskName, command, shell, exitCode

shellExe = WScript.Arguments.Item(0)
scriptPath = WScript.Arguments.Item(1)
workingDir = WScript.Arguments.Item(2)
taskName = WScript.Arguments.Item(3)

command = Quote(shellExe) _
    & " -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File " _
    & Quote(scriptPath) _
    & " -Task " _
    & Quote(taskName)

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = workingDir
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode