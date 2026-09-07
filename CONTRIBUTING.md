# Contributing

Use Python 3.12 or newer for development with the latest supported Ansible Core.
Create a virtual environment and install the development dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

Run the checks before opening a pull request:

```sh
ruff check .
ruff format --check .
pytest -q
ansible-galaxy collection build --output-path dist --force
```

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
`contents: write` permission.

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
