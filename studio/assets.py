"""Small local catalog, Poly Haven acquisition, and two-stage Blender preparation.

Poly Haven supplies CC0 assets; live API use is credited separately. Search
matches describe candidates, never a verified identity or geometry match.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
import zipfile

from .common import REPO, StudioError, check_id, file_hash, lock, read_json, safe_path, stable_hash, write_json

API = "https://api.polyhaven.com"
AMBIENTCG_API = "https://ambientcg.com/api/v2/full_json"
AMBIENTCG_LICENSE = "https://docs.ambientcg.com/license/"
# ambientCG archive member suffix -> map role; other members (NormalDX, usdc, mtlx, blend) are not kept.
AMBIENTCG_MAPS = {"_Color": "base_color", "_NormalGL": "normal", "_Roughness": "roughness", "_Metalness": "metallic",
                  "_Displacement": "displacement", "_AmbientOcclusion": "ambient_occlusion", "_Opacity": "opacity"}
USER_AGENT = "TechnicalReelStudio/0.1"
FORMATS = (".glb", ".gltf", ".blend", ".fbx", ".obj")
# Callers may describe where a file came from, but only studio code decides whether it is cleared.
CALLER_SOURCE_KEYS = {"provider", "page_url", "attribution", "authors", "license_id", "license_evidence", "notes", "use_status"}
# Provider names whose licence is asserted by studio adapter code, never by caller JSON.
ADAPTER_PROVIDERS = {"polyhaven", "ambientcg", "blenderkit", "factory", "fal"}
USE_STATUS_RANK = {"cleared": 2, "review_only": 1, "internal_preview_only": 0, "blocked": 0}



def _portable(data, base, to_relative):
    """Asset manifests store file paths relative to their own folder, so a library moves between machines (a clone)
    unchanged; in memory they are absolute. Old manifests with absolute paths under another root are rebased by
    their relative position (original/<relative_path>, prepared/<name>)."""
    base = Path(base)
    def one(value, fallback=None):
        if not isinstance(value, str) or not value:
            return value
        path = Path(value)
        if to_relative:
            return str(path.relative_to(base)) if path.is_absolute() and path.is_relative_to(base) else value
        if not path.is_absolute():
            return str(base / path)
        if not path.exists() and fallback is not None and (base / fallback).exists():
            return str(base / fallback)
        return value
    out = dict(data)
    out["files"] = [{**item, "path": one(item.get("path"), None if to_relative else f"original/{item.get('relative_path', '')}")}
                    for item in data.get("files") or []]
    if data.get("prepared_scene"):
        out["prepared_scene"] = one(data["prepared_scene"], None if to_relative else f"prepared/{Path(data['prepared_scene']).name}")
    inspection = data.get("inspection")
    if isinstance(inspection, dict) and inspection.get("preview_paths"):
        out["inspection"] = {**inspection, "preview_paths": [one(v, None if to_relative else f"prepared/{Path(v).name}") for v in inspection["preview_paths"]]}
    return out


def read_manifest(path):
    path = Path(path).resolve()
    return _portable(read_json(path), path.parent, to_relative=False)


def write_manifest(path, data):
    path = Path(path).resolve()
    write_json(path, _portable(data, path.parent, to_relative=True))

def _now():
    return datetime.now(timezone.utc).isoformat()


def _request(url):
    if urlparse(url).scheme not in {"https", "http"}:
        raise StudioError("INPUT_INVALID", "Download URLs must use HTTP or HTTPS")
    for attempt in range(3):
        try:
            return urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=45)
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise StudioError("ASSET_ACCESS_REQUIRED", f"HTTP {exc.code} retrieving {url}", retryable=exc.code >= 500)
            retry = exc.headers.get("Retry-After", "")
            time.sleep(min(float(retry) if retry.isdigit() else 2 ** attempt, 10))
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise StudioError("ASSET_ACCESS_REQUIRED", f"Network unavailable: {exc}", retryable=True)
            time.sleep(2 ** attempt)


def _api(endpoint):
    with _request(API + endpoint) as response:
        return json.load(response)


def _relative(value):
    value = unquote(str(value)).replace("\\", "/")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise StudioError("INPUT_INVALID", f"Unsafe asset relative path: {value}")
    return Path(*path.parts)


def extract_zip(archive, destination, max_bytes=None, max_entries=None):
    """Reject traversal, symlinks and oversized (zip-bomb) archives before extracting anything."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if max_entries is not None and len(infos) > max_entries:
            raise StudioError("BUDGET_EXHAUSTED", f"Archive has {len(infos)} entries (limit {max_entries})")
        if max_bytes is not None and sum(entry.file_size for entry in infos) > max_bytes:
            raise StudioError("BUDGET_EXHAUSTED", f"Archive expands beyond {max_bytes} bytes")
        entries = [(entry, destination / _relative(entry.filename)) for entry in infos]
        for entry, target in entries:
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise StudioError("INPUT_INVALID", "Asset archives may not contain symlinks")
            if not target.resolve().is_relative_to(destination):
                raise StudioError("INPUT_INVALID", "Asset archive escapes the destination")
        bundle.extractall(destination)


