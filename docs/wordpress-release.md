# How to WP WordPress release workflow

Reusable GitHub Actions workflow that builds an installable plugin or theme zip from an existing `v*` tag, checks it, and attaches it to a GitHub Release.

This is **not** a WordPress dashboard auto-updater. Customers download zips from [howtowp.ir](https://howtowp.ir) (or from a private GitHub Release if they have access). The workflow never rewrites plugin or theme source and never creates or moves git tags.

The reusable workflow lives in this public repository and contains **no secrets**. Private product repos call it with their own `GITHUB_TOKEN`. Org default token permissions are read-only, so each caller sets `permissions: contents: write` in order to attach a release asset.

## Caller (per product repo)

`.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      tag:
        description: Existing tag to build (e.g. v1.0.0). Tags are never created or moved.
        required: true
        type: string
      dry_run:
        description: Build and verify only; do not create or update a GitHub Release.
        required: false
        type: boolean
        default: true

permissions:
  contents: write
  actions: read

jobs:
  release:
    uses: Howtowp/.github/.github/workflows/wordpress-release.yml@main
    with:
      tag: ${{ inputs.tag || github.ref_name }}
      dry_run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run }}
    secrets: inherit
```

And a per-repo `.release.yml` next to the plugin or theme (see schema below).

`workflow_dispatch` defaults to **dry-run**: it builds and uploads an artifact, and does not create or update a Release. A push of an existing `v*` tag publishes (or updates the zip on) the GitHub Release for that tag. Re-running never retags.

## Config schema (`.release.yml`)

| Key | Required | Default | Meaning |
| --- | --- | --- | --- |
| `slug` | yes | — | Product slug (repo name in most cases). |
| `zip_slug` | no | `slug` | Folder name at the zip root, and the zip filename prefix. Use this when the repo name differs (theme repo `theme-howtowp` ships as `howtowp/`). |
| `main` | yes | — | Main file, relative to the plugin/theme root (the zip root). Plugin: `foo.php`. Theme: `style.css`. |
| `plugin_dir` | no | repo root | Subdirectory that contains the plugin if it is not at the repo root (e.g. `mellat-payment-gateway`). Only this folder is packed. See [Repository layouts](#repository-layouts). |
| `type` | no | `plugin` | `plugin` or `theme`. Theme versions are read from the `style.css` `Version:` header. |
| `composer.enabled` | no | `false` | Run Composer before packing. |
| `composer.dir` | no | `.` | Directory that contains `composer.json`, **relative to the repository root** (not to `plugin_dir`). |
| `composer.command` | no | `install --no-dev --optimize-autoloader` | Arguments passed to `composer`. |
| `node.enabled` | no | `false` | Run `npm ci` (or `npm install`) and `npm run build` before packing. Leave this off when compiled assets are already committed. |
| `node.dir` | no | `.` | Directory that contains `package.json`, **relative to the repository root** (not to `plugin_dir`). |
| `node.version` | no | `"20"` | Node.js version. |
| `node.install` | no | `ci` | `ci` or `install`. |
| `node.script` | no | `build` | npm script name. |
| `exclude` | no | `[]` | Extra paths or glob patterns to drop from the zip (and to strip after `package.sh`). |
| `keep` | no | `[]` | Paths that must exist in the zip, relative to the zip root (e.g. `assets/dist/app.js`, `build/admin.js`, `vendor/autoload.php`). |
| `use_package_sh` | no | `true` | Prefer the repo’s existing `package.sh` if it produces a correctly named zip. If the zip fails checks, the job fails — it does not silently fall back. |
| `use_distignore` | no | `true` | Apply a `.distignore` file when the built-in packer runs. It is read from the plugin folder (`plugin_dir`) when it has one, otherwise from the repo root. Patterns are relative to the plugin folder and are written without a leading slash (`assets/src`, `blocks`, `*.map`). |
| `include_vendor` | no | `false` | Built-in packer only: include a `vendor/` directory. `package.sh` keeps its own vendor behaviour. |
| `allow_tests` | no | `false` | Allow a `tests/` directory in the zip. |
| `copy_root_files` | no | `true` | Built-in packer only: copy `LICENSE`, `README.md`, `CHANGELOG.md` and `readme.txt` from the **repo root** into the zip when the plugin folder does not have them. Set `false` for project layouts so the project README/CHANGELOG never ship (keep `LICENSE` and `readme.txt` inside the plugin folder). |
| `max_zip_mb` | no | `250` | Fail if the zip is larger than this (GitHub’s hard asset limit is 2 GB). |

The tag (with the leading `v` removed) must equal the Version in the plugin header, or in `style.css` for a theme. Version constants and `readme.txt` Stable tags are not rewritten and are not used for this check.

## Repository layouts

Two layouts are supported. Both produce the same zip: a single root folder `<zip_slug>/` that contains only the plugin.

### Simple layout (plugin at the repo root)

```
my-plugin/                 repo root = plugin
├── my-plugin.php  src/  assets/  readme.txt  …
├── .release.yml           (no plugin_dir)
└── .github/workflows/release.yml
```

Use it for small plugins with nothing but code. The packer reads the whole repo root and skips dev files (`.github`, `docs`, `tests`, `node_modules`, `composer.json`, …).

### Project layout (plugin in a subfolder)

Use it when the repo also holds documentation, data, research, design files or scripts.

```
my-plugin/                 repo root = project
├── my-plugin/             the shippable plugin (folder name = slug = zip root)
│   ├── my-plugin.php  src/  blocks/  assets/  languages/  readme.txt  LICENSE
│   ├── composer.json  package.json  .distignore   (dev files; never shipped)
├── docs/  data/  design/  research/  scripts/  dev/   (never shipped)
├── .release.yml
└── .github/workflows/release.yml
```

`.release.yml`:

```yaml
slug: my-plugin
zip_slug: my-plugin
type: plugin
plugin_dir: my-plugin        # only this folder is packed
main: my-plugin.php          # relative to plugin_dir
node:                        # optional build; dir is relative to the REPO root
  enabled: true
  dir: my-plugin
composer:
  enabled: false
  dir: my-plugin
copy_root_files: false       # don't copy the project README.md / CHANGELOG.md into the zip
keep: [my-plugin.php, readme.txt]
use_package_sh: false
```

How it works: the packer only walks `workspace/<plugin_dir>`, so everything beside it (`docs/`, `data/`, `design/`, `research/`, `scripts/`, `dev/`, root `README.md`, …) cannot reach the zip. The tag/Version check reads `<plugin_dir>/<main>`. `composer.dir` and `node.dir` are paths from the repository root, so for a project layout point them at the plugin folder. A `.distignore` inside the plugin folder is used for extra exclusions (for example compiled-from sources like `blocks/` or `assets/src`).

A ready-made project skeleton lives in the template repo [howtowp/plugin-template](https://github.com/howtowp/plugin-template) ("Use this template", then `scripts/init.sh`).

> **Backward compatibility.** `copy_root_files` defaults to `true` and the `.distignore` lookup falls back to the repo root, so existing callers (simple layout or `plugin_dir`) produce byte-identical zips. This was verified by packing all 29 product repos with the old and the new packer.

## What the build does

1. Checks out the requested existing tag (it will not create one).
2. Reads `.release.yml` from the tag, or — for a dry-run of an older tag — from the commit that launched the workflow, without injecting that file into the packed tree.
3. Optionally runs Composer and/or npm as configured.
4. Prefers `package.sh` when present and `use_package_sh` is true; otherwise uses the built-in packer (honouring `.distignore` when present).
5. Writes `<zip_slug>-<version>.zip` with a single root folder `<zip_slug>/`.
6. Strips junk (`.git`, `.github`, `.release.yml`, `.env`, `node_modules`, and `exclude` patterns).
7. Sanity-checks: main file path, required `keep` paths, no `node_modules` / `.git` / `.env` / `tests` (unless allowed), reasonable size, header Version inside the zip still matches the tag.
8. **Dry-run:** uploads the zip as a workflow artifact and stops. Existing Releases and tags are left alone.
9. **Publish:** creates the GitHub Release for that tag, or updates only the zip asset if the release already exists. Versions with a suffix such as `-beta` are marked prerelease.

## Actions minutes

Jobs run in the **caller** repository. Minutes used by private product repos count against the Howtowp org plan (this org is on GitHub Free). Hosting the reusable workflow in this public repo does not itself consume private minutes. Keep builds short: leave `node` and `composer` disabled unless the release zip actually needs a rebuild on the runner.

## Pinning

Callers may pin `wordpress-release.yml@<sha>` instead of `@main` once the workflow is stable. The reusable workflow checks out its companion scripts from the same ref it was called with.
