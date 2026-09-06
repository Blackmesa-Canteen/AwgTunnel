# Publishing AWG Tunnel on Flathub

A checklist and a walkthrough. Everything here is a manual step for a human,
deliberately: Flathub's requirements state that

> AI tools or agents must not open or automate Flathub submission pull
> requests, or generate their commit messages, descriptions, review comments,
> or replies.

So the pull request, its commit message, its description, and every reply to a
reviewer must be your own writing. `scripts/make-submission.sh` stops at
producing files for you to inspect.

## Before you submit

### 1. Disclose the AI-assisted work

Flathub requires this, and it applies to this project:

> Submitters must disclose any AI-generated code, documentation, packaging, or
> other material they know or reasonably believe is included in the application
> or its Flathub packaging.

The application code, the manifest, the metadata and the CI in this repository
were written with AI assistance. State that plainly in the pull request
description. The exemption for "research, discussion, or debugging" does not
apply, because generated material *is* included.

> Undisclosed or materially misrepresented AI-generated material […] may result
> in rejection. Repeated violations may result in a permanent ban from future
> submissions and activities.

Disclosure is not an obstacle to acceptance; concealment is. If you are unsure
how much detail is expected, ask `admins@flathub.org` before opening anything.

### 2. The repository must be named to match the app ID

The app ID is `io.github.blackmesa_canteen.AwgTunnel`, which Flathub resolves
to **`https://github.com/Blackmesa-Canteen/AwgTunnel`**. That repository has to
exist and be public, or the ID cannot be verified. The `blackmesa_canteen`
component is the account name lowercased with `-` converted to `_`, per
Flathub's rule that "the domain portion must be in lowercase and must convert
dash `-` to underscore `_`".

If you rename the account or the repository, re-run
`./scripts/set-app-id.sh <account>` and expect existing installs to lose their
stored profiles — the app ID determines the data directory and the keyring
attributes.

### 3. Add screenshots

`flatpak-builder-lint repo repo` currently fails with
`metainfo-missing-screenshots`, and the buildbot runs that same linter. Add at
least one screenshot to
[`data/io.github.blackmesa_canteen.AwgTunnel.metainfo.xml`](../data/io.github.blackmesa_canteen.AwgTunnel.metainfo.xml):

```xml
<screenshots>
  <screenshot type="default">
    <image>https://raw.githubusercontent.com/Blackmesa-Canteen/AwgTunnel/v0.1.0/data/screenshots/main.png</image>
    <caption>A connected tunnel showing its proxy address</caption>
  </screenshot>
</screenshots>
```

Point at a tagged path rather than `main`, so the image cannot change under a
published release. Take them at a sensible window size, in both light and dark
if you add more than one.

### 4. Everything else is already in place

- Manifest at the top level, named after the app ID.
- `metainfo.xml` and `.desktop` live in **this** repository, not in the
  submission — Flathub wants them upstream.
- `content_rating`, `developer`, `launchable`, homepage and bugtracker URLs.
- No `flathub.json` needed: the default is `x86_64` and `aarch64`, and CI builds
  both.
- Built entirely from source, with no network access during the build.

Run the local gate before going further:

```bash
flatpak run --command=flathub-build org.flatpak.Builder \
    io.github.blackmesa_canteen.AwgTunnel.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
    io.github.blackmesa_canteen.AwgTunnel.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
```

`appid-url-not-reachable` disappears once the GitHub repository is public.

## Submitting

1. **Tag a release in this repository** and let CI publish it. The submission
   manifest must point at a commit that exists publicly.

   ```bash
   git tag -s v0.1.0 -m "AWG Tunnel 0.1.0"
   git push origin v0.1.0
   ```

2. **Generate the submission files.**

   ```bash
   ./scripts/make-submission.sh v0.1.0
   ls .work/flathub/
   #   io.github.blackmesa_canteen.AwgTunnel.yml
   #   go.mod.yml
   #   modules.txt
   ```

   The manifest is identical to the one in this repository except that the app
   module is fetched from a pinned tag instead of the working tree. Diff them
   and confirm that is the only difference — particularly that `finish-args` is
   unchanged. The script checks this too, but read it yourself.

   `go.mod.yml` and `modules.txt` must be included: Flathub requires that "all
   dependency manifests generated for npm, yarn, cargo, pip, etc. must be
   included in the submission".

3. **Build the generated manifest once**, from the generated directory, exactly
   as the buildbot will:

   ```bash
   cd .work/flathub
   flatpak run --command=flathub-build org.flatpak.Builder \
       io.github.blackmesa_canteen.AwgTunnel.yml
   ```

4. **Fork `flathub/flathub`**, unchecking "Copy the master branch only", and
   clone the `new-pr` branch:

   ```bash
   git clone --branch=new-pr git@github.com:Blackmesa-Canteen/flathub.git
   ```

5. **Copy the three generated files to the repository root**, commit, push.

6. **Open the pull request against the `new-pr` base branch.** Not `master` —
   Flathub is explicit: "Please do not open the PR against the `master` base
   branch". Title it `Add io.github.blackmesa_canteen.AwgTunnel`.

   In the description: what the app does, the AI-assistance disclosure from
   step 1, and a note that the app is a local SOCKS5 proxy rather than a
   system-wide VPN, since a reviewer will reasonably ask why a VPN client needs
   no privileges.

7. **A bot builds the PR.** Fix what it reports, and reply to reviewers
   yourself.

## After it is merged

The PR is merged into a new `flathub/io.github.blackmesa_canteen.AwgTunnel`
repository and you are invited as a maintainer. From then on:

- **Releases:** tag here, then update `tag` and `commit` in the Flathub
  repository's manifest. `x-checker-data` lets Flathub's update bot open that
  pull request for you.
- **Engine bumps:** run `./scripts/update-engine.sh <tag>` here, review the hash
  diff, commit, then carry the regenerated `go.mod.yml` and `modules.txt` across
  with the release.
- **Do not let the two manifests drift.** Always regenerate with
  `make-submission.sh` rather than hand-editing the Flathub copy; the
  permission set is the app's security boundary and it lives in that file.

## Things a reviewer is likely to raise

**"Why does a VPN client need no permissions?"** It terminates the tunnel in a
userspace network stack (gVisor netstack, via `amneziawg-go`) and exposes it as
a SOCKS5 proxy on loopback. There is no `tun` device, so no `CAP_NET_ADMIN`, no
host service and no elevated rights. `--share=network` reaches the server's UDP
endpoint; that is all.

**"Is this bundling a prebuilt binary?"** No. The engine is compiled from the
pinned upstream commit during the build, from Go modules declared as
hash-pinned archives. `scripts/verify-engine-sources.sh` re-checks every hash
against the module proxy, and CI runs it on a schedule.

**"Trademarks."** The metainfo and README both carry the non-affiliation notice
and record that WireGuard is a registered trademark of Jason A. Donenfeld.
