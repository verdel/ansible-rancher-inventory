# Contributing

Use Python 3.14 or newer for development with the latest supported Ansible Core.
Create a virtual environment and install the development dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

## Checks

Run these before opening a pull request:

```sh
pre-commit run --all-files   # Ruff lint and format
pytest tests/ -q
ansible-galaxy collection build --output-path dist --force
./.github/scripts/prepare-collection-tree.sh
SANITY_ROOT="$(cat .sanity-tree-path)"
cd "$SANITY_ROOT/ansible_collections/verdel/rancher"
ANSIBLE_COLLECTIONS_PATH="$SANITY_ROOT" ansible-test sanity --local --color no
```

The preparation script creates a temporary collection tree under
`ansible_collections/verdel/rancher` and writes its root directory to
`.sanity-tree-path`. This is required because `ansible-test` expects a collection
to use that directory layout. The `--local` option explicitly runs the sanity tests in the local environment,
without Docker or a remote test host.

CI runs linting, pytest, and the same sanity procedure on every push and pull
request to `main`. It creates
`SANITY_ROOT` with `$(mktemp -d)`, prepares the collection tree, and runs the
sanity test as another step in the main test job.

Tests cover the plugin logic and build/install the collection into a temporary
directory to verify loading by FQCN through Ansible's auto inventory plugin.
They use simulated API responses and do not require Rancher credentials.

Keep the README, full inventory example, plugin documentation, and changelog
consistent with behavior changes. Add regression tests for bug fixes. Never
include credentials, local CA bundles, or real node data in pull requests.

For a local checkout without installing the collection, configure
`inventory_plugins = ./plugins/inventory`, enable `rancher`, and use
`plugin: rancher` in a local inventory file. Installed collection usage should
always use `verdel.rancher.rancher`.

The project is licensed under GPL-3.0-or-later; contributions must use a compatible
license and retain any applicable copyright notices.


## Releases

Update `version` in `galaxy.yml` and the changelog before creating a release.
Publish a GitHub Release with the matching tag, for example `v1.0.0` for version
`1.0.0`. The release workflow checks that they match, runs Ruff and pytest,
builds the collection, publishes it to Ansible Galaxy, and then attaches
`verdel-rancher-<version>.tar.gz` to the GitHub Release.

Before publishing, configure the repository Actions secret `GALAXY_API_TOKEN`
with an Ansible Galaxy API token authorized to publish the `verdel.rancher`
collection. The GitHub Release description and asset are updated using the
automatically provided `GITHUB_TOKEN`; the release job grants it
`contents: write` permission. The release workflow also runs the sanity suite
before publishing the collection.

Publishing to Galaxy runs before updating the GitHub Release. If that step
fails, the workflow does not upload the asset or update the release description.
A full workflow rerun attempts Galaxy publication again; it is not limited to
replacing the GitHub Release asset.

Release notes are extracted from the `## <version>` section of `CHANGELOG.md`
(for example, `## 1.0.0` for tag `v1.0.0`) and used as the GitHub Release body.
The section must exist exactly once and contain text. Nested headings, including
migration notes, are preserved; other versions are excluded. Missing, duplicate,
or empty sections stop the workflow before publication. The generated notes
replace the existing release description.
