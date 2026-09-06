# pyright: reportUndefinedVariable=false

VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 22, 0, 0),
        prodvers=(1, 22, 0, 0),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,          # VOS_NT_WINDOWS32
        fileType=0x1,        # VFT_APP
        subtype=0x0,
    ),
    kids=[
        StringFileInfo([
            StringTable(
                '040904B0',  # Lang: US English, Charset: Unicode
                [
                    StringStruct('CompanyName', 'Jens Duttke'),
                    StringStruct('FileDescription', 'Claude&CodexUsage'),
                    StringStruct('FileVersion', '1.22.0.0'),
                    StringStruct('InternalName', 'UsageMonitorForClaude'),
                    StringStruct('OriginalFilename', 'Claude&CodexUsage.exe'),
                    StringStruct('ProductName', 'Claude&CodexUsage'),
                    StringStruct('ProductVersion', '1.22.0.0'),
                ],
            ),
        ]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)