def search_assets(request, library=None, online=True):
    if isinstance(request, (str, Path)):
        request = read_json(request)
    if not isinstance(request, dict):
        raise StudioError("INPUT_INVALID", "Asset request must be a JSON object")
    query = request.get("query") or request.get("name") or request.get("subject")
    if not isinstance(query, str) or not query.strip():
        raise StudioError("INPUT_INVALID", "Asset request requires a nonempty query")
    limit = min(5, max(1, int(request.get("limit", 5))))
    library = Path(library or request.get("library", REPO / "library"))
    index = read_json(library / "index.json") if (library / "index.json").exists() else {"assets": []}
    entries = index.get("assets", []) if isinstance(index, dict) else index
    if isinstance(entries, dict):
        entries = list(entries.values())
    tokens = query.lower().split()
    local = []
    for entry in entries:
        text = json.dumps({key: entry.get(key) for key in ("name", "tags", "roles", "capabilities")}, ensure_ascii=False).lower()
        score = sum(token in text for token in tokens)
        if score and (not request.get("role") or request["role"] in entry.get("roles", [])):
            candidate = {**entry, "provider": "local", "search_score": score, "identity_status": "unverified"}
            if candidate.get("manifest_path"):
                candidate["manifest_path"] = str((library / candidate["manifest_path"]).resolve())
            local.append(candidate)
    local.sort(key=lambda item: (-item["search_score"], item.get("asset_id", "")))
    warnings, remote = [], []
    providers = request.get("providers", ["polyhaven"])
    if online and "ambientcg" in providers:
        try:
            remote += _search_ambientcg(query, request, limit, library)
        except StudioError as exc:
            warnings.append(str(exc))
    if online and "polyhaven" in providers:
        kind = request.get("type", "models")
        if kind not in {"models", "textures", "hdris"}:
            raise StudioError("INPUT_INVALID", "Asset type must be models, textures, or hdris")
        cache = library / "cache" / f"search_{stable_hash({'q': query, 't': kind})[:16]}.json"
        try:
            if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
                found = read_json(cache)
            else:
                found = _api("/search?" + urlencode({"q": query, "t": kind, "limit": limit}))
                write_json(cache, found)
            if isinstance(found, dict) and "results" in found:
                found = found["results"]
            rows = list(found.items()) if isinstance(found, dict) else [(row.get("id") or row.get("asset_id") or row.get("slug"), row) for row in found]
            for asset_id, info in rows[:limit]:
                if not asset_id or not isinstance(info, dict):
                    continue
                if "name" not in info:
                    info = {**_api("/info/" + quote(asset_id)), **info}
                remote.append({"asset_id": asset_id, "name": info.get("name", asset_id), "tags": info.get("tags", []),
                               "provider": "polyhaven", "type": kind, "page_url": f"https://polyhaven.com/a/{quote(asset_id)}",
                               "resolution": request.get("resolution", "1k"), "identity_status": "unverified",
                               "search_metadata": info, "credit": "Assets from Poly Haven"})
        except StudioError as exc:
            warnings.append(str(exc))
    candidates = (local + remote)[:limit]
    credits = sorted({c["credit"] for c in remote if c.get("credit")})
    return {"status": "candidates_found" if candidates else "no_candidates", "candidates": candidates,
            "warnings": warnings, "artifacts": [], "credit": "; ".join(credits) or None}


def _search_ambientcg(query, request, limit, library):
    kind = request.get("type", "textures")
    if kind != "textures":
        return []
    cache = library / "cache" / f"ambientcg_{stable_hash({'q': query})[:16]}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        found = read_json(cache)
    else:
        with _request(AMBIENTCG_API + "?" + urlencode({"q": query, "type": "Material", "limit": limit, "include": "tagData"})) as response:
            found = json.load(response)
        write_json(cache, found)
    rows = []
    for asset in found.get("foundAssets", [])[:limit]:
        native = asset["assetId"]
        rows.append({"asset_id": "ambientcg_" + native.lower(), "provider_asset_id": native, "name": asset.get("displayName", native),
                     "tags": asset.get("tags", []), "provider": "ambientcg", "type": "textures",
                     "page_url": f"https://ambientcg.com/view?id={quote(native)}", "resolution": request.get("resolution", "1k"),
                     "identity_status": "unverified", "credit": "Materials from ambientCG (CC0)"})
    return rows


