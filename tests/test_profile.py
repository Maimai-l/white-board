import plistlib

from whiteboard import profile


def test_icon_is_png():
    png = profile.icon_png(48)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IEND" in png


def test_mac_icon_follows_apples_template():
    """macOS 图标必须在 1024 画布里只画 824 的方块，否则程序坞里会大一圈。"""
    from whiteboard.profile import MAC_TILE_RATIO

    size = 128
    png = profile.icon_png(size, mac=True)
    assert png.startswith(b"\x89PNG")

    # 边角必须透明（留白），中心必须不透明
    pixels = _decode_rgba(png, size)
    assert pixels[(2, 2)][3] == 0
    assert pixels[(size // 2, size // 2)][3] == 255
    margin = round(size * (1 - MAC_TILE_RATIO) / 2)
    assert pixels[(margin - 2, size // 2)][3] == 0, "方块左边应当还有留白"
    assert pixels[(margin + 3, size // 2)][3] > 0, "留白之后就该是方块了"

    # iPad 主屏图标相反：铺满整张画布
    ios = _decode_rgba(profile.icon_png(size), size)
    assert ios[(1, size // 2)][3] > 0


def _decode_rgba(png: bytes, size: int):
    """解出 PNG 的像素（只支持本项目生成的 8 位 RGBA 无过滤图）。"""
    import struct
    import zlib

    pos = 8
    data = b""
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos : pos + 8])
        chunk = png[pos + 8 : pos + 8 + length]
        if kind == b"IDAT":
            data += chunk
        pos += 12 + length
    raw = zlib.decompress(data)
    stride = size * 4 + 1
    pixels = {}
    for y in range(size):
        row = raw[y * stride + 1 : (y + 1) * stride]
        for x in range(size):
            pixels[(x, y)] = tuple(row[x * 4 : x * 4 + 4])
    return pixels


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
