import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARMONY = ROOT / "harmonyos"


def load_json5_without_comments(path: Path):
    # The manifests currently use strict JSON syntax despite the .json5 suffix.
    return json.loads(path.read_text(encoding="utf-8"))


class HarmonyOsClientTests(unittest.TestCase):
    def test_deveco_bundled_hvigor_plugin_is_not_an_ohpm_dependency(self):
        package = load_json5_without_comments(HARMONY / "oh-package.json5")
        dependencies = {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
        }
        self.assertNotIn("@ohos/hvigor-ohos-plugin", dependencies)

    def test_project_contains_entry_module(self):
        profile = load_json5_without_comments(HARMONY / "build-profile.json5")
        self.assertIn("entry", [module["name"] for module in profile["modules"]])
        pages = load_json5_without_comments(
            HARMONY / "entry/src/main/resources/base/profile/main_pages.json"
        )
        self.assertEqual(pages["src"], ["pages/Index"])

    def test_manifest_allows_lan_server_access(self):
        manifest = load_json5_without_comments(HARMONY / "entry/src/main/module.json5")
        permissions = {
            item["name"] for item in manifest["module"]["requestPermissions"]
        }
        self.assertIn("ohos.permission.INTERNET", permissions)
        self.assertIn("ohos.permission.GET_NETWORK_INFO", permissions)
        self.assertEqual(manifest["module"]["deviceTypes"], ["phone", "tablet"])

    def test_native_page_uses_persisted_server_and_lifecycle_bridge(self):
        page = (HARMONY / "entry/src/main/ets/pages/Index.ets").read_text(
            encoding="utf-8"
        )
        self.assertIn("PersistentStorage.persistProp", page)
        self.assertIn("controller.loadUrl(normalized)", page)
        self.assertIn("onHostBackground", page)
        self.assertIn("onHostForeground", page)

    def test_web_client_exposes_native_lifecycle_api(self):
        app_js = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
        self.assertIn("window.YouPhoniumApp", app_js)
        self.assertIn("onHostBackground", app_js)
        self.assertIn("onHostForeground", app_js)


if __name__ == "__main__":
    unittest.main()
