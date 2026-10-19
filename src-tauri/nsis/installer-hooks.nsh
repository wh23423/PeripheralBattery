; PeripheralBattery NSIS installer / uninstaller hooks.
;
; Wired through tauri.conf.json:
;   bundle.windows.nsis.installerHooks = "nsis/installer-hooks.nsh"
;
; Hook points of tauri-bundler's installer.nsi template:
;   NSIS_HOOK_PREINSTALL    - start of Section Install, before copying files
;   NSIS_HOOK_POSTINSTALL   - end of Section Install
;   NSIS_HOOK_PREUNINSTALL  - start of Section Uninstall, before deleting files
;   NSIS_HOOK_POSTUNINSTALL - end of Section Uninstall, after the template cleanup
;
; Defines available from the template when a macro is inserted:
;   ${PRODUCTNAME} ${MANUFACTURER} ${MANUPRODUCTKEY} ${MANUKEY} ${BUNDLEID}
;
; Data layout since v0.1.1 (keep in sync with src-tauri/src/paths.rs):
;   user data        %APPDATA%\PeripheralBattery          (config/app/aliases/history/logs)
;   legacy data      %LOCALAPPDATA%\PeripheralBattery    (v0.1.0 data dir = old install dir)
;   WebView2 profile %LOCALAPPDATA%\com.peripheralbattery.app (cache, always removed)
; The default install dir stays %LOCALAPPDATA%\PeripheralBattery but only holds
; program files now, so user data is no longer wiped by an uninstall.
;
; When the uninstaller is launched with /UPDATE (in-place update) the template
; keeps data/shortcuts; this hook skips its destructive cleanup as well. The
; interactive reinstall/uninstall path (UpdateMode=0) does run the cleanup.
;
; Silent uninstall semantics:
;   uninstall.exe /S                    -> app files + registry + WebView2 profile removed,
;                                          user data (%APPDATA%\PeripheralBattery) KEPT
;   uninstall.exe /S /DELETEAPPDATA     -> ... user data removed too
; Interactive uninstall: the "delete app data" checkbox controls user data.
;
; NOTE: the template's own "delete app data" branch only fires when the
; checkbox is checked, and it deletes %LOCALAPPDATA%\com.peripheralbattery.app
; (the WebView2 profile), not the real data dir. This hook fixes both issues.

!define PB_LEGACY_DATA_DIR "$LOCALAPPDATA\PeripheralBattery"
!define PB_DATA_DIR "$APPDATA\PeripheralBattery"

