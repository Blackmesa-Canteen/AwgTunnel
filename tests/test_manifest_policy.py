"""Guards on the Flatpak manifest.

The sandbox permissions are the app's security boundary, so they are asserted
exactly: adding one fails this test and forces a deliberate decision rather
than an unnoticed widening. Parsed as text on purpose, so the test needs no
YAML dependency and cannot be fooled by an anchor or an include.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "io.github.blackmesa_canteen.AwgTunnel.yml"

#: The complete, intended permission set.
EXPECTED_FINISH_ARGS = {
    "--share=ipc",
    "--share=network",
    "--socket=wayland",
    "--socket=fallback-x11",
    "--device=dri",
    "--talk-name=org.freedesktop.secrets",
}

#: Permissions that would defeat the point of this design.
FORBIDDEN_PATTERNS = (
    r"--filesystem=",
    r"--device=all",
    r"--device=shm",
    r"--system-talk-name=",
    r"--system-own-name=",
    r"--socket=session-bus",
    r"--socket=system-bus",
    r"--socket=x11\b",
    r"--allow=devel",
    r"--talk-name=org\.freedesktop\.Flatpak",
    r"--talk-name=org\.freedesktop\.systemd1",
)

GO_SOURCES = REPO / "go.mod.yml"
MODULES_TXT = REPO / "modules.txt"


def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def manifest_code() -> str:
    """The manifest with comments removed.

    The manifest documents which permissions are deliberately *absent*, so the
    forbidden-permission checks have to ignore comments or they trip over the
    documentation.
    """
    return re.sub(r"(?m)(?:^|\s)#.*$", "", manifest_text())


def finish_args() -> set[str]:
    text = manifest_text()
    block = re.search(r"^finish-args:\n((?:\s*(?:#.*)?\n|\s+-\s.*\n)+)", text, re.M)
    assert block, "manifest has no finish-args block"
    return {
        match.group(1).strip().strip("'\"")
        for match in re.finditer(r"^\s+-\s+(.*)$", block.group(1), re.M)
    }


def test_manifest_exists():
    assert MANIFEST.is_file()


def test_finish_args_match_the_intended_set():
    assert finish_args() == EXPECTED_FINISH_ARGS


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_no_forbidden_permission(pattern):
    matches = re.findall(pattern, manifest_code())
    assert not matches, f"manifest grants forbidden permission matching {pattern!r}"


def test_no_host_filesystem_access_at_all():
    # Profile import goes through the file chooser portal; if a --filesystem
    # override ever appears, the portal path has regressed.
    assert "--filesystem" not in manifest_code()


def engine_module() -> str:
    """Just the engine module, so assertions cannot stray into the app module."""
    text = manifest_text()
    start = text.index("  - name: awg-tunnel-engine")
    rest = text[start + 1 :]
    end = re.search(r"^  - name: ", rest, re.M)
    return rest[: end.start()] if end else rest


def engine_pin() -> tuple[str, str]:
    """The engine tag and commit, read out of the engine module."""
    module = engine_module()
    tag = re.search(r"^\s+tag: (\S+)$", module, re.M)
    commit = re.search(r"^\s+commit: ([0-9a-f]{40})$", module, re.M)
    assert tag, "engine source has no tag"
    assert commit, "engine source has no 40-character commit"
    return tag.group(1), commit.group(1)


def test_engine_is_pinned_by_commit_not_only_tag():
    # A tag can be moved; the commit cannot. Both are asserted structurally so
    # that bumping the engine does not require editing this test — the point is
    # that a pin exists, not which one.
    engine_pin()


def test_engine_source_is_upstream_not_a_local_copy():
    module = engine_module()
    assert "url: https://github.com/artem-russkikh/wireproxy-awg.git" in module
    # A `type: dir` engine source would mean somebody reintroduced a vendored
    # copy, which Flathub's builders cannot see.
    assert "type: dir" not in module


def test_engine_version_string_matches_the_pin():
    # The binary reports this to the log view and to bug reports, so a stale
    # value here is a lie about what is running.
    tag, _ = engine_pin()
    assert f"-X main.version={tag}" in manifest_text()


def test_go_modules_are_declared_and_hash_pinned():
    assert GO_SOURCES.is_file(), "go.mod.yml is missing"
    assert MODULES_TXT.is_file(), "modules.txt is missing"

    sources = GO_SOURCES.read_text(encoding="utf-8")
    urls = re.findall(r"^  url: (\S+)$", sources, re.M)
    hashes = re.findall(r"^  sha256: ([0-9a-f]{64})$", sources, re.M)

    assert urls, "no module archives declared"
    # Every archive needs a hash: an unpinned one would be a silent hole in the
    # only integrity check the build has, since vendor mode ignores go.sum.
    assert len(urls) == len(hashes)
    assert all(url.startswith("https://proxy.golang.org/") for url in urls)

    # The engine is only useful to us because of its obfuscation support.
    assert any("amnezia-vpn/amneziawg-go" in url for url in urls)


def test_manifest_references_the_generated_sources():
    assert re.search(r"^      - go\.mod\.yml$", manifest_text(), re.M)


def test_modules_txt_covers_every_declared_module():
    # `go build -mod=vendor` fails if modules.txt and the vendor tree disagree,
    # which would only surface deep inside a Flatpak build. Cheaper to catch
    # the mismatch here.
    declared = {
        re.sub(
            r"/v\d+$",
            "",
            url.split("/@v/")[0].removeprefix("https://proxy.golang.org/"),
        )
        for url in re.findall(r"^  url: (\S+)$", GO_SOURCES.read_text(), re.M)
    }
    listed = {
        re.sub(r"/v\d+$", "", line.split()[1])
        for line in MODULES_TXT.read_text(encoding="utf-8").splitlines()
        if line.startswith("# ")
    }
    # Module paths on the proxy are case-escaped ("!make!now!just"), so compare
    # case-insensitively after undoing the escaping.
    normalise = {re.sub(r"!(.)", lambda m: m.group(1), d).lower() for d in declared}
    assert normalise == {module.lower() for module in listed}


def test_build_has_no_network_escape():
    text = manifest_text()
    assert "--share=network" in text  # runtime only
    assert "build-args" not in manifest_code(), (
        "build must not be granted extra sandbox rights"
    )
    assert "GOFLAGS: -mod=vendor -trimpath" in text
    assert "GOTOOLCHAIN: local" in text


def test_app_id_is_consistent_everywhere():
    app_id = "io.github.blackmesa_canteen.AwgTunnel"
    assert f"id: {app_id}" in manifest_text()
    assert (REPO / "data" / f"{app_id}.desktop").is_file()
    assert (REPO / "data" / f"{app_id}.metainfo.xml").is_file()
    assert (REPO / "data" / "icons" / f"{app_id}.svg").is_file()
    assert app_id in (REPO / "meson.build").read_text(encoding="utf-8")