def _file_nodes(data, trail=()):
    if isinstance(data, dict):
        if "url" in data:
            yield trail, data
        else:
            for key, value in data.items():
                yield from _file_nodes(value, (*trail, key))


def select_files(files, kind="models", resolution="1k", selector=None):
    """Select one complete model, or the requested resolution's texture/HDRI files."""
    nodes = list(_file_nodes(files))
    if selector:
        chosen = [(trail, node) for trail, node in nodes if "/".join(trail) == selector]
        if not chosen:
            raise StudioError("INPUT_INVALID", f"File selector absent from provider metadata: {selector}")
    elif kind == "models":
        chosen = [(trail, node) for trail, node in nodes if Path(unquote(urlparse(node["url"]).path)).suffix.lower() in FORMATS]
        chosen.sort(key=lambda item: (resolution not in item[0], FORMATS.index(Path(unquote(urlparse(item[1]["url"]).path)).suffix.lower()), int(item[1].get("size", 0))))
        chosen = chosen[:1]
    elif kind == "hdris":
        chosen = [(trail, node) for trail, node in nodes if resolution in trail and Path(urlparse(node["url"]).path).suffix in {".hdr", ".exr"}]
        chosen.sort(key=lambda item: (Path(urlparse(item[1]["url"]).path).suffix != ".hdr", int(item[1].get("size", 0))))
        chosen = chosen[:1]
    else:
        chosen = [(trail, node) for trail, node in nodes if resolution in trail and Path(urlparse(node["url"]).path).suffix.lower() in {".jpg", ".png", ".exr"}]
        # Select one file per semantic map, preferring JPG for lightweight previews.
        maps = {}
        for trail, node in sorted(chosen, key=lambda item: Path(urlparse(item[1]["url"]).path).suffix != ".jpg"):
            maps.setdefault(trail[0], (trail, node))
        chosen = list(maps.values())
    if not chosen:
        raise StudioError("ASSET_NOT_SUITABLE", "Provider lists no supported files for the requested format/resolution")
    result = []
    for trail, node in chosen:
        filename = unquote(urlparse(node["url"]).path).rsplit("/", 1)[-1]
        result.append({**node, "relative_path": str(_relative(node.get("relative_path", filename))), "role": "primary", "selector": "/".join(trail)})
        includes = node.get("include", {})
        includes = includes.items() if isinstance(includes, dict) else [(item.get("path") or item.get("name"), item) for item in includes]
        for relative, dependency in includes:
            dependency = {"url": dependency} if isinstance(dependency, str) else dependency
            result.append({**dependency, "relative_path": str(_relative(relative)), "role": "dependency"})
    unique = {}
    for item in result:
        path = item["relative_path"]
        if path in unique and unique[path]["url"] != item["url"]:
            raise StudioError("INPUT_INVALID", f"Conflicting download destination: {path}")
        unique[path] = item
    return list(unique.values())


def _matches(path, spec):
    if not path.is_file():
        return False
    if spec.get("size") is not None and path.stat().st_size != int(spec["size"]):
        return False
    for name in ("sha256", "md5"):
        if spec.get(name):
            digest = hashlib.new(name)
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().lower() != spec[name].lower():
                return False
    return True


def _download(spec, destination, file_limit, remaining):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _matches(destination, spec) and any(spec.get(key) for key in ("sha256", "md5")):
        return 0
    if int(spec.get("size", 0)) > min(file_limit, remaining):
        raise StudioError("BUDGET_EXHAUSTED", f"Download exceeds byte limit: {destination.name}")
    partial = destination.with_name(destination.name + f".{os.getpid()}.part")
    written = 0
    try:
        with _request(spec["url"]) as response, partial.open("wb") as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                written += len(chunk)
                if written > min(file_limit, remaining):
                    raise StudioError("BUDGET_EXHAUSTED", f"Download exceeded byte limit: {destination.name}")
                output.write(chunk)
        if not _matches(partial, spec):
            raise StudioError("ASSET_NOT_SUITABLE", f"Downloaded file size or checksum mismatch: {destination.name}")
        os.replace(partial, destination)
        return written
    finally:
        partial.unlink(missing_ok=True)


def _verify_gltf(path):
    if path.suffix.lower() != ".gltf":
        return
    data = read_json(path)
    for item in data.get("buffers", []) + data.get("images", []):
        uri = item.get("uri", "")
        if uri and not uri.startswith("data:"):
            dependency = path.parent / _relative(uri)
            if not dependency.is_file():
                raise StudioError("MISSING_DEPENDENCY", f"glTF dependency missing: {uri}")


