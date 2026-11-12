# Releasing LODGaZilla

## Version numbers

LODGaZilla follows [Semantic Versioning 2.0.0](https://semver.org/): MAJOR.MINOR.PATCH,
with an optional pre-release after a hyphen. The version is `APP_VERSION` in
[`src/version.py`](../src/version.py), the only place it is written; everything else
reads it from there.

| Stage | Version | Meaning |
| --- | --- | --- |
| Before the beta | `0.1.0` | Internal. No compatibility promises. |
| Public beta | `1.0.0-beta.1`, `1.0.0-beta.2`, … | Feature-complete toward 1.0. Each shared build gets the next number. |
| Release candidate | `1.0.0-rc.1`, … | Feature freeze: fixes only. A good candidate becomes the release. |
| Release | `1.0.0` | Stable. |

After 1.0.0, raise:

- **MAJOR** when something users or plugins rely on breaks: older workspaces no
  longer open (`UMW_VERSION` is raised), the plugin API changes incompatibly
  (`PLUGIN_API_VERSION` is raised), existing H5 files must be regenerated, or a
  feature is removed.
- **MINOR** for new features that break nothing: a card type, a setting, a
  backward-compatible addition to the plugin API.
- **PATCH** for bug fixes only.

During a beta, only the pre-release number rises. Breaking changes are still
allowed, but each one is called out in the changelog.

### Other version numbers

These change independently of the application version. Note any change in the
changelog.

| Number | Where | Raise when |
| --- | --- | --- |
| UMW format | `UMW_VERSION` in `src/data/workspace_file.py` | Older applications can no longer read new workspaces. |
| Plugin API | `PLUGIN_API_VERSION` in `src/contracts/plugin.py` | Existing plugins no longer work; plugins declare `api_version` in `plugin.json`. |
| H5 format | `SCHEMA_VERSION` in `src/jobs/import_data.py` | The H5 layout changes ([H5 format](H5_Format.md)). |
| A plugin | `version` in its `plugin.json` | The plugin's own releases, in its own repository. |

## Where the version shows

- The About dialog, from the Plot navigator's help (?) button.
- The log: `LODGaZilla <version>` near the start of `logs/lodgazilla.log`.
- Saved files: `application_version` in UMW workspaces and the `tool_version`
  attribute of generated H5 files, so a file sent with a problem report tells which
  version made it.

In a Git checkout the About dialog and the log add `git describe`, such as
`1.0.0 (v1.0.0-5-g9850d27-dirty)`: five commits after v1.0.0, with uncommitted
changes. On the release tag's own commit they show just `1.0.0`.

## Making a release

1. Run the full test suite: `python -m unittest discover -s tests`.
2. Set `APP_VERSION` in `src/version.py`, such as `1.0.0-beta.1`.
3. In [`CHANGELOG.md`](../CHANGELOG.md), rename **Unreleased** to the version and
   date, such as `## [1.0.0-beta.1] - 2026-12-01`, and start a new empty
   **Unreleased** section above it.
4. Commit both as `Release 1.0.0-beta.1`.
5. Tag that commit with the version prefixed by `v`: `git tag -a v1.0.0-beta.1 -m "LODGaZilla 1.0.0-beta.1"`.
6. Push the commit and the tag, then create the GitHub or Bitbucket release from
   the tag with the changelog section as its notes. Mark betas and release
   candidates as pre-releases.
