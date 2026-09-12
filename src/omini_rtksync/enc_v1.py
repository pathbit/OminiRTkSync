"""Decifra e cifra campos `enc:v1:` do armazenamento do OmniRoute.

O OmniRoute cifra credenciais em repouso com AES-256-GCM. Formato (medido no
bundle do servidor):

    enc:v1:<iv_hex>:<ciphertext_hex>:<auth_tag_hex>

Derivação da chave (linha exata do bundle: scryptSync(key, contexto, 32)):

    chave32 = scrypt(senha=STORAGE_ENCRYPTION_KEY,
                     sal="omniroute-field-encryption-v1",
                     n=16384, r=8, p=1, dklen=32)

O sal é o CONTEXTO estático acima — o sha256 da chave só aparece no caminho
legado de migração, não no formato vigente. A chave chega por variável de
ambiente e nunca vai a log: falhas devolvem None ou o valor original, sem
detalhe do material.
"""

from __future__ import annotations

import hashlib
import os
import secrets as _secrets

from Crypto.Cipher import AES

PREFIX = "enc:v1:"
CONTEXT_SALT = b"omniroute-field-encryption-v1"


def _derive_key(secret: str) -> bytes:
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=CONTEXT_SALT,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def decrypt(value: str, *, secret: str | None = None) -> str | None:
    """Decifra um valor `enc:v1:`; None quando não decifra (nunca levanta).

    Valores fora do formato passam intactos (passthrough), como o próprio
    OmniRoute faz quando a chave está ausente.
    """

    if not is_encrypted(value):
        return value
    secret = secret if secret is not None else os.environ.get("STORAGE_ENCRYPTION_KEY", "")
    if not secret:
        return None
    parts = value[len(PREFIX) :].split(":")
    if len(parts) != 3:
        return None
    iv_hex, ciphertext_hex, tag_hex = parts
    try:
        iv = bytes.fromhex(iv_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        tag = bytes.fromhex(tag_hex)
        cipher = AES.new(_derive_key(secret), AES.MODE_GCM, nonce=iv)
        plaintext = cipher.decrypt(ciphertext)
        cipher.verify(tag)
        return plaintext.decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def decrypt_if_needed(value: str) -> str:
    """Atalho: decifra quando cifrado; devolve o original quando ilegível."""

    result = decrypt(value)
    return result if result is not None else value


def encrypt(value: str, *, secret: str | None = None) -> str:
    """Cifra no formato `enc:v1:`; sem chave, devolve o original (passthrough)."""

    if not value or is_encrypted(value):
        return value
    secret = secret if secret is not None else os.environ.get("STORAGE_ENCRYPTION_KEY", "")
    if not secret:
        return value
    iv = _secrets.token_bytes(16)
    cipher = AES.new(_derive_key(secret), AES.MODE_GCM, nonce=iv)
    ciphertext, tag = cipher.encrypt_and_digest(value.encode("utf-8"))
    return f"{PREFIX}{iv.hex()}:{ciphertext.hex()}:{tag.hex()}"
