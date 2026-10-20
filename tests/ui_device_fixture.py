"""Serve a fully synthetic device workflow for browser acceptance.

Run from the repository root: python tests/ui_device_fixture.py
No user data is read or changed; all state is discarded when the process exits.
"""
from __future__ import annotations

import argparse
import base64
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_device_management import ReadOnlyTransport
from test_native_configuration import fixture

from yomesh.server import handler_for


def main(port=8769, *, dynamic_after_pull=False):
    with tempfile.TemporaryDirectory(prefix="yomesh-device-ui-") as folder:
        root = Path(folder)
        shutil.copytree(ROOT / "web", root / "web")
        shutil.copytree(ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        native = fixture()
        native["mihomo"]["proxy-providers"] = {
            "合成来源 A": {"type": "file", "path": "providers/synthetic-a.yaml"},
            "合成来源 B": {"type": "file", "path": "providers/synthetic-b.yaml"},
        }
        native["documents"].update({
            "providers/synthetic-a.yaml": "proxies: []\n",
            "providers/synthetic-b.yaml": "proxies: []\n",
        })
        main_config = {**native["mihomo"], "rules": [row["text"] for row in native["policies"]]}
        files = {"mihomo.yaml": yaml.safe_dump(main_config, allow_unicode=True).encode(),
                 **{name: text.encode() for name, text in native["documents"].items()},
                 **{name: base64.b64decode(text) for name, text in native["assets"].items()}}
        if dynamic_after_pull:
            files["localdns/generated/qa.txt"] = b"before\n"
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(root, port))
        handler = server.RequestHandlerClass
        handler.device_management.registry.transport = ReadOnlyTransport(files)
        handler.auth_store.update_credentials("", "fixture", "fixture-password")
        ident = "device_" + "a" * 32
        handler.device_management.registry.state["devices"].append({
            "id": ident, "name": "合成测试网关", "host": "192.0.2.7", "port": 22,
            "username": "root", "host_key": "SHA256:" + "A" * 43,
            "space_id": None, "baseline": None, "baseline_digest": None, "refs": [],
            "mihomo_home": "/etc/mihomo", "mihomo_config": "/etc/mihomo/mihomo.yaml",
            "mihomo_binary": "/usr/local/bin/mihomo", "localdns_root": "/opt/localdns",
            "localdns_binary": "/opt/localdns/localdns", "manager": "openrc",
            "services": {"mihomo": "mihomo", "localdns": "localdns"},
            "capabilities": [], "last_read": None})
        if dynamic_after_pull:
            def refresh_generated():
                while handler.device_management.registry.device(ident).get("baseline") is None:
                    time.sleep(0.1)
                handler.device_management.registry.transport.files["localdns/generated/qa.txt"] = b"after\n"
            threading.Thread(target=refresh_generated, daemon=True).start()
        print(f"Synthetic YoMesh fixture: http://127.0.0.1:{port}/#spaces", flush=True)
        print("Login: fixture / fixture-password", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            handler.space_catalog.close()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--dynamic-after-pull", action="store_true")
    args = parser.parse_args()
    main(args.port, dynamic_after_pull=args.dynamic_after_pull)
