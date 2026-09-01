from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
import base64

HAPP_PUBLIC_KEY_V3 = """
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAlBetA0wjbaj+h7oJ/d/h
pNrXvAcuhOdFGEFcfCxSWyLzWk4SAQ05gtaEGZyetTax2uqagi9HT6lapUSUe2S8
nMLJf5K+LEs9TYrhhBdx/B0BGahA+lPJa7nUwp7WfUmSF4hir+xka5ApHjzkAQn6
cdG6FKtSPgq1rYRPd1jRf2maEHwiP/e/jqdXLPP0SFBjWTMt/joUDgE7v/IGGB0L
Q7mGPAlgmxwUHVqP4bJnZ//5sNLxWMjtYHOYjaV+lixNSfhFM3MdBndjpkmgSfmg
D5uYQYDL29TDk6Eu+xetUEqry8ySPjUbNWdDXCglQWMxDGjaqYXMWgxBA1UKjUBW
wbgr5yKTJ7mTqhlYEC9D5V/LOnKd6pTSvaMxkHXwk8hBWvUNWAxzAf5JZ7EVE3jt
0j682+/hnmL/hymUE44yMG1gCcWvSpB3BTlKoMnl4yrTakmdkbASeFRkN3iMRewa
IenvMhzJh1fq7xwX94otdd5eLB2vRFavrnhOcN2JJAkKTnx9dwQwFpGEkg+8U613
+Tfm/f82l56fFeoFN98dD2mUFLFZoeJ5CG81ZeXrH83niI0joX7rtoAZIPWzq3Y1
Zb/Zq+kK2hSIhphY172Uvs8X2Qp2ac9UoTPM71tURsA9IvPNvUwSIo/aKlX5KE3I
VE0tje7twWXL5Gb1sfcXRzsCAwEAAQ==
-----END PUBLIC KEY-----
"""

HAPP_PUBLIC_KEY_LEGACY = """
-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCxsS7PUq1biQlVD92rf6eXKr9o
G1/SrYx3qWahZP+Jq35m4Wb/Z+mB6eBWrPzJ/zZpZLWLQorcvOKt+sLaCHyH1HLN
kti4jlaEQX6x97XgBm8GK08+lLLWquFDhWRNxsrfzJyNdpVopzBRmCJKTc8ObYyP
brv9T35a8Kd5WqjnUwIDAQAB
-----END PUBLIC KEY-----
"""

# RSA-1024 with PKCS#1 v1.5 reserves 11 bytes for padding.
HAPP_LEGACY_MAX_PAYLOAD_BYTES = 117


def _encrypt(url: str, public_key_pem: str) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    encrypted = public_key.encrypt(url.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()


def encrypt_happ_url(url: str) -> str:
    """Encrypt a URL using Happ crypt3 (RSA-4096)."""
    return _encrypt(url, HAPP_PUBLIC_KEY_V3)


def encrypt_happ_url_legacy(url: str) -> str:
    """Encrypt a short URL using Happ crypt (RSA-1024)."""
    payload_length = len(url.encode("utf-8"))
    if payload_length > HAPP_LEGACY_MAX_PAYLOAD_BYTES:
        raise ValueError(
            "Happ legacy crypt payload is too long: "
            f"{payload_length} > {HAPP_LEGACY_MAX_PAYLOAD_BYTES} bytes"
        )
    return _encrypt(url, HAPP_PUBLIC_KEY_LEGACY)
