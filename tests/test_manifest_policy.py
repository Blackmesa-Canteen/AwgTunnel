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
MANIFEST = REPO / "io.github.awgtunnel.AwgTunnel.yml"

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

ENGINE_COMMIT = "84f4795ea76f9c3168a61e478d0fe0e5c3238308"


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


def test_engine_is_pinned_by_commit():
    # A tag alone can be moved; the commit cannot.
    assert ENGINE_COMMIT in (REPO / "scripts" / "fetch-engine.sh").read_text()
    assert ENGINE_COMMIT in manifest_text()


def test_build_has_no_network_escape():
    text = manifest_text()
    assert "--share=network" in text  # runtime only
    assert "build-args" not in manifest_code(), (
        "build must not be granted extra sandbox rights"
    )
    assert "GOFLAGS: -mod=vendor -trimpath" in text
    assert "GOTOOLCHAIN: local" in text


def test_app_id_is_consistent_everywhere():
    app_id = "io.github.awgtunnel.AwgTunnel"
    assert f"id: {app_id}" in manifest_text()
    assert (REPO / "data" / f"{app_id}.desktop").is_file()
    assert (REPO / "data" / f"{app_id}.metainfo.xml").is_file()
    assert (REPO / "data" / "icons" / f"{app_id}.svg").is_file()
    assert app_id in (REPO / "meson.build").read_text(encoding="utf-8")
