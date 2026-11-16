"""
Windows audio control for Triki projects - install with ``pip install triki[windows]``.

Minimal Windows audio control via Core Audio COM (needs: pip install comtypes).

  * list active output devices
  * get / set master volume of the default output device
  * mute / unmute the default microphone
  * per-application volume (the app that is playing right now)
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


# ---- per-application volume (audio sessions)
class ISimpleAudioVolume(IUnknown):
    _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
    _methods_ = [
        STDMETHOD(HRESULT, "SetMasterVolume", [c_float, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetMasterVolume", [POINTER(c_float)]),
        STDMETHOD(HRESULT, "SetMute", [BOOL, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetMute", [POINTER(BOOL)]),
    ]


class IAudioMeterInformation(IUnknown):
    _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
    _methods_ = [STDMETHOD(HRESULT, "GetPeakValue", [POINTER(c_float)])]


class IAudioSessionControl(IUnknown):
    _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
    _methods_ = [
        STDMETHOD(HRESULT, "GetState", [POINTER(c_uint)]),
        STDMETHOD(HRESULT, "GetDisplayName", [POINTER(c_wchar_p)]),
        STDMETHOD(HRESULT, "SetDisplayName", [LPCWSTR, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetIconPath", [POINTER(c_wchar_p)]),
        STDMETHOD(HRESULT, "SetIconPath", [LPCWSTR, POINTER(GUID)]),
        STDMETHOD(HRESULT, "GetGroupingParam", [POINTER(GUID)]),
        STDMETHOD(HRESULT, "SetGroupingParam", [POINTER(GUID), POINTER(GUID)]),
        STDMETHOD(HRESULT, "RegisterAudioSessionNotification", [c_void_p]),
        STDMETHOD(HRESULT, "UnregisterAudioSessionNotification", [c_void_p]),
    ]


class IAudioSessionControl2(IAudioSessionControl):
    _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
    _methods_ = [
        STDMETHOD(HRESULT, "GetSessionIdentifier", [POINTER(c_wchar_p)]),
        STDMETHOD(HRESULT, "GetSessionInstanceIdentifier", [POINTER(c_wchar_p)]),
        STDMETHOD(HRESULT, "GetProcessId", [POINTER(DWORD)]),
        STDMETHOD(HRESULT, "IsSystemSoundsSession", []),
        STDMETHOD(HRESULT, "SetDuckingPreference", [BOOL]),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        STDMETHOD(HRESULT, "GetCount", [POINTER(ctypes.c_int)]),
        STDMETHOD(HRESULT, "GetSession", [ctypes.c_int, POINTER(POINTER(IAudioSessionControl))]),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        STDMETHOD(HRESULT, "GetAudioSessionControl", [POINTER(GUID), DWORD, c_void_p]),
        STDMETHOD(HRESULT, "GetSimpleAudioVolume", [POINTER(GUID), DWORD, c_void_p]),
        STDMETHOD(HRESULT, "GetSessionEnumerator", [POINTER(POINTER(IAudioSessionEnumerator))]),
    ]


class AppSession:
    """One application's audio on the default output device."""

    def __init__(self, pid, name, volume_iface, meter_iface, active):
        self.pid, self.name, self.active = pid, name, active
        self._vol, self._meter = volume_iface, meter_iface

    @property
    def peak(self):
        v = c_float()
        try:
            self._meter.GetPeakValue(ctypes.byref(v))
        except comtypes.COMError:
            return 0.0
        return v.value

    def get_volume(self):
        v = c_float()
        self._vol.GetMasterVolume(ctypes.byref(v))
        return v.value

    def set_volume(self, level):
        level = max(0.0, min(1.0, level))
        self._vol.SetMasterVolume(level, None)
        return level

    def __repr__(self):
        return f"<AppSession {self.name} pid={self.pid} active={self.active}>"


def _process_name(pid):
    """'Spotify' for pid of Spotify.exe (no extra dependencies)."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return f"pid {pid}"
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = DWORD(520)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = buf.value.rsplit("\\", 1)[-1]
            return name[:-4] if name.lower().endswith(".exe") else name
        return f"pid {pid}"
    finally:
        k32.CloseHandle(h)


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

    # ---- per-application volume
    def app_sessions(self):
        """Audio sessions (apps) on the default output, system sounds excluded."""
        dev = self._enum.GetDefaultAudioEndpoint(E_RENDER, 0)
        mgr = dev.Activate(ctypes.byref(IAudioSessionManager2._iid_), CLSCTX_ALL, None)            .QueryInterface(IAudioSessionManager2)
        enum = POINTER(IAudioSessionEnumerator)()
        mgr.GetSessionEnumerator(ctypes.byref(enum))
        count = ctypes.c_int()
        enum.GetCount(ctypes.byref(count))
        out = []
        for i in range(count.value):
            ctl = POINTER(IAudioSessionControl)()
            enum.GetSession(i, ctypes.byref(ctl))
            ctl2 = ctl.QueryInterface(IAudioSessionControl2)
            if ctl2.IsSystemSoundsSession() == 0:   # S_OK means "is system sounds"
                continue
            pid = DWORD()
            ctl2.GetProcessId(ctypes.byref(pid))
            state = c_uint()
            ctl.GetState(ctypes.byref(state))
            if state.value == 2:                    # expired
                continue
            out.append(AppSession(pid.value, _process_name(pid.value), ctl.QueryInterface(ISimpleAudioVolume),
                                  ctl.QueryInterface(IAudioMeterInformation), state.value == 1))
        return out

    def loudest_app(self):
        """The app that is making the most sound right now (or None)."""
        best, best_peak = None, 0.0
        for sess in self.app_sessions():
            peak = sess.peak if sess.active else 0.0
            if peak > best_peak:
                best, best_peak = sess, peak
        return best

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