def _acquisition_key(candidate):
    data = {key: candidate.get(key) for key in ("asset_id", "primitive", "file_selector", "manifest_path")}
    data.update({"version": candidate.get("version", "v0001"), "source_units": candidate.get("source_units", "meters")})
    for optional in ("provider_asset_id", "scale_basis"):  # only when present, so existing hashes are unchanged
        if candidate.get(optional) is not None:
            data[optional] = candidate[optional]
    data.update({"provider": candidate.get("provider", candidate.get("source", {}).get("provider", "local")),
                 "type": candidate.get("type", "models"), "resolution": candidate.get("resolution", "1k")})
    specs = list(candidate.get("files", []))
    if candidate.get("path"):
        specs = [{"local_path": candidate["path"], "relative_path": Path(candidate["path"]).name}, *specs]
    data["files"] = []
    for spec in specs:
        item = {key: spec.get(key) for key in ("url", "relative_path", "sha256", "md5", "size", "role")}
        local = spec.get("local_path") or spec.get("path")
        if local:
            path = Path(local).resolve()
            item.update({"source_path": str(path), "source_sha256": file_hash(path) if path.is_file() else None})
        data["files"].append(item)
    return stable_hash(data)


def _fetch_ambientcg(candidate, out, file_limit, download_limit):
    native = candidate.get("provider_asset_id")
    if not native or not re.fullmatch(r"[A-Za-z0-9_]+", native):
        raise StudioError("INPUT_INVALID", "ambientCG candidates need provider_asset_id such as Metal009")
    with _request(AMBIENTCG_API + "?" + urlencode({"id": native, "include": "downloadData,tagData"})) as response:
        found = json.load(response).get("foundAssets", [])
    if not found:
        raise StudioError("ASSET_NOT_SUITABLE", f"ambientCG has no asset {native}")
    asset = found[0]
    attribute = candidate.get("resolution", "1k").upper() + "-JPG"
    downloads = [d for folder in asset["downloadFolders"].values() for category in folder["downloadFiletypeCategories"].values()
                 for d in category["downloads"] if d.get("attribute") == attribute and d.get("filetype") == "zip"]
    if not downloads:
        raise StudioError("ASSET_NOT_SUITABLE", f"ambientCG {native} has no {attribute} zip")
    archive = out / "download" / downloads[0]["fileName"]
    # Listed sizes are approximate, so they bound the download instead of being checked exactly.
    downloaded = _download({"url": downloads[0]["downloadLink"]}, archive, min(file_limit, int(downloads[0].get("size", file_limit)) * 2), download_limit)
    staging = out / "download" / "extracted"
    shutil.rmtree(staging, ignore_errors=True)
    extract_zip(archive, staging, max_bytes=file_limit, max_entries=64)
    files = []
    for member in sorted(staging.rglob("*")):
        role = next((r for suffix, r in AMBIENTCG_MAPS.items() if member.stem.endswith(suffix)), None)
        if member.is_file() and role:
            destination = safe_path(out / "original", member.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(member), destination)
            files.append({"path": str(destination), "relative_path": member.name, "sha256": file_hash(destination),
                          "bytes": destination.stat().st_size, "role": role})
    shutil.rmtree(out / "download", ignore_errors=True)
    if not any(f["role"] == "base_color" for f in files):
        raise StudioError("ASSET_NOT_SUITABLE", f"ambientCG {native} archive has no Color map")
    source = {"provider": "ambientcg", "provider_asset_id": native, "page_url": f"https://ambientcg.com/view?id={native}",
              "license_id": "CC0-1.0", "license_evidence": AMBIENTCG_LICENSE, "retrieved_at": _now(), "use_status": "cleared",
              "attribution": "Materials from ambientCG", "authors": {}, "license_asserted_by": "studio", "archive": downloads[0]["fileName"]}
    info = {"name": asset.get("displayName", native), "tags": asset.get("tags", [])}
    return info, files, downloaded, source


