Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef APP_EXE
  !error "APP_EXE is required"
!endif
!ifndef SIDECAR_DIR
  !error "SIDECAR_DIR is required"
!endif
!ifndef DEFAULT_CONFIG
  !error "DEFAULT_CONFIG is required"
!endif
!ifndef INSTALLER_ICON
  !error "INSTALLER_ICON is required"
!endif
!ifndef OUTPUT_INSTALLER
  !error "OUTPUT_INSTALLER is required"
!endif
!ifndef PUBLISHER
  !error "PUBLISHER is required"
!endif

Name "Bookflow Scholar"
Caption "Bookflow Scholar 0.8.0-rc.2"
OutFile "${OUTPUT_INSTALLER}"
InstallDir "$LOCALAPPDATA\Programs\Bookflow Scholar"
InstallDirRegKey HKCU "Software\Bookflow Scholar" "InstallLocation"
Icon "${INSTALLER_ICON}"
UninstallIcon "${INSTALLER_ICON}"
BrandingText "Bookflow Scholar · ${PUBLISHER}"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "0.8.0.2"
VIAddVersionKey /LANG=1033 "ProductName" "Bookflow Scholar"
VIAddVersionKey /LANG=1033 "ProductVersion" "0.8.0-rc.2"
VIAddVersionKey /LANG=1033 "FileVersion" "0.8.0.2"
VIAddVersionKey /LANG=1033 "CompanyName" "${PUBLISHER}"
VIAddVersionKey /LANG=1033 "FileDescription" "Bookflow Scholar current-user installer"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright 2026 ${PUBLISHER}"

!define MUI_ABORTWARNING
!define MUI_ICON "${INSTALLER_ICON}"
!define MUI_UNICON "${INSTALLER_ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Bookflow Scholar.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 Bookflow Scholar"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  SetShellVarContext current
FunctionEnd

Function un.onInit
  SetShellVarContext current
FunctionEnd

Function EnsureWebView2
  ClearErrors
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
    ReadRegStr $0 HKLM "Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 != ""
    DetailPrint "Microsoft Edge WebView2 Runtime detected: $0"
    Return
  ${EndIf}

  DetailPrint "Downloading the Microsoft Edge WebView2 bootstrapper..."
  NSISdl::download /TIMEOUT=60000 \
    "https://go.microsoft.com/fwlink/p/?LinkId=2124703" \
    "$TEMP\MicrosoftEdgeWebview2Setup.exe"
  Pop $0
  ${If} $0 != "success"
    MessageBox MB_ICONSTOP|MB_OK "无法下载 Microsoft Edge WebView2 Runtime：$0"
    Abort
  ${EndIf}
  nsExec::ExecToLog '"$TEMP\MicrosoftEdgeWebview2Setup.exe" /silent /install'
  Pop $0
  Delete "$TEMP\MicrosoftEdgeWebview2Setup.exe"
  ${If} $0 != "0"
    MessageBox MB_ICONSTOP|MB_OK "Microsoft Edge WebView2 Runtime 安装失败：$0"
    Abort
  ${EndIf}
FunctionEnd

Section "Bookflow Scholar" SecMain
  SectionIn RO
  Call EnsureWebView2

  SetOutPath "$INSTDIR"
  File "/oname=Bookflow Scholar.exe" "${APP_EXE}"

  SetOutPath "$INSTDIR\bookflow-sidecar"
  File /r "${SIDECAR_DIR}\*.*"

  SetOutPath "$INSTDIR\defaults"
  File /oname=providers.yaml "${DEFAULT_CONFIG}"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Bookflow Scholar" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "DisplayName" "Bookflow Scholar"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "DisplayVersion" "0.8.0-rc.2"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar" \
    "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\Bookflow Scholar"
  CreateShortcut "$SMPROGRAMS\Bookflow Scholar\Bookflow Scholar.lnk" \
    "$INSTDIR\Bookflow Scholar.exe"
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\Bookflow Scholar\Bookflow Scholar.lnk"
  RMDir "$SMPROGRAMS\Bookflow Scholar"
  Delete "$INSTDIR\Bookflow Scholar.exe"
  Delete "$INSTDIR\defaults\providers.yaml"
  RMDir "$INSTDIR\defaults"
  RMDir /r "$INSTDIR\bookflow-sidecar"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Bookflow Scholar"
  DeleteRegKey HKCU "Software\Bookflow Scholar"
SectionEnd
