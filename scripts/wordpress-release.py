#!/usr/bin/env python3
"""Build and verify a How to WP WordPress release zip. Never rewrites plugin/theme source."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required. Install it with: pip install pyyaml")


ALWAYS_STRIP_NAMES = {
    ".git",
    ".github",
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".release.yml",
    ".cursor",
    ".qoder",
    ".refact",
    ".idea",
    ".vscode",
    ".DS_Store",
    "__MACOSX",
    "Thumbs.db",
}

ALWAYS_STRIP_PREFIXES = (".env", "._")
ALWAYS_STRIP_SUFFIXES = (".log",)

DEFAULT_PACK_SKIP = {
    ".git",
    ".github",
    ".gitignore",
    ".gitattributes",
    ".release.yml",
    ".DS_Store",
    "docs",
    "package.sh",
    "dist",
    "node_modules",
    ".env",
    "tests",
    "phpunit.xml.dist",
    "phpunit.xml",
    "phpcs.xml.dist",
    "phpcs.xml",
    "phpstan.neon.dist",
    "package.json",
    "package-lock.json",
    "composer.lock",
    "composer.json",
    "bin",
    "sample",
    "admin-app",
    "webpack.config.js",
    "tailwind.config.js",
    "postcss.config.js",
    "Makefile",
    "docker",
    "docker-compose.yml",
    ".wp-env.json",
    ".cursor",
    "TESTING.md",
    "scripts",
}

DEFAULT_PACK_SUFFIXES = {".zip", ".tar", ".tar.gz", ".rar", ".log", ".phar"}


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_config(path: Path) -> dict:
    if not path.is_file():
        die(
            f"release config not found at {path}. "
            "Add a .release.yml in the product repo (see Howtowp/.github/docs/wordpress-release.md)."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        die(f"{path} must be a YAML mapping")
    slug = data.get("slug")
    main = data.get("main")
    if not slug or not main:
        die(f"{path} must set both 'slug' and 'main'")
    composer = data.get("composer") or {}
    node = data.get("node") or {}
    return {
        "slug": str(slug),
        "zip_slug": str(data.get("zip_slug") or slug),
        "main": str(main).lstrip("./"),
        "plugin_dir": str(data.get("plugin_dir") or "").strip().strip("/"),
        "type": str(data.get("type") or "plugin"),
        "composer": {
            "enabled": bool(composer.get("enabled", False)),
            "dir": str(composer.get("dir") or "."),
            "command": str(composer.get("command") or "install --no-dev --optimize-autoloader"),
        },
        "node": {
            "enabled": bool(node.get("enabled", False)),
            "dir": str(node.get("dir") or "."),
            "version": str(node.get("version") or "20"),
            "install": str(node.get("install") or "ci"),
            "script": str(node.get("script") or "build"),
        },
        "exclude": [str(x) for x in (data.get("exclude") or [])],
        "keep": [str(x).lstrip("./") for x in (data.get("keep") or [])],
        "use_package_sh": bool(data.get("use_package_sh", True)),
        "use_distignore": bool(data.get("use_distignore", True)),
        "allow_tests": bool(data.get("allow_tests", False)),
        "include_vendor": bool(data.get("include_vendor", False)),
        "max_zip_mb": float(data.get("max_zip_mb") or 250),
    }


def parse_tag(tag: str) -> str:
    tag = tag.strip()
    if not re.fullmatch(r"v[0-9][0-9A-Za-z._-]*", tag):
        die(
            f"tag '{tag}' is not a valid release tag. Expected vX.Y.Z (optional suffix, e.g. v1.0.0-beta)."
        )
    return tag[1:]


def read_header_version(main_file: Path, typ: str) -> str:
    if not main_file.is_file():
        die(f"main file not found: {main_file}")
    text = main_file.read_text(encoding="utf-8", errors="replace")
    patterns = [
        re.compile(r"^\s*\*\s*Version:\s*(\S+)", re.MULTILINE),
        re.compile(r"^Version:\s*(\S+)", re.MULTILINE),
    ]
    version = None
    for pat in patterns:
        match = pat.search(text)
        if match:
            version = match.group(1).strip().strip(",")
            break
    if not version:
        kind = "style.css theme header" if typ == "theme" else "plugin header"
        die(f"could not read Version from {kind} in {main_file}")
    return version


def plugin_root(workspace: Path, cfg: dict) -> Path:
    if cfg["plugin_dir"]:
        root = (workspace / cfg["plugin_dir"]).resolve()
        if not root.is_dir():
            die(f"plugin_dir '{cfg['plugin_dir']}' does not exist under {workspace}")
        return root
    return workspace.resolve()


def main_file_path(workspace: Path, cfg: dict) -> Path:
    # main is relative to the plugin/theme root (the zip root contents).
    return plugin_root(workspace, cfg) / cfg["main"]


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def find_zip(dist: Path, zip_slug: str, version: str) -> Path:
    expected = dist / f"{zip_slug}-{version}.zip"
    if expected.is_file():
        return expected
    zips = sorted(dist.glob("*.zip"))
    die(
        f"package.sh did not produce {expected.name} in {dist}. "
        + ("Found: " + ", ".join(p.name for p in zips) if zips else "No zip files were created.")
    )
    return expected


def load_distignore(workspace: Path) -> list[str]:
    path = workspace / ".distignore"
    if not path.is_file():
        return []
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def matches_any(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    name = Path(rel).name
    for pat in patterns:
        pat = pat.rstrip("/")
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
        if rel == pat or rel.startswith(pat + "/"):
            return True
    return False


def built_in_pack(workspace: Path, cfg: dict, version: str, dest: Path) -> Path:
    zip_slug = cfg["zip_slug"]
    skip = set(DEFAULT_PACK_SKIP)
    if not cfg["include_vendor"]:
        skip.add("vendor")
    if cfg["allow_tests"]:
        skip.discard("tests")
    extra = list(cfg["exclude"])
    if cfg["use_distignore"]:
        extra.extend(load_distignore(workspace))
    src_root = plugin_root(workspace, cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    staging = Path(tempfile.mkdtemp(prefix=f"{zip_slug}-zip-"))
    plugin = staging / zip_slug
    plugin.mkdir()
    try:
        for item in src_root.iterdir():
            if item.name in skip or item.name.startswith(".env") or item.name.startswith("."):
                continue
            if any(item.name.endswith(suf) for suf in DEFAULT_PACK_SUFFIXES):
                continue
            if matches_any(item.name, extra):
                continue
            target = plugin / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    target,
                    ignore=lambda _d, names: {
                        n
                        for n in names
                        if n in {".DS_Store", "node_modules", ".git"} or n.endswith("~")
                    },
                )
            else:
                shutil.copy2(item, target)
        for extra_name in ("LICENSE", "README.md", "CHANGELOG.md", "readme.txt"):
            src = workspace / extra_name
            target = plugin / extra_name
            if src.exists() and not target.exists() and src.resolve() != target.resolve():
                shutil.copy2(src, target)
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in plugin.rglob("*"):
                if path.name == ".DS_Store" or path.name.endswith("~"):
                    continue
                rel = path.relative_to(staging).as_posix()
                inner = rel.split("/", 1)[1] if "/" in rel else ""
                if inner and matches_any(inner, extra):
                    continue
                zf.write(path, rel)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest


def should_strip(name: str, cfg: dict) -> bool:
    parts = name.split("/")
    # zip root is the slug folder
    inner_parts = parts[1:] if parts else []
    inner = "/".join(inner_parts)
    if inner and (
        inner in cfg["keep"]
        or any(k == inner or inner.startswith(k.rstrip("/") + "/") for k in cfg["keep"])
    ):
        # still strip true junk even if somehow listed
        base = parts[-1] if parts else name
        if base in ALWAYS_STRIP_NAMES or base.startswith(ALWAYS_STRIP_PREFIXES):
            return True
        return False
    for part in parts:
        if part in ALWAYS_STRIP_NAMES:
            return True
        if part.startswith(ALWAYS_STRIP_PREFIXES):
            return True
        if part.endswith(ALWAYS_STRIP_SUFFIXES):
            return True
    if inner and matches_any(inner, cfg["exclude"]):
        return True
    if not cfg["allow_tests"] and (inner == "tests" or inner.startswith("tests/")):
        return True
    if "node_modules" in parts:
        return True
    return False


def sanitize_zip(src: Path, dest: Path, cfg: dict) -> tuple[Path, int]:
    removed = 0
    tmp = dest.with_suffix(".sanitizing.zip")
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(
        tmp, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                if should_strip(name.rstrip("/"), cfg):
                    removed += 1
                    continue
            elif should_strip(name, cfg):
                removed += 1
                print(f"  stripping {name}")
                continue
            # skip obviously broken absolute/parent paths
            if name.startswith("/") or ".." in Path(name).parts:
                removed += 1
                continue
            data = zin.read(info)
            zout.writestr(info, data)
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)
    return dest, removed


def sanity_check(zip_path: Path, cfg: dict, version: str) -> list[str]:
    zip_slug = cfg["zip_slug"]
    main_in_zip = f"{zip_slug}/{cfg['main']}"
    issues: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        roots = sorted({n.split("/")[0] for n in names if n})
        if roots != [zip_slug]:
            issues.append(
                f"zip root must be a single folder named '{zip_slug}/' (found {roots})"
            )
        if main_in_zip not in names and f"{main_in_zip}/" not in names:
            issues.append(f"main file missing at {main_in_zip}")
        forbidden = []
        for n in names:
            parts = n.split("/")
            if "node_modules" in parts:
                forbidden.append(n)
            if ".git" in parts:
                forbidden.append(n)
            base = parts[-1] if parts else n
            if base == ".env" or base.startswith(".env."):
                forbidden.append(n)
            if not cfg["allow_tests"] and (n == f"{zip_slug}/tests" or n.startswith(f"{zip_slug}/tests/")):
                forbidden.append(n)
        if forbidden:
            preview = ", ".join(forbidden[:8])
            more = f" (+{len(forbidden) - 8} more)" if len(forbidden) > 8 else ""
            issues.append(f"zip contains forbidden paths: {preview}{more}")
        for keep in cfg["keep"]:
            expected = f"{zip_slug}/{keep}"
            as_dir = expected.rstrip("/") + "/"
            if expected not in names and as_dir not in names and not any(
                n == expected or n.startswith(expected.rstrip("/") + "/") for n in names
            ):
                issues.append(f"required path missing from zip: {expected}")
        size = zip_path.stat().st_size
        max_bytes = int(cfg["max_zip_mb"] * 1024 * 1024)
        if size > max_bytes:
            issues.append(
                f"zip is {size} bytes, over the configured max of {cfg['max_zip_mb']} MB"
            )
        if size < 200:
            issues.append(f"zip is suspiciously small ({size} bytes)")
        # header version inside the zip should still match
        if main_in_zip in names:
            raw = zf.read(main_in_zip).decode("utf-8", errors="replace")
            tmp = Path(tempfile.mkstemp(suffix=Path(cfg["main"]).suffix)[1])
            try:
                tmp.write_text(raw, encoding="utf-8")
                inside = read_header_version(tmp, cfg["type"])
            finally:
                tmp.unlink(missing_ok=True)
            if inside != version:
                issues.append(
                    f"Version inside the zip ({inside}) does not match the tag version ({version})"
                )
    return issues


def list_zip(zip_path: Path) -> list[tuple[str, int, str]]:
    rows = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            crc = f"{info.CRC:08x}"
            rows.append((info.filename.replace("\\", "/"), info.file_size, crc))
    return sorted(rows)


def is_prerelease(version: str) -> bool:
    return "-" in version


def publish_release(repo: str, tag: str, version: str, zip_path: Path) -> None:
    env = os.environ.copy()
    if not env.get("GH_TOKEN") and env.get("GITHUB_TOKEN"):
        env["GH_TOKEN"] = env["GITHUB_TOKEN"]
    title = tag
    view = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "tagName"],
        capture_output=True,
        text=True,
        env=env,
    )
    if view.returncode == 0:
        print(f"Release {tag} already exists; uploading asset only (tag will not be moved).")
        subprocess.run(
            ["gh", "release", "upload", tag, str(zip_path), "--repo", repo, "--clobber"],
            check=True,
            env=env,
        )
        return
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        str(zip_path),
        "--repo",
        repo,
        "--title",
        title,
        "--notes",
        f"Installable zip for {tag}. Customers download this from howtowp.ir; there is no in-dashboard auto-updater.",
    ]
    if is_prerelease(version):
        cmd.append("--prerelease")
    # Do not pass --target or create a tag; the tag must already exist.
    subprocess.run(cmd, check=True, env=env)
    print(f"Created GitHub Release {tag} with {zip_path.name}")


def write_github_output(path: Path | None, values: dict[str, str]) -> None:
    if not path:
        return
    with path.open("a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    cfg = load_config(args.config)
    version = parse_tag(args.tag)
    main_path = main_file_path(workspace, cfg)
    header = read_header_version(main_path, cfg["type"])
    kind = "style.css" if cfg["type"] == "theme" else "plugin header"
    print(f"tag={args.tag}  header Version ({kind} in {main_path.relative_to(workspace)})={header}")
    if header != version:
        die(
            f"git tag '{args.tag}' does not match the {kind} Version '{header}' in "
            f"{main_path.relative_to(workspace)}. Expected Version {version}. "
            f"Update the header to {version} before tagging, or tag v{header}. "
            "This workflow does not rewrite plugin or theme source files."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    package_sh = workspace / "package.sh"
    used = "builtin"
    built: Path | None = None

    if cfg["use_package_sh"] and package_sh.is_file():
        print(f"Using existing {package_sh}")
        mode = package_sh.stat().st_mode
        if not (mode & stat.S_IXUSR):
            package_sh.chmod(mode | stat.S_IXUSR)
        run(["bash", str(package_sh)], cwd=workspace)
        built = find_zip(workspace / "dist", cfg["zip_slug"], version)
        used = "package.sh"
    else:
        if cfg["use_package_sh"] and not package_sh.is_file():
            print("package.sh not found; using built-in packer")
        dest = args.output_dir / f"{cfg['zip_slug']}-{version}.zip"
        built = built_in_pack(workspace, cfg, version, dest)

    final_zip = args.output_dir / f"{cfg['zip_slug']}-{version}.zip"
    final_zip, stripped = sanitize_zip(built, final_zip, cfg)
    print(f"sanitized zip at {final_zip} (stripped {stripped} entries)")

    issues = sanity_check(final_zip, cfg, version)
    if issues:
        for issue in issues:
            print(f"error: {issue}", file=sys.stderr)
        if used == "package.sh":
            die(
                "package.sh produced a zip that failed the sanity checks. "
                "Fix package.sh, or set use_package_sh: false in .release.yml to use the built-in packer. "
                "Plugin source was not modified."
            )
        die("built-in packer produced a zip that failed the sanity checks")

    entries = list_zip(final_zip)
    print(f"ok: {final_zip.name}  files={len(entries)}  bytes={final_zip.stat().st_size}")
    print(f"root={cfg['zip_slug']}/  main={cfg['zip_slug']}/{cfg['main']}")

    if args.dry_run:
        print("dry-run: skipping GitHub Release create/update (existing releases and tags are untouched)")
    else:
        if not args.repo:
            die("GITHUB_REPOSITORY / --repo is required to publish a release")
        publish_release(args.repo, args.tag, version, final_zip)

    summary = {
        "tag": args.tag,
        "version": version,
        "slug": cfg["slug"],
        "zip_slug": cfg["zip_slug"],
        "main": cfg["main"],
        "zip": str(final_zip),
        "zip_name": final_zip.name,
        "bytes": final_zip.stat().st_size,
        "files": len(entries),
        "packer": used,
        "stripped": stripped,
        "dry_run": args.dry_run,
        "prerelease": is_prerelease(version),
    }
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    write_github_output(Path(gh_out) if gh_out else None, {
        "zip_path": str(final_zip),
        "zip_name": final_zip.name,
        "version": version,
        "dry_run": "true" if args.dry_run else "false",
        "prerelease": "true" if is_prerelease(version) else "false",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
