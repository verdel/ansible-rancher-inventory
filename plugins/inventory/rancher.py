# Copyright (c) 2026, verdel
# SPDX-License-Identifier: GPL-3.0-or-later

"""Discover Kubernetes nodes through the Rancher 2.x API proxy."""

import json
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit

from ansible.errors import AnsibleParserError
from ansible.module_utils.urls import open_url
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable

DOCUMENTATION = r"""
name: rancher
plugin_type: inventory
short_description: Kubernetes nodes from Rancher 2.x
description:
  - Lists Rancher clusters and reads Kubernetes Nodes through the Rancher proxy.
  - Inventory sources must end in C(rancher.yml) or C(rancher.yaml).
  - Host names use the cluster name and Kubernetes node name separated by two underscores.
extends_documentation_fragment:
  - ansible.builtin.constructed
options:
  plugin:
    description: Plugin name.
    required: true
    choices: [verdel.rancher.rancher, rancher]
  url:
    description: Rancher server base URL, without /v3.
    type: str
    required: true
    env:
      - name: RANCHER_URL
  token:
    description: Rancher API bearer token. Prefer the environment variable.
    type: str
    required: true
    env:
      - name: RANCHER_TOKEN
  clusters:
    description: Exact cluster names or IDs to include. Empty means all visible clusters.
    type: list
    elements: str
    default: []
  include_local:
    description: Include the Rancher local management cluster.
    type: bool
    default: false
  compose:
    description:
      - Mapping of host variable names to Jinja2 expressions applied to nodes in all selected clusters.
      - Write expressions without template delimiters. String literals require quotes inside the expression.
      - C(cluster_compose) supplements or overrides these expressions for individual clusters.
      - Expressions use original host variables and cannot depend on other composed variables.
      - Evaluation errors follow C(strict). Results are available to C(groups) and C(keyed_groups).
    type: dict
    default: {}
  cluster_compose:
    description:
      - Mapping of cluster names or IDs to dictionaries of Jinja2 expressions.
      - Expressions supplement global C(compose). For the same variable, cluster name overrides global and cluster ID overrides name.
      - Expressions are merged before evaluation and cannot depend on other composed variables.
    type: dict
    default: {}
  skip_disconnected:
    description:
      - Skip clusters reporting C(Connected=False), with a warning, even when explicitly selected.
      - Other API errors and unknown connection states are not skipped.
    type: bool
    default: false
  address_types:
    description: Ordered Kubernetes address types used to select ansible_host.
    type: list
    elements: str
    default: [InternalIP, ExternalIP, Hostname]
  label_selector:
    description: Kubernetes label selector applied to nodes in every selected cluster.
    type: str
    default: ''
  validate_certs:
    description: Verify the Rancher TLS certificate.
    type: bool
    default: true
  ca_path:
    description: Optional path to a PEM CA bundle on the controller.
    type: path
  timeout:
    description: Timeout in seconds for each API request.
    type: int
    default: 30
"""

EXAMPLES = r"""
plugin: verdel.rancher.rancher
url: https://rancher.example.com
clusters: [production, staging]
address_types: [InternalIP]
compose:
  ansible_user: "'ubuntu'"
cluster_compose:
  production:
    ansible_user: "'deploy'"
    ansible_port: '2222'
keyed_groups:
  - key: rancher_cluster_name
    prefix: cluster
"""


