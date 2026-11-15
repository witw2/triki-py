"""
Windows audio control for Triki projects - install with ``pip install triki[windows]``.

Minimal Windows audio control via Core Audio COM (needs: pip install comtypes).

  * list active output devices
  * get / set master volume of the default output device
  * mute / unmute the default microphone
  * press the media volume keys (Windows shows its own volume overlay)
  * change the default output device (undocumented IPolicyConfig, the same
    interface Windows' own sound panel and tools like SoundSwitch use)

Create the AudioController on the thread that will use it and keep all calls
on that thread (COM objects are bound to the thread that created them).
"""

import ctypes
from ctypes import POINTER, Structure, Union, c_float, c_uint, c_ulong, c_ulonglong, c_ushort, c_void_p, c_wchar_p
from ctypes.wintypes import BOOL, DWORD, LPCWSTR

import comtypes
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown, STDMETHOD

E_RENDER = 0
DEVICE_STATE_ACTIVE = 1
ROLES = (0, 1, 2)  # eConsole, eMultimedia, eCommunications


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PV_UNION(Union):
    _fields_ = [("pwszVal", c_wchar_p), ("_pad", c_ulonglong * 2)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("u", _PV_UNION)]


PKEY_Device_FriendlyName = PROPERTYKEY(GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)
VT_LPWSTR = 31


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
    _methods_ = [
        STDMETHOD(HRESULT, "GetCount", [POINTER(DWORD)]),
        STDMETHOD(HRESULT, "GetAt", [DWORD, POINTER(PROPERTYKEY)]),
        STDMETHOD(HRESULT, "GetValue", [POINTER(PROPERTYKEY), POINTER(PROPVARIANT)]),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate",
                  (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "ctx"),
                  (["in"], c_void_p, "params"), (["out"], POINTER(POINTER(IUnknown)), "out")),
        COMMETHOD([], HRESULT, "OpenPropertyStore",
                  (["in"], DWORD, "access"), (["out"], POINTER(POINTER(IPropertyStore)), "store")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p), "id")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "state")),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "count")),
        COMMETHOD([], HRESULT, "Item", (["in"], c_uint, "i"), (["out"], POINTER(POINTER(IMMDevice)), "dev")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_uint, "flow"), (["in"], DWORD, "mask"),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)), "devs")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_uint, "flow"), (["in"], c_uint, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "dev")),
    ]


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = [
        STDMETHOD(HRESULT, "RegisterControlChangeNotify", [c_void_p]),
        STDMETHOD(HRESULT, "UnregisterControlChangeNotify", [c_void_p]),
        STDMETHOD(HRESULT, "GetChannelCount", [POINTER(c_uint)]),
        STDMETHOD(HRESULT, "SetMasterVolumeLevel", [c_float, POINTER(GUID)]),
        STDMETHOD(HRESULT, "SetMasterVolumeLevelScalar", [c_float, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetMasterVolumeLevel", [POINTER(c_float)]),
        STDMETHOD(HRESULT, "GetMasterVolumeLevelScalar", [POINTER(c_float)]),
        STDMETHOD(HRESULT, "SetChannelVolumeLevel", [c_uint, c_float, POINTER(GUID)]),
        STDMETHOD(HRESULT, "SetChannelVolumeLevelScalar", [c_uint, c_float, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetChannelVolumeLevel", [c_uint, POINTER(c_float)]),
        STDMETHOD(HRESULT, "GetChannelVolumeLevelScalar", [c_uint, POINTER(c_float)]),
        STDMETHOD(HRESULT, "SetMute", [BOOL, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetMute", [POINTER(BOOL)]),
    ]


class IPolicyConfig(IUnknown):
    # Undocumented, stable since Windows 7. Only SetDefaultEndpoint is used; the
    # placeholder entries just keep the vtable order right.
    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    _methods_ = [STDMETHOD(HRESULT, name, [c_void_p, c_void_p, c_void_p]) for name in (
        "GetMixFormat", "GetDeviceFormat", "ResetDeviceFormat", "SetDeviceFormat",
        "GetProcessingPeriod", "SetProcessingPeriod", "GetShareMode", "SetShareMode",
        "GetPropertyValue", "SetPropertyValue")] + [
        STDMETHOD(HRESULT, "SetDefaultEndpoint", [LPCWSTR, c_uint]),
        STDMETHOD(HRESULT, "SetEndpointVisibility", [LPCWSTR, BOOL]),
    ]


CLSID_PolicyConfigClient = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")

_ole32 = ctypes.oledll.ole32
_user32 = ctypes.windll.user32
E_CAPTURE = 1
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_EXTENDEDKEY = 0x1
KEYEVENTF_KEYUP = 0x2


class AudioController:
    def __init__(self):
        try:  # make the controller usable from any thread (e.g. a triki handler thread)
            comtypes.CoInitialize()
        except OSError:
            pass  # already initialised in another apartment mode - fine
        self._enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        self._vol = None
        self._vol_dev = None

    @staticmethod
    def _name(dev):
        store = dev.OpenPropertyStore(0)
        pv = PROPVARIANT()
        store.GetValue(ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(pv))
        name = pv.u.pwszVal if pv.vt == VT_LPWSTR else "?"
        _ole32.PropVariantClear(ctypes.byref(pv))
        return name

    def list_outputs(self):
        """[(device_id, friendly_name), ...] for active output devices."""
        coll = self._enum.EnumAudioEndpoints(E_RENDER, DEVICE_STATE_ACTIVE)
        out = []
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            out.append((dev.GetId(), self._name(dev)))
        return out

    def default_output_id(self):
        return self._enum.GetDefaultAudioEndpoint(E_RENDER, 0).GetId()

    def set_default_output(self, device_id):
        policy = comtypes.CoCreateInstance(CLSID_PolicyConfigClient, IPolicyConfig, CLSCTX_ALL)
        for role in ROLES:
            policy.SetDefaultEndpoint(device_id, role)
        self._vol = None  # default changed -> re-acquire volume endpoint

    def _volume_iface(self):
        dev = self._enum.GetDefaultAudioEndpoint(E_RENDER, 0)
        dev_id = dev.GetId()
        if self._vol is None or dev_id != self._vol_dev:
            unk = dev.Activate(ctypes.byref(IAudioEndpointVolume._iid_), CLSCTX_ALL, None)
            self._vol = unk.QueryInterface(IAudioEndpointVolume)
            self._vol_dev = dev_id
        return self._vol

    def get_volume(self):
        v = c_float()
        self._volume_iface().GetMasterVolumeLevelScalar(ctypes.byref(v))
        return v.value

    def set_volume(self, level):
        level = max(0.0, min(1.0, level))
        self._volume_iface().SetMasterVolumeLevelScalar(level, None)
        return level

    # ---- volume keys: Windows applies a 2 % step and shows its own volume overlay
    @staticmethod
    def press_volume_key(up, times=1):
        vk = VK_VOLUME_UP if up else VK_VOLUME_DOWN
        for _ in range(times):
            _user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
            _user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

    # ---- microphone (default capture devices)
    def _mic_ifaces(self):
        """Endpoint volume of the default mic for console and communications (often the same)."""
        ifaces, seen = [], set()
        for role in (0, 2):
            try:
                dev = self._enum.GetDefaultAudioEndpoint(E_CAPTURE, role)
            except comtypes.COMError:
                continue  # no microphone
            dev_id = dev.GetId()
            if dev_id in seen:
                continue
            seen.add(dev_id)
            unk = dev.Activate(ctypes.byref(IAudioEndpointVolume._iid_), CLSCTX_ALL, None)
            ifaces.append((unk.QueryInterface(IAudioEndpointVolume), self._name(dev)))
        return ifaces

    def mic_name(self):
        ifaces = self._mic_ifaces()
        return ifaces[0][1] if ifaces else None

    def get_mic_muted(self):
        ifaces = self._mic_ifaces()
        if not ifaces:
            return None
        muted = BOOL()
        ifaces[0][0].GetMute(ctypes.byref(muted))
        return bool(muted.value)

    def set_mic_muted(self, muted):
        for iface, _ in self._mic_ifaces():
            iface.SetMute(bool(muted), None)

    def toggle_mic(self):
        """Returns the new muted state, or None if there is no microphone."""
        cur = self.get_mic_muted()
        if cur is None:
            return None
        self.set_mic_muted(not cur)
        return not cur


if __name__ == "__main__":
    ac = AudioController()
    default = ac.default_output_id()
    for dev_id, name in ac.list_outputs():
        print("*" if dev_id == default else " ", name, dev_id)
    print("volume:", round(ac.get_volume() * 100), "%")
    print("mic:", ac.mic_name(), "muted" if ac.get_mic_muted() else "live")
