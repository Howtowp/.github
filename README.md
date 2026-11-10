# Howtowp/.github

This public repository holds:

- The organization profile (`profile/README.md`), shown on [github.com/Howtowp](https://github.com/Howtowp)
- The reusable WordPress release workflow (no secrets) used by private product repositories

Private product zips are built by [`.github/workflows/wordpress-release.yml`](.github/workflows/wordpress-release.yml). See [docs/wordpress-release.md](docs/wordpress-release.md) for the config schema, caller snippet, and behaviour.

This workflow does **not** ship a WordPress dashboard auto-updater. Customers download releases from [howtowp.ir](https://howtowp.ir).
