import base64
import unittest

from utils.encrypt_happ_url import (
    HAPP_LEGACY_MAX_PAYLOAD_BYTES,
    encrypt_happ_url_legacy,
)


class EncryptHappUrlLegacyTests(unittest.TestCase):
    def test_legacy_ciphertext_has_one_rsa_1024_block(self):
        encrypted = encrypt_happ_url_legacy("https://example.com/sub/custom-json")

        self.assertEqual(len(base64.b64decode(encrypted)), 128)
        self.assertEqual(len("happ://crypt/" + encrypted), 185)

    def test_accepts_maximum_payload(self):
        encrypted = encrypt_happ_url_legacy("a" * HAPP_LEGACY_MAX_PAYLOAD_BYTES)

        self.assertEqual(len(base64.b64decode(encrypted)), 128)

    def test_rejects_payload_over_rsa_limit(self):
        with self.assertRaisesRegex(ValueError, "too long"):
            encrypt_happ_url_legacy("a" * (HAPP_LEGACY_MAX_PAYLOAD_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
