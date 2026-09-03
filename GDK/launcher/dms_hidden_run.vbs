Option Explicit

Dim shell, fso, target, ext, cmd, comspec, pyw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If WScript.Arguments.Count < 1 Then
    WScript.Quit 2
End If

target = WScript.Arguments(0)
ext = LCase(fso.GetExtensionName(target))

If ext = "bat" Or ext = "cmd" Then
    comspec = shell.ExpandEnvironmentStrings("%ComSpec%")
    cmd = Chr(34) & comspec & Chr(34) & " /d /s /c " & Chr(34) & Chr(34) & target & Chr(34) & Chr(34)
    shell.Run cmd, 0, False
    WScript.Quit 0
End If

If ext = "py" Or ext = "pyw" Then
    pyw = "C:\Python314\pythonw.exe"
    If fso.FileExists(pyw) Then
        cmd = Chr(34) & pyw & Chr(34) & " " & Chr(34) & target & Chr(34)
    Else
        cmd = "pyw.exe -3 " & Chr(34) & target & Chr(34)
    End If
    shell.Run cmd, 0, False
    WScript.Quit 0
End If

cmd = Chr(34) & target & Chr(34)
shell.Run cmd, 0, False
