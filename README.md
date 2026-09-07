# verdel.rancher

Ansible collection for discovering Kubernetes nodes through Rancher 2.x.
Repository: [verdel/ansible-rancher-inventory](https://github.com/verdel/ansible-rancher-inventory).

The inventory plugin is **`verdel.rancher.rancher`**. It lists clusters through
`/v3/clusters` and reads nodes through
`/k8s/clusters/<cluster-id>/api/v1/nodes`, independently of the node driver.

Example inventory output:

```text
@all:
  |--@ungrouped:
  |--@rancher_nodes:
  |  |--production__worker-1
  |--@production:
  |  |--production__worker-1
  |--@kubernetes_role_worker:
  |  |--production__worker-1
```

## Installation

Use Ansible Core 2.16–2.19 and a Python version supported by the selected
Ansible release. Python 3.12 is used in CI. No Rancher or Kubernetes Python SDK
is required.

Once this repository is available on GitHub, install directly from Git:

```sh
ansible-galaxy collection install git+https://github.com/verdel/ansible-rancher-inventory.git
```

Or use a collection requirements file:

```yaml
collections:
  - name: https://github.com/verdel/ansible-rancher-inventory.git
    type: git
```

```sh
ansible-galaxy collection install -r collections-requirements.yml
```

To build and install from a local checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
ansible-galaxy collection build --output-path dist --force
ansible-galaxy collection install dist/verdel-rancher-1.0.0.tar.gz --force
```

These commands do not require the collection to be published on Ansible Galaxy.

## Getting started

After installing the collection, use the full example in `inventory/rancher.yml`
and copy `ansible.cfg.example` to `ansible.cfg` (merge with any existing local
settings). The example enables Ansible's `auto` loader and the collection plugin.

```sh
export RANCHER_URL='https://rancher.example.com'
# Enter the token without saving it in shell history (bash/zsh):
read -rs RANCHER_TOKEN
export RANCHER_TOKEN
ansible-inventory -i inventory/rancher.yml --graph
ansible-inventory -i inventory/rancher.yml --list
ansible rancher_nodes -i inventory/rancher.yml -m ping -u ubuntu
ansible-doc -t inventory verdel.rancher.rancher
```

Use a Rancher API token in the form `token-xxxxx:secret` with permission to list
clusters and perform `list nodes` in the selected clusters through the Rancher
proxy. No kubeconfig is required. Ansible connects directly to nodes over SSH;
Rancher access is used only to build the inventory.

Inventory filenames must end in `rancher.yml` or `rancher.yaml`. Set
`plugin: verdel.rancher.rancher` to use the installed collection. Ansible's
`auto` plugin can load it without configuring a local plugin directory.

Keep private inventory settings in `inventory/local.rancher.yml` and CA bundles
outside the published example. Local inventory files, CA bundles, and
`ansible.cfg` are ignored by Git and excluded from the collection artifact.

## Configuration

```yaml
plugin: verdel.rancher.rancher
url: https://rancher.example.com
# Prefer passing the token through RANCHER_TOKEN.
clusters: [production, c-m-abcdefgh]  # Exact names or IDs; [] = all visible clusters
include_local: false
skip_disconnected: false  # true = skip clusters reporting Connected=False
address_types: [InternalIP, ExternalIP, Hostname]  # First matching address
label_selector: 'environment=production'
validate_certs: true
# ca_path: /etc/ssl/certs/company-ca.pem
timeout: 30
compose:
  ansible_user: "'ubuntu'"
keyed_groups:
  - key: rancher_cluster_name
    prefix: cluster
groups:
  ready_nodes: kubernetes_ready
```

The plugin supports the `RANCHER_URL` and `RANCHER_TOKEN` environment variables
and the `compose`, `groups`, `keyed_groups`, and `strict` options from Ansible's
constructed inventory support. The URL must be an HTTPS Rancher base URL without
`/v3`; a reverse proxy path prefix is supported. TLS verification is enabled.
Use `ca_path` for a custom CA bundle.

By default, all visible clusters except `local` are included. To explicitly
select `local`, also set `include_local: true`. An unknown or excluded cluster
in `clusters` causes an error. An unavailable API or a node without a matching
address from `address_types` also causes an error. Nodes that are not ready or
are cordoned are included; you can filter them in a playbook using their host variables.

## Hosts and groups

Host names follow the format `<cluster-name>__<node-name>`, for example
`production__worker-1`. Nodes with the same name in different clusters remain
separate hosts. `ansible_host` contains the selected connection address.

Automatic groups:

- `rancher_nodes`: all nodes.
- `<cluster-name>`: nodes in a cluster, with invalid characters replaced by `_` (for example, `rancher_test_cluster`).
- `kubernetes_role_<role>`: roles from the `node-role.kubernetes.io/*` and
  `kubernetes.io/role` labels. The worker role is not inferred from the absence
  of other roles.

Invalid characters in group names are replaced with `_`. If cluster names
collide after this replacement, the plugin raises an error to prevent their
hosts from being merged. Names matching reserved groups (`all`, `ungrouped`,
`rancher_nodes`, or the `kubernetes_role_` prefix) are rejected.
If a cluster has no name, its ID is used instead.
The ID is always available in `rancher_cluster_id`. Renaming a cluster changes
its host and group names in the inventory.

Host variables: `rancher_cluster_id`, `rancher_cluster_name`,
`kubernetes_node_name`, `kubernetes_labels`, `kubernetes_annotations`,
`kubernetes_roles`, `kubernetes_addresses`, `kubernetes_ready`,
`kubernetes_unschedulable`, and `ansible_host`.

Both APIs support pagination. There is no caching: each run reads current data.
The token is not stored in hostvars. HTTP redirects and pagination links to
another server are rejected.

## Disconnected clusters

If Rancher reports `Connected=False`, the plugin stops with an error containing
the disconnected cluster's name and ID, without waiting for the Kubernetes proxy
to time out. Restore the cluster agent connection or explicitly select the
connected clusters you need using `clusters`.

To skip disconnected clusters with a warning, set `skip_disconnected: true`.
This also applies to explicitly selected clusters. If all selected clusters are
disconnected, the inventory will be empty. The option is disabled by default.
Unknown connection states, timeouts, and other API errors are not skipped.

## Per-cluster variables

`compose` defines shared expressions for all nodes. `cluster_compose` supplements
or overrides them for a cluster selected by its exact name or ID:

```yaml
compose:
  ansible_user: "'ubuntu'"
  ansible_port: '22'
cluster_compose:
  rancher-test-cluster:
    ansible_user: "'deploy'"
    ansible_ssh_private_key_file: "'/home/user/.ssh/test_cluster'"
  c-m-abcdefgh:
    ansible_port: '2222'
```

For each variable, precedence from highest to lowest is: cluster ID, cluster
name, then global `compose`. Expression dictionaries are merged before evaluation.
Expressions use the original hostvars and must not refer to other variables
created through compose. Results are available to `groups` and `keyed_groups`.
Values must be strings containing Jinja2 expressions without `{{ }}`; string
literals require inner quotes. Evaluation errors follow the `strict` option,
as with standard `compose`. Settings for clusters outside the current selection
are not applied.

## Validation

```sh
pip install -r requirements-dev.txt
pytest -q
ansible-doc -t inventory verdel.rancher.rancher
```

Tests use the actual Ansible plugin loader with simulated API responses.
They also build and install the collection in a temporary directory and verify
FQCN discovery through the auto loader. No live Rancher access is required.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

## API references

- [Rancher v3 API](https://ranchermanager.docs.rancher.com/v2.14/api/v3-rancher-api-guide)
- [Rancher API proxy](https://github.com/rancher/rancher/wiki/Rancher-API-Extensions)
- [Kubernetes Nodes API](https://kubernetes.io/docs/reference/kubernetes-api/core/node-v1/)

## Linting

Install the development dependencies and enable the Git hook in your Git checkout:

```sh
pip install -r requirements-dev.txt
pre-commit install
pre-commit run --all-files
```

The Ruff hook checks Python code and applies available safe fixes. If it modifies
files, review and stage the changes before committing again. To run the same
lint rules without modifying files:

```sh
ruff check .
```

Rules are configured in `ruff.toml`: Ruff's default error checks plus import
sorting. Ruff is pinned to the same version in the development dependencies,
pre-commit hook, and CI workflow. CI runs on pushes, pull requests, and manual
dispatch. It checks lint, formatting, tests, and collection installation. A separate
release workflow validates the release tag, reruns checks, builds the collection,
and attaches its archive to the published GitHub Release.


## Repository layout

```text
ansible-rancher-inventory/
├── galaxy.yml                 # Collection metadata: verdel.rancher
├── meta/runtime.yml           # Supported Ansible versions
├── plugins/inventory/rancher.py
├── inventory/rancher.yml      # Full public configuration example
├── tests/                     # Unit and collection installation tests
├── .github/workflows/ci.yml   # Push / pull request checks
├── .github/workflows/release.yml # Build and attach release archive
├── ansible.cfg.example
├── CHANGELOG.md
├── CONTRIBUTING.md
├── MAINTAINERS.md
└── LICENSE
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