def _caller_source(candidate, provider):
    """Source record for caller-described files: never cleared unless studio itself generated it."""
    claimed = candidate.get("source", {})
    if not isinstance(claimed, dict) or set(claimed) - CALLER_SOURCE_KEYS:
        raise StudioError("INPUT_INVALID", f"Unexpected source fields: {sorted(set(claimed) - CALLER_SOURCE_KEYS) if isinstance(claimed, dict) else claimed}")
    if provider in ADAPTER_PROVIDERS or claimed.get("provider", provider) in ADAPTER_PROVIDERS:
        raise StudioError("INPUT_INVALID", f"Provider {provider!r} is reserved for studio adapters; fetch through its adapter")
    # Pure numeric primitives are generated by studio from the candidate itself.
    generated = bool(candidate.get("primitive")) and not candidate.get("files") and not candidate.get("path")
    status = "cleared" if generated else "review_only"
    asked = claimed.get("use_status", status)
    if USE_STATUS_RANK.get(asked, 3) > USE_STATUS_RANK[status]:
        raise StudioError("INPUT_INVALID", f"use_status {asked!r} cannot be self-asserted; caller-provided files are {status}",
                          recovery="Record licence evidence in source and have a human review it; studio adapters set cleared")
    return {"provider": provider, "page_url": None, "license_id": None, "license_evidence": None, "attribution": None,
            **{k: v for k, v in claimed.items() if k != "use_status"}, "retrieved_at": _now(), "use_status": asked,
            "license_asserted_by": "studio" if generated else "caller"}


def _fetch_asset(candidate, asset_root=None, file_limit=1073741824, download_limit=3221225472, *, trusted_source=None):
    if isinstance(candidate, (str, Path)):
        candidate = read_json(candidate)
    if not isinstance(candidate, dict):
        raise StudioError("INPUT_INVALID", "Asset candidate must be a JSON object")
    asset_id = candidate.get("asset_id")
    check_id(asset_id)
    root = Path(asset_root or REPO / "library" / "assets").resolve()
    version = candidate.get("version", "v0001")
    check_id(version)
    if not re.fullmatch(r"v\d+", version):
        raise StudioError("INPUT_INVALID", "Asset version must be v followed by digits")
    if candidate.get("manifest_path") and not any(candidate.get(key) for key in ("files", "path", "primitive")):
        referenced = read_manifest(candidate["manifest_path"])
        if (referenced.get("asset_id"), referenced.get("version")) != (asset_id, version):
            raise StudioError("REVISION_CONFLICT", "Local catalog manifest has a different asset identity/version")
        if not all(Path(item["path"]).is_file() and file_hash(item["path"]) == item["sha256"] for item in referenced.get("files", [])):
            raise StudioError("MISSING_DEPENDENCY", "Local catalog asset has missing or changed original files")
        return {**referenced, "manifest_path": str(Path(candidate["manifest_path"]).resolve()), "reused": True,
                "artifacts": [str(Path(candidate["manifest_path"]).resolve())]}
    out = root / asset_id / version
    manifest_path = out / "asset.json"
    acquisition_key = _acquisition_key(candidate)
    if manifest_path.exists():
        cached = read_manifest(manifest_path)
        if cached.get("acquisition_request_hash") != acquisition_key:
            raise StudioError("REVISION_CONFLICT", "Asset version belongs to a different source/request; fetch into a new version")
        if (cached.get("files") or cached.get("primitive")) and all(Path(item["path"]).is_file() and file_hash(item["path"]) == item["sha256"] for item in cached["files"]):
            _index_asset(cached, manifest_path, root)
            return {**cached, "manifest_path": str(manifest_path), "reused": True, "downloaded_bytes": 0, "artifacts": [str(manifest_path)]}
        if cached.get("status") == "prepared":
            raise StudioError("MISSING_DEPENDENCY", "Prepared asset version has missing or changed original files; restore its recorded bytes")
    provider = candidate.get("provider", candidate.get("source", {}).get("provider", "local"))
    kind = candidate.get("type", "models")
    out.mkdir(parents=True, exist_ok=True)
    files, warnings, downloaded = [], [], 0
    if provider == "ambientcg":
        info, files, downloaded, source = _fetch_ambientcg(candidate, out, file_limit, download_limit)
        specs = []
    elif provider == "polyhaven":
        info = _api("/info/" + quote(asset_id))
        specs = select_files(_api("/files/" + quote(asset_id)), kind, candidate.get("resolution", "1k"), candidate.get("file_selector"))
        source = {"provider": "polyhaven", "page_url": f"https://polyhaven.com/a/{asset_id}", "license_id": "CC0-1.0",
                  "license_evidence": "https://polyhaven.com/license", "retrieved_at": _now(), "use_status": "cleared",
                  "attribution": "Assets from Poly Haven", "authors": info.get("authors", {})}
    else:
        info = candidate
        source = _caller_source(candidate, provider) if trusted_source is None else \
            {"provider": provider, "page_url": None, "license_id": None, "license_evidence": None,
             "retrieved_at": _now(), "attribution": None, **trusted_source, "license_asserted_by": "studio"}
        specs = candidate.get("files", [])
        if candidate.get("path"):
            specs = [{"local_path": candidate["path"], "relative_path": Path(candidate["path"]).name, "role": "primary"}, *specs]
        if not specs and not candidate.get("primitive"):
            if candidate.get("manifest_path"):
                return {**read_manifest(candidate["manifest_path"]), "manifest_path": candidate["manifest_path"], "reused": True}
            if source.get("page_url") or candidate.get("page_url"):
                raise StudioError("ASSET_ACCESS_REQUIRED", "Asset page requires an authorized model file or direct download URL", recovery="Provide a licensed local file or choose another candidate")
            raise StudioError("INPUT_INVALID", "Local candidate requires path, files, manifest_path, or primitive")
    for spec in specs:
        relative = _relative(spec.get("relative_path") or Path(spec.get("local_path", spec.get("path", ""))).name)
        destination = safe_path(out / "original", relative)
        if spec.get("url"):
            downloaded += _download(spec, destination, file_limit, download_limit - downloaded)
        else:
            original = Path(spec.get("local_path", spec.get("path", ""))).resolve()
            if not original.is_file():
                raise StudioError("MISSING_DEPENDENCY", f"Local asset file does not exist: {original}")
            if original.stat().st_size > file_limit:
                raise StudioError("BUDGET_EXHAUSTED", "Local asset exceeds per-file size limit")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        files.append({"path": str(destination), "relative_path": str(relative), "sha256": file_hash(destination),
                      "bytes": destination.stat().st_size, "role": spec.get("role", "dependency")})
    for item in files:
        _verify_gltf(Path(item["path"]))
    manifest = {"schema_version": 1, "asset_id": asset_id, "version": version, "name": info.get("name", asset_id),
                "tags": info.get("tags", []), "source": source, "files": files, "asset_type": kind,
                "primitive": candidate.get("primitive"), "source_units": candidate.get("source_units", "meters"),
                "scale_basis": candidate.get("scale_basis"),
                "prepared_scene": None, "units": "meters", "parts": [], "anchors": [],
                "capabilities": {name: {"status": "needs_prep", "reason": "Requires Blender inventory and semantic mapping"} for name in ("explode", "peel", "closeup")},
                "inspection": {"identity_status": "unverified", "geometry_status": "not_inspected", "missing_files": [], "warnings": warnings, "preview_paths": []},
                "downloaded_bytes": downloaded, "status": "fetched", "acquisition_request_hash": acquisition_key}
    write_manifest(manifest_path, manifest)
    _index_asset(manifest, manifest_path, root)
    return {**manifest, "manifest_path": str(manifest_path), "artifacts": [str(manifest_path)], "reused": False}


