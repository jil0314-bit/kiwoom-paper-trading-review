Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
entryRel = "\SOURCE\core\bot_v76_00_three_stage_ctrlv_paper.py"

Function HasEntry(folderPath)
  On Error Resume Next
  HasEntry = fso.FileExists(folderPath & entryRel)
  On Error GoTo 0
End Function

Function SearchDown(folderPath, depth)
  On Error Resume Next
  Dim folder, child, found, nameUpper
  SearchDown = ""
  If depth < 0 Then Exit Function
  If HasEntry(folderPath) Then
    SearchDown = folderPath
    Exit Function
  End If
  If Not fso.FolderExists(folderPath) Then Exit Function
  Set folder = fso.GetFolder(folderPath)
  For Each child In folder.SubFolders
    nameUpper = UCase(child.Name)
    If Left(nameUpper, 4) = "V76_" Or nameUpper = "SOURCE" Then
      found = SearchDown(child.Path, depth - 1)
      If found <> "" Then
        SearchDown = found
        Exit Function
      End If
    End If
  Next
  On Error GoTo 0
End Function

Function FindValidBase(startFolder)
  On Error Resume Next
  Dim folderPath, parentPath, found, folder, child, i, nameUpper
  FindValidBase = ""
  found = SearchDown(startFolder, 4)
  If found <> "" Then
    FindValidBase = found
    Exit Function
  End If
  folderPath = startFolder
  For i = 1 To 5
    parentPath = fso.GetParentFolderName(folderPath)
    If parentPath = "" Or parentPath = folderPath Then Exit For
    If HasEntry(parentPath) Then
      FindValidBase = parentPath
      Exit Function
    End If
    If fso.FolderExists(parentPath) Then
      Set folder = fso.GetFolder(parentPath)
      For Each child In folder.SubFolders
        nameUpper = UCase(child.Name)
        If Left(nameUpper, 4) = "V76_" Then
          found = SearchDown(child.Path, 3)
          If found <> "" Then
            FindValidBase = found
            Exit Function
          End If
        End If
      Next
    End If
    folderPath = parentPath
  Next
  On Error GoTo 0
End Function

validBase = FindValidBase(base)

If validBase <> "" Then
  base = validBase
End If

sourceDir = base & "\SOURCE"

If Not fso.FileExists(sourceDir & "\core\bot_v76_00_three_stage_ctrlv_paper.py") Then
  MsgBox "압축을 먼저 완전히 푼 뒤 실행하세요." & vbCrLf & vbCrLf & _
         "SOURCE 폴더 또는 실행 핵심 파일을 찾을 수 없습니다." & vbCrLf & _
         "현재 위치: " & base & vbCrLf & vbCrLf & _
         "방법: 새 ZIP을 빈 새 폴더에 압축 풀기 > 풀린 폴더 안의 3_START_V76_GUI.vbs 실행", _
         48, "V76 실행 방법"
  WScript.Quit 2
End If

shell.CurrentDirectory = sourceDir

Function RunHidden(command)
  On Error Resume Next
  Err.Clear
  RunHidden = shell.Run(command, 0, True)
  If Err.Number <> 0 Then
    RunHidden = 9009
    Err.Clear
  End If
  On Error GoTo 0
End Function

' Kiwoom is HIGH (administrator) on this PC.  Start the exact installed Python
' launcher with the same integrity so Windows does not reject 0600 input with
' error 5.  PowerShell Start-Process reliably displays the Windows UAC prompt.
pyExe = shell.ExpandEnvironmentStrings("%SystemRoot%\py.exe")
launcherPath = sourceDir & "\launcher.py"
psSource = Replace(sourceDir, "'", "''")
psPy = Replace(pyExe, "'", "''")
psLauncher = Replace(launcherPath, "'", "''")
psCommand = "$arg='-3 ' + [char]34 + '" & psLauncher & _
            "' + [char]34;" & _
            "$p=Start-Process -FilePath '" & psPy & _
            "' -ArgumentList $arg -WorkingDirectory '" & _
            psSource & "' -Verb RunAs -WindowStyle Hidden -Wait -PassThru; " & _
            "exit $p.ExitCode"
rc = RunHidden("powershell.exe -NoProfile -WindowStyle Hidden -Command " & _
               Chr(34) & psCommand & Chr(34))

If rc <> 0 And rc <> -1 Then
  MsgBox "V76 대시보드를 시작하지 못했습니다." & vbCrLf & _
         "관리자 권한 질문에서 '예'를 눌러주세요." & vbCrLf & _
         "종료코드: " & rc & vbCrLf & _
         "로그: " & sourceDir & "\runtime\logs\v75_73_dashboard.log", _
         16, "V76 시작 오류"
End If
