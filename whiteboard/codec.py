"""笔画点数据的紧凑二进制编码。

白板内容以矢量形式保存，不保存位图。单个笔画的点序列是体积的主要来源，
这里用「量化 + 增量 + zigzag varint」把浮点坐标压成字节流，外层再由
:mod:`whiteboard.store` 统一做 zlib 压缩。

线上（WebSocket / 内存）格式是扁平数组 ``[x0, y0, p0, x1, y1, p1, ...]``，
落盘时转成本模块的字节流并 base64。
"""

from __future__ import annotations

import base64
from typing import Iterable, List

# 坐标量化精度：1 / QUANT 世界像素。1/8 px 对书写来说远超肉眼分辨率。
QUANT = 8
# 压感量化为 1 字节。
PRESSURE_SCALE = 255


def _write_uvarint(out: bytearray, value: int) -> None:
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def _write_svarint(out: bytearray, value: int) -> None:
    # zigzag：把有符号数映射到无符号数，小的负数同样只占一个字节。
    _write_uvarint(out, (value << 1) ^ (value >> 63))


def _read_uvarint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("varint 读取越界")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint 过长")


def _read_svarint(data: bytes, pos: int) -> tuple[int, int]:
    raw, pos = _read_uvarint(data, pos)
    return (raw >> 1) ^ -(raw & 1), pos


def encode_points(flat: Iterable[float]) -> bytes:
    """把 ``[x, y, pressure, ...]`` 编码成字节流。"""
    values = list(flat)
    if len(values) % 3 != 0:
        raise ValueError("点数组长度必须是 3 的倍数")
    count = len(values) // 3
    out = bytearray()
    _write_uvarint(out, count)
    prev_x = 0
    prev_y = 0
    for i in range(count):
        x = round(values[i * 3] * QUANT)
        y = round(values[i * 3 + 1] * QUANT)
        pressure = values[i * 3 + 2]
        _write_svarint(out, x - prev_x)
        _write_svarint(out, y - prev_y)
        prev_x, prev_y = x, y
        clamped = 0.0 if pressure < 0 else (1.0 if pressure > 1 else float(pressure))
        out.append(round(clamped * PRESSURE_SCALE))
    return bytes(out)


def decode_points(data: bytes) -> List[float]:
    """:func:`encode_points` 的逆操作。"""
    count, pos = _read_uvarint(data, 0)
    values: List[float] = []
    x = 0
    y = 0
    for _ in range(count):
        dx, pos = _read_svarint(data, pos)
        dy, pos = _read_svarint(data, pos)
        x += dx
        y += dy
        if pos >= len(data):
            raise ValueError("压感字节缺失")
        pressure = data[pos]
        pos += 1
        values.extend((x / QUANT, y / QUANT, pressure / PRESSURE_SCALE))
    return values


def encode_points_b64(flat: Iterable[float]) -> str:
    return base64.b64encode(encode_points(flat)).decode("ascii")


def decode_points_b64(text: str) -> List[float]:
    return decode_points(base64.b64decode(text.encode("ascii")))