!macro NSIS_HOOK_PREINSTALL
  ; v0.1.0 stored the install location under vendor "peripheralbattery" (the
  ; same id we keep as bundle.publisher for drop-in upgrades, so the registry
  ; key is unchanged between 0.1.0 and 0.1.1). Only if a future release changes
  ; the publisher, clean the stale legacy key here.
  !if "${MANUFACTURER}" != "peripheralbattery"
    DeleteRegKey HKCU "Software\peripheralbattery\PeripheralBattery"
    DeleteRegKey /ifempty HKCU "Software\peripheralbattery"
  !endif
  DetailPrint "PeripheralBattery: pre-install checks done"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  DetailPrint "PeripheralBattery: installed; user data dir is ${PB_DATA_DIR}"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "PeripheralBattery: uninstalling (user data handled after file removal)"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    SetShellVarContext current

    ; ------------------------------------------------------------------
    ; 1) What did the user ask for?
    ;    Interactive: "delete app data" checkbox (v0.1.0 template variable).
    ;    Silent / passive: /DELETEAPPDATA command line flag.
    ; ------------------------------------------------------------------
    StrCpy $R8 0
    ${If} $DeleteAppDataCheckboxState = 1
      StrCpy $R8 1
    ${EndIf}
    ${GetOptions} $CMDLINE "/DELETEAPPDATA" $R9
    ${IfNot} ${Errors}
      StrCpy $R8 1
    ${EndIf}

    ; ------------------------------------------------------------------
    ; 2) Keep v0.1.0 settings when the user did NOT ask to delete data.
    ;    v0.1.0 kept app.json/config.json/aliases.json/history.json in the
    ;    install dir, which the uninstaller removes before the new release
    ;    can migrate them (paths.rs migration runs on first app start).
    ;    Copy them to the new data dir when the target is missing.
    ; ------------------------------------------------------------------
    ${If} $R8 <> 1
      ${If} ${FileExists} "${PB_LEGACY_DATA_DIR}\app.json"
        CreateDirectory "${PB_DATA_DIR}"
        ${IfNot} ${FileExists} "${PB_DATA_DIR}\app.json"
          CopyFiles /SILENT "${PB_DATA_DIR}" "${PB_LEGACY_DATA_DIR}\app.json"
        ${EndIf}
      ${EndIf}
      ${If} ${FileExists} "${PB_LEGACY_DATA_DIR}\config.json"
        CreateDirectory "${PB_DATA_DIR}"
        ${IfNot} ${FileExists} "${PB_DATA_DIR}\config.json"
          CopyFiles /SILENT "${PB_DATA_DIR}" "${PB_LEGACY_DATA_DIR}\config.json"
        ${EndIf}
      ${EndIf}
      ${If} ${FileExists} "${PB_LEGACY_DATA_DIR}\aliases.json"
        CreateDirectory "${PB_DATA_DIR}"
        ${IfNot} ${FileExists} "${PB_DATA_DIR}\aliases.json"
          CopyFiles /SILENT "${PB_DATA_DIR}" "${PB_LEGACY_DATA_DIR}\aliases.json"
        ${EndIf}
      ${EndIf}
      ${If} ${FileExists} "${PB_LEGACY_DATA_DIR}\history.json"
        CreateDirectory "${PB_DATA_DIR}"
        ${IfNot} ${FileExists} "${PB_DATA_DIR}\history.json"
          CopyFiles /SILENT "${PB_DATA_DIR}" "${PB_LEGACY_DATA_DIR}\history.json"
        ${EndIf}
      ${EndIf}
      DetailPrint "PeripheralBattery: user data kept at ${PB_DATA_DIR}"
    ${Else}
      DetailPrint "PeripheralBattery: user data removal requested"
    ${EndIf}

    ; ------------------------------------------------------------------
    ; 3) WebView2 profile/cache: always removed, it holds no user settings.
    ;    (%LOCALAPPDATA%\com.peripheralbattery.app is ~70MB after a few runs)
    ; ------------------------------------------------------------------
    RMDir /r "$LOCALAPPDATA\${BUNDLEID}"
    RMDir /r "$APPDATA\${BUNDLEID}"

    ; ------------------------------------------------------------------
    ; 4) Real user data: only when requested (checkbox or /DELETEAPPDATA).
    ; ------------------------------------------------------------------
    ${If} $R8 = 1
      ${If} "$APPDATA" != ""
        RMDir /r "${PB_DATA_DIR}"
      ${EndIf}
      ; v0.1.0 data dir; when the app used the default install dir this is
      ; the same path as $INSTDIR and is removed by the block below.
      ${If} "${PB_LEGACY_DATA_DIR}" != "$INSTDIR"
        RMDir /r "${PB_LEGACY_DATA_DIR}"
      ${EndIf}
      DetailPrint "PeripheralBattery: user data removed"
    ${EndIf}

    ; ------------------------------------------------------------------
    ; 5) Registry: always remove the current publisher/vendor key AND, if a
    ;    future release changes the publisher, the 0.1.0 vendor key too.
    ;    (review P2-02: the template deletes these only when the "delete app
    ;    data" checkbox is checked.)
    ; ------------------------------------------------------------------
    DeleteRegKey HKCU "${MANUPRODUCTKEY}"
    DeleteRegKey /ifempty HKCU "${MANUKEY}"
    !if "${MANUFACTURER}" != "peripheralbattery"
      DeleteRegKey HKCU "Software\peripheralbattery\PeripheralBattery"
      DeleteRegKey /ifempty HKCU "Software\peripheralbattery"
    !endif

    ; ------------------------------------------------------------------
    ; 5b) F-01 autostart: the app writes "PeripheralBattery" under the
    ;     per-user Run key when the user enables "start at login". Windows
    ;     also records a StartupApproved marker in a separate key when the
    ;     user toggles the entry in Task Manager / Settings. Remove both so
    ;     an uninstall leaves no orphaned startup entry behind (a leftover
    ;     StartupApproved value would otherwise make a future reinstall
    ;     look "disabled by the system" before the user ever touched it).
    ;     Both keys are HKCU-only: no elevation required.
    ; ------------------------------------------------------------------
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PeripheralBattery"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "PeripheralBattery"

    ; ------------------------------------------------------------------
    ; 6) Install dir leftovers (v0.1.0 kept config/logs here, so the
    ;    template's non-recursive RMDir leaves the folder behind).
    ;    Guarded: never recurse into a filesystem/user/system root.
    ; ------------------------------------------------------------------
    StrLen $R7 "$INSTDIR"
    ${If} $R7 > 3
    ${AndIf} "$INSTDIR" != "$LOCALAPPDATA"
    ${AndIf} "$INSTDIR" != "$APPDATA"
    ${AndIf} "$INSTDIR" != "$PROFILE"
    ${AndIf} "$INSTDIR" != "$PROGRAMFILES"
    ${AndIf} "$INSTDIR" != "$PROGRAMFILES64"
      SetOutPath "$TEMP"
      RMDir /r "$INSTDIR"
    ${EndIf}
  ${EndIf}
!macroend
