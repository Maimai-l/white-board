import math

from whiteboard import codec


def test_roundtrip_keeps_quantized_precision():
    points = [0.0, 0.0, 0.5, 12.25, -8.125, 1.0, 1000.5, 2400.375, 0.0]
    decoded = codec.decode_points(codec.encode_points(points))
    assert len(decoded) == len(points)
    for original, value in zip(points, decoded):
        assert math.isclose(original, value, abs_tol=1 / codec.QUANT + 1e-9)


def test_pressure_is_clamped():
    decoded = codec.decode_points(codec.encode_points([0, 0, 5.0, 1, 1, -2.0]))
    assert decoded[2] == 1.0
    assert decoded[5] == 0.0


def test_delta_encoding_is_compact():
    # 一条 500 点的连续笔画，平均每点不应超过 4 字节。
    points = []
    for i in range(500):
        points.extend([100 + i * 0.5, 200 + math.sin(i / 10) * 20, 0.6])
    blob = codec.encode_points(points)
    assert len(blob) < 500 * 4


def test_b64_roundtrip():
    points = [1.0, 2.0, 0.25]
    assert codec.decode_points_b64(codec.encode_points_b64(points))[0] == 1.0


def test_rejects_bad_length():
    try:
        codec.encode_points([1.0, 2.0])
    except ValueError:
        return
    raise AssertionError("长度非法时应当报错")
