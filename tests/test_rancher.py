import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from ansible.errors import AnsibleError, AnsibleParserError
from ansible.inventory.data import InventoryData
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader, inventory_loader

init_plugin_loader()
ROOT = Path(__file__).resolve().parents[1]
inventory_loader.add_directory(str(ROOT / "plugins" / "inventory"))


@pytest.fixture
def plugin():
    return inventory_loader.get("rancher")


def node(name="worker-1", addresses=None):
    return {
        "metadata": {"name": name, "labels": {"node-role.kubernetes.io/worker": ""}},
        "status": {
            "addresses": addresses
            if addresses is not None
            else [
                {"type": "ExternalIP", "address": "203.0.113.10"},
                {"type": "InternalIP", "address": "10.0.0.10"},
            ],
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def parse(plugin, tmp_path, monkeypatch, pages, extra=""):
    config = tmp_path / "test.rancher.yml"
    config.write_text(
        "plugin: rancher\nurl: https://rancher.example.com\ntoken: secret\n" + extra
    )
    calls = []

    def request(url):
        calls.append(url)
        return pages[len(calls) - 1]

    monkeypatch.setattr(plugin, "_request", request)
    inventory = InventoryData()
    plugin.parse(inventory, DataLoader(), str(config))
    return inventory, calls


def test_inventory_and_constructed(plugin, tmp_path, monkeypatch):
    inv, calls = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {
                "data": [
                    {"id": "local", "name": "local"},
                    {"id": "c-one", "name": "prod"},
                    {"id": "c-two", "name": "stage"},
                ]
            },
            {"items": [node()]},
            {"items": [node()]},
        ],
        "compose:\n  ansible_user: \"'ubuntu'\"\nkeyed_groups:\n  - key: rancher_cluster_name\n    prefix: cluster\n",
    )
    assert set(inv.hosts) == {"prod__worker-1", "stage__worker-1"}
    variables = inv.hosts["prod__worker-1"].vars
    assert inv.groups["prod"].hosts == [inv.hosts["prod__worker-1"]]
    assert variables["rancher_cluster_id"] == "c-one"
    assert variables["ansible_host"] == "10.0.0.10"
    assert variables["ansible_user"] == "ubuntu"
    assert variables["kubernetes_ready"] is True
    assert inv.groups["cluster_prod"].hosts == [inv.hosts["prod__worker-1"]]
    assert len(inv.groups["kubernetes_role_worker"].hosts) == 2
    assert len(calls) == 3


def test_pagination_and_selection(plugin, tmp_path, monkeypatch):
    inv, calls = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {
                "data": [{"id": "c-skip", "name": "skip"}],
                "pagination": {"next": "?page=2"},
            },
            {"data": [{"id": "c-one", "name": "prod"}]},
            {"items": [node()], "metadata": {"continue": "a+/="}},
            {"items": [node("worker-2")]},
        ],
        'clusters: [prod]\nlabel_selector: "env=prod"\n',
    )
    assert len(inv.hosts) == 2
    assert calls[1] == "https://rancher.example.com/v3/clusters?page=2"
    assert "continue=a%2B%2F%3D" in calls[3]
    assert "labelSelector=env%3Dprod" in calls[2]


def test_unknown_cluster(plugin, tmp_path, monkeypatch):
    with pytest.raises(AnsibleParserError, match="not found"):
        parse(plugin, tmp_path, monkeypatch, [{"data": []}], "clusters: [missing]\n")


def test_missing_address(plugin, tmp_path, monkeypatch):
    with pytest.raises(AnsibleParserError, match="No matching connection address"):
        parse(
            plugin,
            tmp_path,
            monkeypatch,
            [{"data": [{"id": "c-one"}]}, {"items": [node(addresses=[])]}],
        )


def test_repeated_pagination(plugin, tmp_path, monkeypatch):
    with pytest.raises(AnsibleParserError, match="Repeated Rancher"):
        parse(
            plugin,
            tmp_path,
            monkeypatch,
            [{"data": [], "pagination": {"next": "?limit=100"}}],
        )


def test_external_pagination_rejected(plugin):
    plugin._base_url = "https://rancher.example.com"
    with pytest.raises(AnsibleParserError, match="outside"):
        plugin._request("https://attacker.example/v3/clusters")


