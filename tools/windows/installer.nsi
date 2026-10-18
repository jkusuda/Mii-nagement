; The Windows installer, Mii-nagement-Setup.exe (tools/package_windows.sh builds it with NSIS).
; A per-user install, no admin rights: %LOCALAPPDATA%\Programs\Mii-nagement, a Start menu and a
; desktop shortcut, and an entry in Settings > Apps to uninstall it. Islands and their git
; repositories are the user's: uninstalling leaves them where they are.
; Defines from the build script: VERSION, STAGE (the folder to install), OUTFILE, ICON.

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user
ManifestDPIAware true

!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APP "Mii-nagement"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
BrandingText "${APP} ${VERSION}"

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "Your island of coding agents, ready to go: everything it needs comes with it (its own Python, Git and Node).$\r$\n$\r$\nResidents think with the AI you already use. Log in to one of these once, outside the app:$\r$\n  - Claude Code: run `claude` and log in$\r$\n  - Codex: run `codex login`$\r$\n  - Gemini CLI: run `gemini` and log in$\r$\nor save an OpenRouter key in the app's Settings.$\r$\n$\r$\nFor GitHub issues and pull requests, install the GitHub CLI (`gh`) and run `gh auth login`."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP}.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP}"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "${APP} will be removed. Your islands, their git repositories and your settings stay on this computer."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; A running copy keeps its files open: close it, its backend and the agents it started.
!macro StopRunningCopy
  nsExec::Exec 'taskkill /IM "${APP}.exe" /T /F'
  nsExec::Exec `powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\runtime\*' } | Stop-Process -Force"`
!macroend

Section "Install"
  !insertmacro StopRunningCopy
  SetOutPath "$INSTDIR"
  ; An update replaces these folders whole, so no stale files stay behind. Only when this
  ; folder holds an earlier Mii-nagement: never empty folders that happen to share the name.
  IfFileExists "$INSTDIR\${APP}.exe" 0 +3
    RMDir /r "$INSTDIR\backend"
    RMDir /r "$INSTDIR\runtime"
  File /r "${STAGE}\*"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${APP}.exe"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${APP}.exe"

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP}.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" $0
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !insertmacro StopRunningCopy
  Delete "$INSTDIR\${APP}.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\runtime"
  RMDir "$INSTDIR"  ; only if empty: anything else the user put there stays
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
