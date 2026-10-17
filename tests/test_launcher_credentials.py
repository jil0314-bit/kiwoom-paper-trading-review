import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from SOURCE import launcher


class LauncherCredentialTests(TestCase):
    def test_current_names_are_supported(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(launcher.install_credentials({
                "KIWOOM_APP_KEY": "app",
                "KIWOOM_SECRET_KEY": "secret",
            }))
            self.assertEqual(os.environ["KIWOOM_APP_KEY"], "app")
            self.assertEqual(os.environ["KIWOOM_SECRET_KEY"], "secret")

    def test_existing_desktop_aliases_are_supported(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(launcher.install_credentials({
                "APP_KEY": "app",
                "APP_SECRET": "secret",
                "BASE_URL": "https://example.invalid",
            }))
            self.assertEqual(os.environ["KIWOOM_APP_KEY"], "app")
            self.assertEqual(os.environ["KIWOOM_SECRET_KEY"], "secret")
            self.assertEqual(os.environ["KIWOOM_BASE_URL"], "https://example.invalid")

    def test_partial_credentials_are_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(launcher.install_credentials({"APP_KEY": "app"}))
            self.assertNotIn("KIWOOM_APP_KEY", os.environ)

    def test_explicit_kiwoom_file_beats_cached_generic_rest_file(self):
        with TemporaryDirectory() as folder:
            home = Path(folder)
            generic = home / "Desktop" / "코딩관련파일들" / "키움REST" / ".env"
            canonical = home / "Desktop" / "기타" / ".env"
            pointer = home / "pointer.txt"
            generic.parent.mkdir(parents=True)
            canonical.parent.mkdir(parents=True)
            generic.write_text("APP_KEY=generic\nAPP_SECRET=generic-secret\n", encoding="utf-8")
            canonical.write_text(
                "KIWOOM_APP_KEY=kiwoom\nKIWOOM_APP_SECRET=kiwoom-secret\n",
                encoding="utf-8",
            )
            pointer.write_text(str(generic), encoding="utf-8")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(launcher, "CREDENTIAL_POINTER", pointer),
                patch.object(launcher, "ROOT", home / "project"),
                patch.object(launcher, "CORE", home / "project" / "SOURCE" / "core"),
                patch.object(launcher.Path, "home", return_value=home),
            ):
                self.assertEqual(launcher.recover_existing_credentials(), "recovered-sibling")
                self.assertEqual(os.environ["KIWOOM_APP_KEY"], "kiwoom")
                self.assertEqual(os.environ["KIWOOM_SECRET_KEY"], "kiwoom-secret")
                self.assertEqual(pointer.read_text(encoding="utf-8"), str(canonical.resolve()))