def test_request_security_and_errors(plugin, monkeypatch):
    plugin._base_url = "https://rancher.example.com"
    options = {
        "token": "secret",
        "validate_certs": True,
        "ca_path": None,
        "timeout": 30,
    }
    monkeypatch.setattr(plugin, "get_option", options.get)
    module = __import__(plugin.__module__, fromlist=["open_url"])

    def open_mock(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer secret"
        assert kwargs["follow_redirects"] == "none"
        assert kwargs["use_netrc"] is False
        return io.BytesIO(json.dumps({"data": []}).encode())

    monkeypatch.setattr(module, "open_url", open_mock)
    assert plugin._request(plugin._base_url + "/v3/clusters") == {"data": []}

    def denied(url, **kwargs):
        raise HTTPError(url, 403, "secret", {}, None)

    monkeypatch.setattr(module, "open_url", denied)
    with pytest.raises(AnsibleParserError, match="HTTP 403") as error:
        plugin._request(plugin._base_url + "/v3/clusters")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "failure, message",
    [
        (TimeoutError("secret"), "timed out after 30 seconds"),
        (URLError(TimeoutError("secret")), "timed out after 30 seconds"),
        (
            URLError(__import__("ssl").SSLCertVerificationError(1, "secret")),
            "TLS certificate verification failed",
        ),
        (
            URLError(__import__("socket").gaierror(-2, "secret")),
            "DNS resolution failed",
        ),
        (ConnectionResetError("secret"), "connection refused, reset or interrupted"),
        (URLError("secret"), "transport error"),
    ],
)
def test_transport_diagnostics(plugin, monkeypatch, failure, message):
    plugin._base_url = "https://rancher.example.com"
    options = {
        "token": "secret",
        "validate_certs": True,
        "ca_path": None,
        "timeout": 30,
    }
    monkeypatch.setattr(plugin, "get_option", options.get)
    module = __import__(plugin.__module__, fromlist=["open_url"])

    def fail(url, **kwargs):
        raise failure

    monkeypatch.setattr(module, "open_url", fail)
    with pytest.raises(AnsibleParserError, match=message) as error:
        plugin._request(plugin._base_url + "/api/v1/nodes")
    assert "secret" not in str(error.value)


def test_invalid_json_closes_response(plugin, monkeypatch):
    plugin._base_url = "https://rancher.example.com"
    monkeypatch.setattr(plugin, "get_option", {"token": "secret"}.get)
    module = __import__(plugin.__module__, fromlist=["open_url"])
    response = io.BytesIO(b"<html>secret</html>")
    monkeypatch.setattr(module, "open_url", lambda *args, **kwargs: response)
    with pytest.raises(AnsibleParserError, match="invalid JSON") as error:
        plugin._request(plugin._base_url + "/api/v1/nodes")
    assert response.closed
    assert "secret" not in str(error.value)


def test_disconnected_cluster_fails_before_node_request(plugin, tmp_path, monkeypatch):
    with pytest.raises(AnsibleParserError, match=r"prod .* is disconnected"):
        parse(
            plugin,
            tmp_path,
            monkeypatch,
            [
                {
                    "data": [
                        {
                            "id": "c-one",
                            "name": "prod",
                            "conditions": [{"type": "Connected", "status": "False"}],
                        }
                    ]
                }
            ],
        )


def test_disconnected_unselected_cluster_is_ignored(plugin, tmp_path, monkeypatch):
    inv, calls = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {
                "data": [
                    {"id": "c-one", "name": "prod"},
                    {
                        "id": "c-two",
                        "name": "offline",
                        "conditions": [{"type": "Connected", "status": "False"}],
                    },
                ]
            },
            {"items": [node()]},
        ],
        "clusters: [prod]\n",
    )
    assert len(inv.hosts) == 1
    assert len(calls) == 2


def test_skip_disconnected_preserves_connected_nodes(plugin, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(plugin.display, "warning", warnings.append)
    inv, calls = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {
                "data": [
                    {
                        "id": "c-off",
                        "name": "offline",
                        "conditions": [{"type": "Connected", "status": "False"}],
                    },
                    {
                        "id": "c-on",
                        "name": "online",
                        "conditions": [{"type": "Connected", "status": "True"}],
                    },
                ]
            },
            {"items": [node()]},
        ],
        "skip_disconnected: true\n",
    )
    assert set(inv.hosts) == {"online__worker-1"}
    assert len(calls) == 2
    assert "/c-on/" in calls[1]
    assert len(warnings) == 1
    assert "offline (c-off)" in warnings[0]


def test_skip_explicitly_selected_disconnected_cluster(plugin, tmp_path, monkeypatch):
    inv, calls = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {
                "data": [
                    {
                        "id": "c-off",
                        "name": "offline",
                        "conditions": [{"type": "Connected", "status": "False"}],
                    }
                ]
            },
        ],
        "skip_disconnected: true\nclusters: [offline]\n",
    )
    assert not inv.hosts
    assert len(calls) == 1


