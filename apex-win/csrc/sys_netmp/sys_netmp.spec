; sys_netmp Wine DLL export specification
;
; This file tells Wine which functions to export from the DLL.
; Format: ordinal stub_name [handler]

@ stdcall HandleIoctl(ptr long ptr long ptr long ptr)
