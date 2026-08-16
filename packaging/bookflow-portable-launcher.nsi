Unicode true
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true

!include "LogicLib.nsh"

!ifndef APP_EXE_NAME
  !error "APP_EXE_NAME is required"
!endif
!ifndef OUTPUT_LAUNCHER
  !error "OUTPUT_LAUNCHER is required"
!endif
!ifndef LAUNCHER_ICON
  !error "LAUNCHER_ICON is required"
!endif
!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef PRODUCT_VERSION
  !error "PRODUCT_VERSION is required"
!endif

Name "Bookflow Scholar"
OutFile "${OUTPUT_LAUNCHER}"
Icon "${LAUNCHER_ICON}"

VIProductVersion "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=1033 "ProductName" "Bookflow Scholar"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "Bookflow Scholar portable launcher"
VIAddVersionKey /LANG=1033 "CompanyName" "huanghaitck"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright 2026 huanghaitck"

Function EnsureWebView2
  ClearErrors
  SetRegView 64
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
    SetRegView 32
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
    ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  SetRegView 32
  ${If} $0 != ""
    Return
  ${EndIf}

  IfFileExists "$EXEDIR\MicrosoftEdgeWebview2Setup.exe" bootstrap_present bootstrap_missing
  bootstrap_missing:
    MessageBox MB_ICONSTOP|MB_OK "缺少 Microsoft Edge WebView2 Runtime，且便携包中的安装引导程序不完整。请重新下载完整便携包或使用 setup.exe。$\r$\n$\r$\nMicrosoft Edge WebView2 Runtime is missing and the portable package is incomplete. Re-download it or use setup.exe."
    Abort

  bootstrap_present:
    MessageBox MB_ICONINFORMATION|MB_OK "首次运行需要安装 Microsoft Edge WebView2 Runtime。点击确定后将由微软官方安装程序完成。$\r$\n$\r$\nMicrosoft Edge WebView2 Runtime is required. The official Microsoft bootstrapper will install it now."
    nsExec::ExecToLog '"$EXEDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install'
    Pop $0
    ${If} $0 != "0"
    ${AndIf} $0 != "3010"
      MessageBox MB_ICONSTOP|MB_OK "Microsoft Edge WebView2 Runtime 安装失败：$0。请检查网络连接，或改用离线安装程序。$\r$\n$\r$\nInstallation failed: $0. Check the network or use the offline runtime installer."
      Abort
    ${EndIf}
FunctionEnd

Section
  SetShellVarContext current
  Call EnsureWebView2
  IfFileExists "$EXEDIR\${APP_EXE_NAME}" app_present app_missing
  app_missing:
    MessageBox MB_ICONSTOP|MB_OK "便携包不完整：缺少 ${APP_EXE_NAME}。请重新解压完整 ZIP。$\r$\n$\r$\nThe portable package is incomplete. Re-extract the full ZIP archive."
    Abort
  app_present:
    Exec '"$EXEDIR\${APP_EXE_NAME}"'
SectionEnd
