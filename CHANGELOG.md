# Changelog

## 1.0.0

Initial collection packaging for `verdel.rancher`.

- Discover Kubernetes nodes through the Rancher 2.x API proxy, with pagination.
- Select clusters by name or ID and optionally skip disconnected clusters.
- Use cluster names for inventory hosts and cluster groups.
- Select SSH addresses and expose Kubernetes labels, roles, and node status.
- Support global and per-cluster compose expressions and constructed groups.
- Provide TLS verification, custom CA bundles, and request timeouts.
- Support Ansible inventory caching with memory and persistent cache backends.
- Install the inventory plugin as `verdel.rancher.rancher`.
