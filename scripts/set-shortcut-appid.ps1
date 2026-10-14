# set-shortcut-appid.ps1 -- stamps a .lnk file's System.AppUserModel.ID property.
#
# WScript.Shell (the COM object install-shortcut.ps1 uses to create .lnk files)
# can't set this property -- it only exposes TargetPath/Arguments/IconLocation
# etc. Setting it requires IPropertyStore, which needs a bit of P/Invoke.
#
# Why this matters: without a matching AppUserModelID on both the shortcut and
# the app itself (see tray/main.js's app.setAppUserModelID call), Windows
# derives a taskbar icon identity from whatever process launched the window --
# for Felix that's powershell.exe (via launch-felix.ps1), which is a different
# identity than the Electron window that ends up on screen. Result: pinning
# Felix.lnk to the taskbar and then opening it shows TWO icons (the pin, and
# the running window) instead of one that merges/highlights like every other
# pinned app.
#
# Usage: dot-source or call directly with -Path and -AppId, e.g.:
#   .\set-shortcut-appid.ps1 -Path "C:\Users\you\Desktop\Felix.lnk" -AppId "OpenMind.Felix"

param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$AppId
)

$ErrorActionPreference = "Stop"

if (-not ('ShortcutAppId.Helper' -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace ShortcutAppId {

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey {
        public Guid fmtid;
        public int pid;
        public PropertyKey(Guid fmtid, int pid) { this.fmtid = fmtid; this.pid = pid; }
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PropVariant {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        int GetCount(out uint propertyCount);
        int GetAt(uint propertyIndex, out PropertyKey key);
        int GetValue(ref PropertyKey key, out PropVariant pv);
        int SetValue(ref PropertyKey key, ref PropVariant pv);
        int Commit();
    }

    // CLSID_ShellLink -- the real shell32 CShellLink object, which implements
    // IShellLinkW, IPersistFile, and IPropertyStore all at once. We only need
    // the latter two here; C# COM interop resolves the cast via QueryInterface.
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    public class ShellLinkCoClass { }

    public static class Helper {
        [DllImport("ole32.dll")]
        private static extern int PropVariantClear(ref PropVariant pvar);

        // PKEY_AppUserModel_ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, pid 5
        public static void SetAppId(string shortcutPath, string appId) {
            var link = new ShellLinkCoClass();
            var persistFile = (IPersistFile)link;
            persistFile.Load(shortcutPath, 2); // STGM_READWRITE -- STGM_READ (0) fails Save() with STG_E_ACCESSDENIED

            var store = (IPropertyStore)link;
            var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);

            PropVariant pv = new PropVariant();
            pv.vt = 31; // VT_LPWSTR
            pv.pointerValue = Marshal.StringToCoTaskMemUni(appId);

            int hr = store.SetValue(ref key, ref pv);
            if (hr != 0) throw new Exception("IPropertyStore.SetValue failed: 0x" + hr.ToString("X"));
            store.Commit();

            PropVariantClear(ref pv);

            persistFile.Save(shortcutPath, true);
            Marshal.FinalReleaseComObject(link);
        }
    }
}
"@
}

if (-not (Test-Path $Path)) { throw "Shortcut not found: $Path" }
[ShortcutAppId.Helper]::SetAppId($Path, $AppId)
Write-Host "Set System.AppUserModel.ID = '$AppId' on $Path"
