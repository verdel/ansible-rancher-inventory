"""Exercise the distributable collection through Ansible's real loaders."""

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_install_and_auto_inventory(tmp_path):
    env = os.environ.copy()
    config = tmp_path / "ansible.cfg"
    config.write_text("[inventory]\nenable_plugins = auto\nunparsed_is_failed = true\n")
    env.update(
        ANSIBLE_CONFIG=str(config),
        ANSIBLE_HOME=str(tmp_path / "ansible-home"),
        ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "collections"),
        RANCHER_URL="https://rancher.example.com",
        RANCHER_TOKEN="test-token",
    )
    galaxy = str(Path(sys.executable).with_name("ansible-galaxy"))

    def run(args):
        return subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    run([galaxy, "collection", "build", str(ROOT), "--output-path", str(tmp_path)])
    (artifact,) = tmp_path.glob("verdel-rancher-*.tar.gz")
    with tarfile.open(artifact) as archive:
        names = archive.getnames()
        assert "plugins/inventory/rancher.py" in names
        assert "meta/runtime.yml" in names
        assert "LICENSE" in names
        assert "inventory/rancher.yml" in names
        assert not any(
            name.startswith(
                (".venv", ".collections", "dist", "tests", "inventory/local")
            )
            or name.endswith((".crt", ".pem"))
            or name == "ansible.cfg"
            for name in names
        )
        manifest = json.load(archive.extractfile("MANIFEST.json"))
        assert manifest["collection_info"]["namespace"] == "verdel"
        assert manifest["collection_info"]["name"] == "rancher"
    run(
        [
            galaxy,
            "collection",
            "install",
            str(artifact),
            "-p",
            env["ANSIBLE_COLLECTIONS_PATH"],
            "--offline",
        ]
    )
    doc = run(
        [
            str(Path(sys.executable).with_name("ansible-doc")),
            "-t",
            "inventory",
            "verdel.rancher.rancher",
            "--json",
        ]
    )
    assert (
        "cluster_compose"
        in json.loads(doc.stdout)["verdel.rancher.rancher"]["doc"]["options"]
    )
    source = tmp_path / "test.rancher.yml"
    source.write_text(
        "plugin: verdel.rancher.rancher\nstrict: true\n"
        "compose:\n  ansible_user: \"'ubuntu'\"\n"
    )
    run(
        [
            sys.executable,
            "-c",
            """
import io
import json
import sys
from ansible.plugins.loader import init_plugin_loader, inventory_loader
from ansible.parsing.dataloader import DataLoader
from ansible.inventory.manager import InventoryManager
init_plugin_loader()
plugin = inventory_loader.get('verdel.rancher.rancher')
module = sys.modules[plugin.__module__]
def request(url, **kwargs):
    if '/v3/clusters' in url:
        data = {'data': [{'id': 'c-test', 'name': 'production'}]}
    elif '/k8s/clusters/c-test/api/v1/nodes' in url:
        data = {'items': [{'metadata': {'name': 'worker-1'}, 'status': {
            'addresses': [{'type': 'InternalIP', 'address': '10.0.0.1'}]}}]}
    else:
        raise AssertionError(url)
    return io.BytesIO(json.dumps(data).encode())
module.open_url = request
inventory = InventoryManager(loader=DataLoader(), sources=[sys.argv[1]])
host = inventory.get_host('production__worker-1')
assert host.vars['ansible_host'] == '10.0.0.1'
assert host.vars['ansible_user'] == 'ubuntu'
assert host in inventory.groups['production'].hosts
""",
            str(source),
        ]
    )