def _index_asset(manifest, manifest_path, root):
    library = root.parent if root.name == "assets" else root
    index_path = library / "index.json"
    with lock(library / ".index.lock"):
        data = read_json(index_path) if index_path.exists() else {"schema_version": 1, "assets": []}
        entries = data.get("assets", [])
        if isinstance(entries, dict):
            entries = list(entries.values())
        entry = {key: manifest[key] for key in ("asset_id", "version", "name", "tags", "capabilities")}
        entry["roles"] = [part["part_id"] for part in manifest["parts"]]
        entry["manifest_path"] = str(Path(manifest_path).relative_to(library))
        data["assets"] = [item for item in entries if (item.get("asset_id"), item.get("version")) != (entry["asset_id"], entry["version"])] + [entry]
        write_json(index_path, data)


def _index_prepared(manifest, manifest_path):
    # Only infer the catalog root for the acquisition layout we actually own.
    if manifest_path.parent.name == manifest.get("version") and manifest_path.parent.parent.name == manifest["asset_id"]:
        _index_asset(manifest, manifest_path, manifest_path.parents[2])


def fetch_asset(candidate, asset_root=None, file_limit=1073741824, download_limit=3221225472):
    if isinstance(candidate, (str, Path)):
        candidate = read_json(candidate)
    root = Path(asset_root or REPO / "library" / "assets").resolve()
    # One library write at a time; no persistent process or queue is needed.
    with lock(root / ".fetch.lock"):
        return _fetch_asset(candidate, root, file_limit, download_limit)


def fetch_trusted(candidate, trusted_source, asset_root=None, file_limit=1073741824, download_limit=3221225472):
    """For studio adapter code only (factory, fal, ambientCG): the adapter asserts the licence.

    Not exposed through the CLI, so a candidate JSON can never reach this path.
    """
    root = Path(asset_root or REPO / "library" / "assets").resolve()
    with lock(root / ".fetch.lock"):
        return _fetch_asset(candidate, root, file_limit, download_limit, trusted_source=dict(trusted_source))


