import random

import pytest
from fakes import RT950_IDENT, FakeRadtel

from wokitoki.core.driver import DriverError
from wokitoki.core.transport import TransportTimeout
from wokitoki.drivers.radtel.common import (
    ENCRYPT_STRINGS,
    HandshakeSpec,
    build_encryption_frame,
    open_session,
    xor_crypt,
)
from wokitoki.drivers.radtel.rt950pro import RT950Pro

FAST = HandshakeSpec(magic=b"PROGRAMBT9000U", timeout=0.2)


def test_xor_is_symmetric():
    key = b"CO 7"
    payload = bytes(range(256)) * 2
    assert xor_crypt(xor_crypt(payload, key), key) == payload


def test_xor_skip_rules():
    key = b"A ES"  # 0x41 0x20 0x45 0x53
    assert xor_crypt(b"\x00\xff\x41\xbe", b"AAAA") == b"\x00\xff\x41\xbe"  # 00, FF, k, ~k untouched
    assert xor_crypt(b"\x10\x10\x10\x10", key) == bytes([0x10 ^ 0x41, 0x10, 0x10 ^ 0x45, 0x10 ^ 0x53])


def test_encryption_frame_selects_key_from_its_own_bytes():
    for seed in range(200):
        frame, key = build_encryption_frame(random.Random(seed))
        assert len(frame) == 25 and frame[:4] == b"SEND"
        code = frame[4]
        assert code >> 4 in (1, 2) and code & 0x0F <= 4
        idx = (code - 0x20) * 2 + 1 if code & 0x20 else (code - 0x10) * 2
        assert key == ENCRYPT_STRINGS[frame[5 + idx] % 20]


async def test_handshake_reads_model_and_ident():
    radio = FakeRadtel()
    await radio.open()
    session = await open_session(radio, FAST)
    assert session.model == "RT-950"
    assert session.ident == RT950_IDENT
    assert session.key in ENCRYPT_STRINGS
    assert radio.written[:3] == [b"PROGRAMBT9000U", b"F", b"M"]


async def test_handshake_strips_late_duplicate_ack():
    radio = FakeRadtel(duplicate_ack=True)
    await radio.open()
    session = await open_session(radio, FAST)
    assert session.ident == RT950_IDENT


async def test_handshake_retries_when_first_round_trip_is_swallowed():
    radio = FakeRadtel(ignore_first=1)
    await radio.open()
    session = await open_session(radio, FAST)
    assert session.model == "RT-950"
    assert radio.written.count(b"PROGRAMBT9000U") == 2


async def test_handshake_gives_up():
    radio = FakeRadtel(ignore_first=10)
    await radio.open()
    with pytest.raises(DriverError, match="handshake"):
        await open_session(radio, FAST)


async def test_read_exact_times_out():
    radio = FakeRadtel()
    await radio.open()
    with pytest.raises(TransportTimeout):
        await radio.read_exact(1, 0.05)


async def test_rt950_identify_closes_clone_session():
    radio = FakeRadtel()
    await radio.open()
    info = await RT950Pro().identify(radio)
    assert info.model == "RT-950"
    assert radio.written[-1] == b"E"
