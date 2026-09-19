import plistlib

from whiteboard import profile


def test_icon_is_png():
    png = profile.icon_png(48)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IEND" in png


def test_profile_contains_webclip():
    data = profile.build_profile("http://mac-mini.local:8848/")
    parsed = plistlib.loads(data)
    payload = parsed["PayloadContent"][0]
    assert payload["PayloadType"] == "com.apple.webClip.managed"
    assert payload["URL"] == "http://mac-mini.local:8848/"
    assert payload["FullScreen"] is True
    assert isinstance(payload["Icon"], bytes)


def test_uuid_is_stable_per_url():
    first = plistlib.loads(profile.build_profile("http://a.local:1/"))
    again = plistlib.loads(profile.build_profile("http://a.local:1/"))
    other = plistlib.loads(profile.build_profile("http://b.local:1/"))
    assert first["PayloadUUID"] == again["PayloadUUID"]
    assert first["PayloadUUID"] != other["PayloadUUID"]
