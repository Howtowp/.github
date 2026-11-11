# Howtowp/.github

This public repository holds:

- The organization profile (`profile/README.md`), shown on [github.com/Howtowp](https://github.com/Howtowp)
- The reusable WordPress release workflow (no secrets) used by private product repositories
- The canonical [`schemas/product.schema.json`](schemas/product.schema.json) for each product repo's bilingual `product.json` (store copy that an agent fills; howtowp-core later syncs it to howtowp.ir). A convenience copy also lives in [plugin-template](https://github.com/Howtowp/plugin-template/blob/main/product.schema.json). The writing guide for the long description and feature cards is [docs/product-copy.md](docs/product-copy.md).

Private product zips are built by [`.github/workflows/wordpress-release.yml`](.github/workflows/wordpress-release.yml). See [docs/wordpress-release.md](docs/wordpress-release.md) for the config schema, caller snippet, and behaviour.

Starting a new plugin? Use the template repo [howtowp/plugin-template](https://github.com/howtowp/plugin-template) (project layout, PSR-4 modules, release workflow). Layouts are documented in [docs/wordpress-release.md](docs/wordpress-release.md#repository-layouts).

This workflow does **not** ship a WordPress dashboard auto-updater. Customers download releases from [howtowp.ir](https://howtowp.ir).