@pytest.mark.parametrize(
    "conditions", [[], [{"type": "Connected", "status": "Unknown"}]]
)
def test_skip_disconnected_does_not_hide_other_errors(
    plugin, tmp_path, monkeypatch, conditions
):
    with pytest.raises(AnsibleParserError, match="missing items list"):
        parse(
            plugin,
            tmp_path,
            monkeypatch,
            [
                {"data": [{"id": "c-one", "conditions": conditions}]},
                {},
            ],
            "skip_disconnected: true\n",
        )


@pytest.mark.parametrize("names", [("prod", "prod"), ("prod-a", "prod_a")])
def test_cluster_name_collision_rejected(plugin, tmp_path, monkeypatch, names):
    with pytest.raises(AnsibleParserError, match="same inventory group"):
        parse(
            plugin,
            tmp_path,
            monkeypatch,
            [
                {
                    "data": [
                        {"id": "c-one", "name": names[0]},
                        {"id": "c-two", "name": names[1]},
                    ]
                },
                {"items": [node()]},
                {"items": [node()]},
            ],
        )


def test_cluster_without_name_falls_back_to_id(plugin, tmp_path, monkeypatch):
    inv, _ = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {"data": [{"id": "c-one"}]},
            {"items": [node()]},
        ],
    )
    assert set(inv.hosts) == {"c-one__worker-1"}
    assert inv.groups["c_one"].hosts == [inv.hosts["c-one__worker-1"]]


def test_cluster_compose_overrides_and_groups(plugin, tmp_path, monkeypatch):
    inv, _ = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {
                "data": [
                    {"id": "c-one", "name": "prod"},
                    {"id": "c-two", "name": "stage"},
                ]
            },
            {"items": [node()]},
            {"items": [node()]},
        ],
        """strict: true
compose:
  ansible_user: "'ubuntu'"
  shared_value: "'shared'"
cluster_compose:
  prod:
    ansible_user: "'deploy'"
    ansible_port: '2222'
    node_alias: kubernetes_node_name
  c-one:
    ansible_user: "'admin'"
  unused:
    ansible_user: "'unused'"
groups:
  admins: ansible_user == 'admin'
""",
    )
    prod = inv.hosts["prod__worker-1"]
    stage = inv.hosts["stage__worker-1"]
    assert prod.vars["ansible_user"] == "admin"
    assert prod.vars["ansible_port"] == 2222
    assert prod.vars["node_alias"] == "worker-1"
    assert stage.vars["ansible_user"] == "ubuntu"
    assert "ansible_port" not in stage.vars
    assert prod.vars["shared_value"] == stage.vars["shared_value"] == "shared"
    assert inv.groups["admins"].hosts == [prod]


@pytest.mark.parametrize(
    "config", ["prod: null", "prod: []", "prod: {ansible_port: 2222}"]
)
def test_invalid_cluster_compose(plugin, tmp_path, monkeypatch, config):
    with pytest.raises(AnsibleParserError, match="cluster_compose"):
        parse(plugin, tmp_path, monkeypatch, [], "cluster_compose:\n  " + config + "\n")


@pytest.mark.parametrize("strict", ["true", "false"])
def test_cluster_compose_respects_strict(plugin, tmp_path, monkeypatch, strict):
    pages = [{"data": [{"id": "c-one", "name": "prod"}]}, {"items": [node()]}]
    config = (
        "strict: "
        + strict
        + "\ncluster_compose:\n  prod:\n    invalid: missing_variable\n"
    )
    if strict == "true":
        with pytest.raises(AnsibleError, match="Could not set invalid"):
            parse(plugin, tmp_path, monkeypatch, pages, config)
    else:
        inv, _ = parse(plugin, tmp_path, monkeypatch, pages, config)
        assert "invalid" not in inv.hosts["prod__worker-1"].vars


@pytest.mark.parametrize(
    "name", ["all", "ungrouped", "rancher_nodes", "kubernetes-role-worker"]
)
def test_reserved_cluster_group_rejected(plugin, tmp_path, monkeypatch, name):
    with pytest.raises(AnsibleParserError, match="reserved inventory group"):
        parse(
            plugin,
            tmp_path,
            monkeypatch,
            [
                {"data": [{"id": "c-one", "name": name}]},
                {"items": [node()]},
            ],
        )


def test_cluster_group_has_no_prefix(plugin, tmp_path, monkeypatch):
    inv, _ = parse(
        plugin,
        tmp_path,
        monkeypatch,
        [
            {"data": [{"id": "c-one", "name": "rancher-test-cluster"}]},
            {"items": [node()]},
        ],
    )
    host = inv.hosts["rancher-test-cluster__worker-1"]
    assert inv.groups["rancher_test_cluster"].hosts == [host]
    assert "rancher_cluster_rancher_test_cluster" not in inv.groups
