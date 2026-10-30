"""Checks for the RAM-only AX88772B device-table patch generator."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wifi-sta"))
import patch_asix_772b as patcher  # noqa: E402


def image(entry):
    data = bytearray(patcher.OFFSET + 0x100)
    data[patcher.OFFSET:patcher.OFFSET + 16] = entry
    return bytes(data)


class PatchAsixTest(unittest.TestCase):
    def test_rewrites_only_vendor_and_device(self):
        data = image(patcher.ORIGINAL)
        out = patcher.patch(data)
        self.assertEqual(len(out), len(data))
        vid, did, chip, flags = struct.unpack_from("<IIII", out, patcher.OFFSET)
        self.assertEqual((vid, did, chip, flags), (0x0b95, 0x772b, 0x88772b, 0x80))
        changed = [i for i in range(len(data)) if data[i] != out[i]]
        self.assertTrue(all(patcher.OFFSET <= i < patcher.OFFSET + 8 for i in changed))

    def test_refuses_unexpected_bytes(self):
        with self.assertRaises(ValueError):
            patcher.patch(image(b"\0" * 16))

    def test_refuses_repeat(self):
        with self.assertRaises(ValueError):
            patcher.patch(patcher.patch(image(patcher.ORIGINAL)))


if __name__ == "__main__":
    unittest.main()