class InventoryModule(BaseInventoryPlugin, Constructable):
    NAME = "rancher"

    def verify_file(self, path):
        return super().verify_file(path) and path.endswith(
            ("rancher.yml", "rancher.yaml")
        )

    def _request(self, url):
        # Pagination must never send the bearer token to another origin.
        target, base = urlsplit(url), urlsplit(self._base_url)
        if (target.scheme, target.netloc) != (
            base.scheme,
            base.netloc,
        ) or target.username:
            raise AnsibleParserError(
                "Rancher pagination URL points outside the configured server"
            )
        try:
            response = open_url(
                url,
                headers={
                    "Authorization": "Bearer " + self.get_option("token"),
                    "Accept": "application/json",
                },
                validate_certs=self.get_option("validate_certs"),
                ca_path=self.get_option("ca_path"),
                timeout=self.get_option("timeout"),
                follow_redirects="none",
                use_netrc=False,
            )
            try:
                result = json.load(response)
            finally:
                response.close()
        except HTTPError as exc:
            raise AnsibleParserError(
                "Rancher API returned HTTP %s for %s" % (exc.code, target.path)
            ) from None
        except (ValueError, UnicodeError):
            raise AnsibleParserError(
                "Rancher API returned invalid JSON for %s; check the Rancher proxy response"
                % target.path
            ) from None
        except (URLError, OSError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            # Classify failures without printing arbitrary exception text, headers or bodies.
            if isinstance(reason, (TimeoutError, socket.timeout)):
                detail = (
                    "request timed out after %s seconds; check cluster connectivity or increase timeout"
                    % self.get_option("timeout")
                )
            elif isinstance(reason, ssl.SSLCertVerificationError):
                detail = "TLS certificate verification failed; configure ca_path with the trusted CA bundle"
            elif isinstance(reason, ssl.SSLError):
                detail = "TLS handshake or transport failed"
            elif isinstance(reason, socket.gaierror):
                detail = "DNS resolution failed"
            elif isinstance(reason, ConnectionError):
                detail = (
                    "connection refused, reset or interrupted (%s)"
                    % type(reason).__name__
                )
            else:
                detail = (
                    "transport error (%s); check network and proxy settings"
                    % type(reason).__name__
                )
            raise AnsibleParserError(
                "Rancher API request failed for %s: %s" % (target.path, detail)
            ) from None
        if not isinstance(result, dict):
            raise AnsibleParserError("Rancher API response must be a JSON object")
        return result

    def _clusters(self):
        url = self._base_url + "/v3/clusters?limit=100"
        seen = set()
        result = []
        while url:
            if url in seen:
                raise AnsibleParserError("Repeated Rancher pagination URL")
            seen.add(url)
            page = self._request(url)
            if not isinstance(page.get("data"), list):
                raise AnsibleParserError(
                    "Rancher cluster response is missing data list"
                )
            result.extend(page["data"])
            next_url = (page.get("pagination") or {}).get("next")
            url = urljoin(url, next_url) if next_url else None
        return result

    def _nodes(self, cluster_id):
        endpoint = (
            self._base_url
            + "/k8s/clusters/"
            + quote(cluster_id, safe="")
            + "/api/v1/nodes"
        )
        params = {"limit": 500}
        if self.get_option("label_selector"):
            params["labelSelector"] = self.get_option("label_selector")
        result, seen = [], set()
        while True:
            page = self._request(endpoint + "?" + urlencode(params))
            if not isinstance(page.get("items"), list):
                raise AnsibleParserError(
                    "Kubernetes node response is missing items list for " + cluster_id
                )
            result.extend(page["items"])
            continuation = (page.get("metadata") or {}).get("continue")
            if not continuation:
                return result
            if continuation in seen:
                raise AnsibleParserError(
                    "Repeated Kubernetes pagination token for " + cluster_id
                )
            seen.add(continuation)
            params["continue"] = continuation

    @staticmethod
    def _group(value):
        return re.sub(r"[^A-Za-z0-9_]", "_", value)

    def _populate(self, cluster, node):
        cluster_id = cluster["id"]
        metadata = node.get("metadata") or {}
        name = metadata.get("name")
        if not name:
            raise AnsibleParserError("Kubernetes node is missing metadata.name")
        addresses = (node.get("status") or {}).get("addresses") or []
        address = next(
            (
                a["address"]
                for kind in self.get_option("address_types")
                for a in addresses
                if a.get("type") == kind and a.get("address")
            ),
            None,
        )
        if not address:
            raise AnsibleParserError(
                "No matching connection address for %s/%s" % (cluster_id, name)
            )
        labels = metadata.get("labels") or {}
        roles = sorted(
            {
                key.split("/", 1)[1]
                for key in labels
                if key.startswith("node-role.kubernetes.io/") and key.split("/", 1)[1]
            }
        )
        if labels.get("kubernetes.io/role"):
            roles = sorted(set(roles + [labels["kubernetes.io/role"]]))
        host = (cluster.get("name") or cluster_id) + "__" + name
        self.inventory.add_host(host)
        variables = {
            "ansible_host": address,
            "rancher_cluster_id": cluster_id,
            "rancher_cluster_name": cluster.get("name") or cluster_id,
            "kubernetes_node_name": name,
            "kubernetes_labels": labels,
            "kubernetes_annotations": metadata.get("annotations") or {},
            "kubernetes_roles": roles,
            "kubernetes_addresses": addresses,
            "kubernetes_ready": any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in (node.get("status") or {}).get("conditions", [])
            ),
            "kubernetes_unschedulable": bool(
                (node.get("spec") or {}).get("unschedulable", False)
            ),
        }
        for key, value in variables.items():
            self.inventory.set_variable(host, key, value)
        for group in [
            "rancher_nodes",
            self._group(cluster.get("name") or cluster_id),
        ] + ["kubernetes_role_" + self._group(role) for role in roles]:
            self.inventory.add_group(group)
            self.inventory.add_host(host, group=group)
        strict = self.get_option("strict")
        compose = dict(self.get_option("compose"))
        cluster_compose = self.get_option("cluster_compose")
        compose.update(cluster_compose.get(cluster.get("name"), {}))
        compose.update(cluster_compose.get(cluster_id, {}))
        self._set_composite_vars(compose, variables, host, strict=strict)
        variables.update(self.inventory.get_host(host).get_vars())
        self._add_host_to_composed_groups(
            self.get_option("groups"), variables, host, strict=strict
        )
        self._add_host_to_keyed_groups(
            self.get_option("keyed_groups"), variables, host, strict=strict
        )

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path)
        self._read_config_data(path)
        for selector, expressions in self.get_option("cluster_compose").items():
            if not isinstance(selector, str) or not isinstance(expressions, dict):
                raise AnsibleParserError(
                    "cluster_compose must map cluster names or IDs to dictionaries of expressions"
                )
            if any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in expressions.items()
            ):
                raise AnsibleParserError(
                    "cluster_compose variable names and Jinja2 expressions must be strings"
                )
        self._base_url = self.get_option("url").rstrip("/")
        base = urlsplit(self._base_url)
        if (
            base.scheme != "https"
            or not base.netloc
            or base.username
            or base.query
            or base.fragment
        ):
            raise AnsibleParserError(
                "url must be an HTTPS Rancher base URL without credentials, query or fragment"
            )
        if not self.get_option("token").strip():
            raise AnsibleParserError("Rancher token must not be empty")
        if self.get_option("timeout") <= 0 or not self.get_option("address_types"):
            raise AnsibleParserError(
                "timeout must be positive and address_types must not be empty"
            )
        requested = set(self.get_option("clusters"))
        clusters = [
            c
            for c in self._clusters()
            if (self.get_option("include_local") or c.get("id") != "local")
        ]
        matched = {value for c in clusters for value in (c.get("id"), c.get("name"))}
        if requested - matched:
            raise AnsibleParserError(
                "Requested clusters were not found or are excluded: "
                + ", ".join(sorted(requested - matched))
            )
        # Fetch everything before populating inventory, so API failures do not produce partial inventory.
        records = []
        for cluster in clusters:
            if requested and not requested.intersection(
                (cluster.get("id"), cluster.get("name"))
            ):
                continue
            if not cluster.get("id"):
                raise AnsibleParserError("Rancher cluster is missing id")
            if any(
                c.get("type") == "Connected" and c.get("status") == "False"
                for c in cluster.get("conditions") or []
            ):
                if self.get_option("skip_disconnected"):
                    self.display.warning(
                        "Skipping disconnected Rancher cluster %s (%s): Connected=False"
                        % (cluster.get("name") or cluster["id"], cluster["id"])
                    )
                    continue
                raise AnsibleParserError(
                    "Rancher cluster %s (%s) is disconnected (Connected=False); "
                    "restore the cluster agent connection, select connected clusters using clusters, "
                    "or enable skip_disconnected"
                    % (cluster.get("name") or cluster["id"], cluster["id"])
                )
            records.extend((cluster, node) for node in self._nodes(cluster["id"]))
        group_owners = {}
        for cluster, _ in records:
            cluster_name = cluster.get("name") or cluster["id"]
            group = self._group(cluster_name)
            if group in {"all", "ungrouped", "rancher_nodes"} or group.startswith(
                "kubernetes_role_"
            ):
                raise AnsibleParserError(
                    "Cluster name produces reserved inventory group %s; rename the cluster"
                    % group
                )
            owner = group_owners.setdefault(group, cluster["id"])
            if owner != cluster["id"]:
                raise AnsibleParserError(
                    "Cluster names produce the same inventory group %s (%s and %s); "
                    "rename the clusters or select one using clusters"
                    % (group, owner, cluster["id"])
                )
        for cluster, node in records:
            self._populate(cluster, node)