def _prepare_asset(manifest, mapping=None, blender=None):
    from .blender import blender_binary
    manifest_path = Path(manifest).resolve()
    data = read_manifest(manifest_path)
    if not isinstance(data, dict) or not data.get("asset_id"):
        raise StudioError("INPUT_INVALID", "Asset manifest must contain an asset_id")
    if mapping is not None and not isinstance(mapping, dict):
        mapping = read_json(mapping)
    if mapping is not None and (not isinstance(mapping, dict) or not isinstance(mapping.get("parts"), list) or not mapping["parts"]):
        raise StudioError("INPUT_INVALID", "Mapping must contain a nonempty parts array")
    for item in data.get("files", []):
        if not Path(item["path"]).is_file() or file_hash(item["path"]) != item["sha256"]:
            raise StudioError("MISSING_DEPENDENCY", f"Original asset file is missing or changed: {item['path']}")
    mapping_hash = stable_hash(mapping) if mapping is not None else None
    if data.get("status") == "prepared" and data.get("mapping_hash"):
        if mapping_hash and mapping_hash != data["mapping_hash"]:
            raise StudioError("REVISION_CONFLICT", "Prepared asset versions are fixed; prepare a new asset version for a changed mapping")
        scene = Path(data["prepared_scene"])
        if scene.is_file() and file_hash(scene) == data.get("prepared_scene_sha256"):
            inventory_path = manifest_path.parent / "prepared" / "inventory.json"
            _index_prepared(data, manifest_path)
            return {"status": "prepared", "manifest_path": str(manifest_path), "asset": data,
                    "inventory": read_json(inventory_path), "reused": True,
                    "artifacts": [str(manifest_path), str(scene), str(inventory_path), *data["inspection"]["preview_paths"]],
                    "warnings": data["inspection"]["warnings"]}
        raise StudioError("MISSING_DEPENDENCY", "Prepared asset bytes are missing or changed; restore its pinned scene or use a new version")
    out = manifest_path.parent / "prepared"
    out.mkdir(exist_ok=True)
    config = {"manifest": data, "mapping": mapping, "output_dir": str(out),
              "inventory_scene": str(out / "imported.blend"), "source_root": str(manifest_path.parent)}
    config_path = out / "prepare_input.json"
    write_json(config_path, config)
    executable = blender or blender_binary()
    command = [str(executable), "--background", "--disable-autoexec", "--python-exit-code", "1", "--python", str(Path(__file__).parent / "blender_ops" / "asset_prepare.py"), "--", str(config_path)]
    started = time.monotonic()
    (out / "result.json").unlink(missing_ok=True)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise StudioError("BLENDER_SCRIPT_ERROR", "Asset preparation exceeded 10 minutes", retryable=True) from exc
    (out / "prepare.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode or not (out / "result.json").exists():
        raise StudioError("BLENDER_SCRIPT_ERROR", (result.stderr + result.stdout)[-2000:], recovery=f"Inspect {out / 'prepare.log'}")
    report = read_json(out / "result.json")
    if not report.get("ok"):
        raise StudioError(report.get("error_code", "ASSET_NOT_SUITABLE"), report.get("error", "Asset preparation failed"))
    data.update({key: report[key] for key in ("parts", "anchors", "capabilities", "inspection", "prepared_scene", "status")})
    data["asset_prep_seconds"] = round(time.monotonic() - started, 3)
    data["mapping_hash"] = mapping_hash
    data["prepared_scene_sha256"] = file_hash(data["prepared_scene"]) if data["prepared_scene"] else None
    write_manifest(manifest_path, data)
    _index_prepared(data, manifest_path)
    return {"status": data["status"], "manifest_path": str(manifest_path), "asset": data, "reused": False,
            "inventory": report["inventory"], "artifacts": [str(manifest_path), str(out / "inventory.json"), *data["inspection"]["preview_paths"]],
            "warnings": data["inspection"]["warnings"]}


def prepare_asset(manifest, mapping=None, blender=None):
    with lock(Path(manifest).resolve().parent / ".prepare.lock"):
        return _prepare_asset(manifest, mapping, blender)


def turnaround_sheet(manifest_path):
    """One image of the prepared previews for a human turnaround check."""
    from PIL import Image
    data = read_manifest(manifest_path)
    previews = [Path(p) for p in data.get("inspection", {}).get("preview_paths", []) if Path(p).is_file()]
    if not previews:
        raise StudioError("ASSET_NOT_SUITABLE", "Prepare the asset first; no preview images to review")
    images = [Image.open(p).convert("RGB") for p in previews]
    width = max(i.width for i in images); height = max(i.height for i in images)
    columns = min(4, len(images)); rows = -(-len(images) // columns)
    sheet = Image.new("RGB", (columns * width, rows * height), (255, 255, 255))
    for index, image in enumerate(images):
        sheet.paste(image, ((index % columns) * width, (index // columns) * height))
    target = Path(manifest_path).parent / "prepared" / "turnaround.jpg"
    target.parent.mkdir(exist_ok=True)
    sheet.save(target, quality=90)
    return target


def approve_asset(manifest_path, decision, reviewer, evidence, reviewer_kind="human"):
    """Record a human turnaround decision pinned to the exact previews and prepared scene."""
    if reviewer_kind != "human":
        raise StudioError("INPUT_INVALID", "Only a human reviewer can approve an asset turnaround")
    if decision not in ("approved", "rejected") or not reviewer or not isinstance(evidence, str) or len(evidence.strip()) < 8:
        raise StudioError("INPUT_INVALID", "approve needs decision approved|rejected, reviewer and the reviewer's own words as evidence")
    manifest_path = Path(manifest_path).resolve()
    with lock(manifest_path.parent / ".prepare.lock"):
        data = read_manifest(manifest_path)
        if data.get("status") != "prepared":
            raise StudioError("INPUT_INVALID", "Only prepared assets (with previews) can be approved")
        sheet = turnaround_sheet(manifest_path)
        data["approval"] = {"decision": decision, "reviewer": reviewer, "reviewer_kind": reviewer_kind, "evidence": evidence.strip(),
                            "approved_at": _now(), "turnaround_sha256": file_hash(sheet),
                            "prepared_scene_sha256": data.get("prepared_scene_sha256")}
        write_manifest(manifest_path, data)
    return {"status": decision, "manifest_path": str(manifest_path), "approval": data["approval"], "artifacts": [str(sheet), str(manifest_path)]}


def register_commands(subparsers):
    parser = subparsers.add_parser("asset", help="Search, acquire, and prepare assets; live assets from Poly Haven")
    commands = parser.add_subparsers(dest="asset_command", required=True)
    search = commands.add_parser("search")
    search.add_argument("--request", required=True)
    search.add_argument("--library")
    search.add_argument("--offline", action="store_true")
    search.set_defaults(handler=lambda args: search_assets(args.request, args.library, not args.offline))
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--candidate", required=True)
    fetch.add_argument("--asset-root")
    fetch.add_argument("--file-limit", type=int, default=1073741824)
    fetch.add_argument("--download-limit", type=int, default=3221225472)
    fetch.set_defaults(handler=lambda args: fetch_asset(args.candidate, args.asset_root, args.file_limit, args.download_limit))
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--mapping")
    prepare.add_argument("--blender")
    prepare.set_defaults(handler=lambda args: prepare_asset(args.manifest, args.mapping, args.blender))
    approve = commands.add_parser("approve", help="Record a HUMAN turnaround decision; never run on an agent's own judgement")
    approve.add_argument("--manifest", required=True)
    approve.add_argument("--decision", choices=["approved", "rejected"], required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--evidence", required=True, help="The reviewer's own words")
    approve.set_defaults(handler=lambda args: approve_asset(args.manifest, args.decision, args.reviewer, args.evidence))
    sheet = commands.add_parser("image3d-review", help="Sheet for one PAID image -> 3D request (no call): show it, get the user's words")
    sheet.add_argument("--project", required=True)
    sheet.add_argument("--image", required=True)
    sheet.add_argument("--asset-id", required=True)
    sheet.add_argument("--endpoint", default="fal-ai/hyper3d/rodin/v2.5")
    sheet.add_argument("--real-dimension", required=True, help="e.g. longest=0.30 (metres)")
    def run_image3d_review(args):
        from .generative.image3d import review
        dimension, meters = args.real_dimension.split("=")
        return review(args.project, args.image, args.asset_id, endpoint=args.endpoint, real_dimension={"dimension": dimension, "meters": float(meters)})
    sheet.set_defaults(handler=run_image3d_review)
    image3d = commands.add_parser("image3d", help="PAID: send the reviewed image -> 3D request; registered review_only + ai_generated")
    image3d.add_argument("--project", required=True)
    image3d.add_argument("--review", required=True)
    image3d.add_argument("--user-words", required=True, help="The user's approval of the sheet, verbatim")
    image3d.add_argument("--allow-paid", action="store_true")
    image3d.add_argument("--max-usd", type=float)
    def run_image3d(args):
        from .generative.image3d import image_to_3d
        return image_to_3d(args.project, args.review, args.user_words, allow_paid=args.allow_paid, max_usd=args.max_usd)
    image3d.set_defaults(handler=run_image3d)
    from .asset_factory.factory import register_generate
    register_generate(commands)
