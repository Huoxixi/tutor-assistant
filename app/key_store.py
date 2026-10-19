"""Local model credentials encrypted by the current Windows user's DPAPI."""
import os
import threading
import uuid
from pathlib import Path


class KeyStoreError(Exception):
    pass


def _protect(data):
    try:
        import win32crypt
        return win32crypt.CryptProtectData(data, "Tutor Assistant model key", None, None, None, 1)
    except Exception:
        raise KeyStoreError("无法使用Windows账户加密保存密钥，请检查本机读取组件") from None


def _unprotect(data):
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(data, None, None, None, 1)[1]
    except Exception:
        raise KeyStoreError("本机密钥无法解密，请在当前Windows账户下重新填写") from None


class LocalKeyStore:
    MAGIC = b"TAKEY1\x00"

    def __init__(self, database_path):
        database = Path(database_path).resolve()
        self.path = database.with_name(database.stem + ".model-key.dpapi")
        self.lock = threading.Lock()

    @property
    def saved(self):
        return self.path.is_file()

    def load(self):
        with self.lock:
            try:
                if not self.path.exists():
                    return ""
                payload = self.path.read_bytes()
                if len(payload) > 65536 or not payload.startswith(self.MAGIC):
                    raise KeyStoreError("本机密钥文件格式异常，请重新填写密钥")
                return _unprotect(payload[len(self.MAGIC):]).decode("utf-8")
            except KeyStoreError:
                raise
            except Exception:
                raise KeyStoreError("无法读取本机加密密钥，请重新填写") from None

    def save(self, key):
        with self.lock:
            if not key:
                try:
                    self.path.unlink(missing_ok=True)
                    return
                except OSError:
                    raise KeyStoreError("无法删除本机保存的密钥，请检查文件访问权限") from None
            encrypted = self.MAGIC + _protect(key.encode("utf-8"))
            temp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp.write_bytes(encrypted)
                os.replace(temp, self.path)
            except OSError:
                raise KeyStoreError("无法保存本机加密密钥，请检查文件访问权限") from None
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
