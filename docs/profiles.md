# Profiles

A profile supplies styling and reusable content. Keep it in its own directory;
Quire stores a reference to it rather than copying it into this repository.

## Register and select

```sh
quire install /path/to/acme --name acme
quire --config acme notes.md
acme-pdf notes.md --letterhead
quire configs
```

Omit `--name` to use the directory name. Names start with an ASCII letter and
contain only letters, digits, or hyphens; `quire` is reserved. A `profile.toml`
file path is also accepted. `--no-command` skips writing the shortcut.
Registration defaults to the existing `quire` executable's resolved directory,
or `~/.local/bin` when none is found. Use `--bin-dir` to override it.
Re-register after moving a profile or the Quire checkout.

The user config is `$XDG_CONFIG_HOME/quire/config.toml`, or
`~/.config/quire/config.toml` when that variable is unset. To choose a default,
edit its top-level `default` value:

```toml
default = "acme" # Use "" for no default profile

[profiles]
acme = "/absolute/path/to/acme"
```

Registration and re-registration preserve `default` and other profiles.
Invalid configuration or formatting that cannot be updated safely produces an
error and leaves the configuration file unchanged.
`quire install` and `quire configs` accept `--config-dir`; PDF builds use the
standard location above. Use `XDG_CONFIG_HOME` to relocate it for all commands.

Selection order, first match:

1. `--profile PATH` (directory or TOML file)
2. `--no-config` (plain output)
3. `--config NAME`
4. `QUIRE_CONFIG` (registered name)
5. `QUIRE_PROFILE` (profile path)
6. User config `default`
7. Plain output with `plain.css`

Unknown names and missing profiles produce errors. `--no-config` overrides
environment variables and the default, but an explicit `--profile` wins.

## Create a minimal profile

Create `profile.toml` and `document.css` in the same directory:

```toml
[variants.default]
css = ["document.css"]
```

```css
@page { size: letter; margin: 0.75in; }
body { font-family: sans-serif; font-size: 11pt; }
```

Try it without registration:

```sh
quire --profile /path/to/acme notes.md
```

`[variants.default]` is required. Every referenced CSS, fragment, and built-in
source file must exist. Profile paths resolve relative to the directory
containing the TOML file, including built-in input and output paths. Absolute
paths and `~` paths are also supported.

## Variants and reusable documents

With a profile, flags select these variant names:

| Flags | Variant |
| --- | --- |
| None | `default` |
| `--letterhead` | `letterhead` |
| `--confidential` | `confidential` |
| Both | `letterhead-confidential` |

`--confidential` with no profile, including after `--no-config`, does not use
this table. It uses the built-in plain confidential stylesheet, prepends a
Confidential banner, marks every page with that banner, and adds
`-confidential` to the output name. `--letterhead` with no profile is an
error, whether or not `--confidential` is also set.

Define the variants you need; selecting an absent variant is an error. Use a
complete stylesheet for each combination. This complete example adds variants
and a built-in document; create the referenced files alongside it:

```toml
resource_root = "."

[variants.default]
css = ["document.css"]

[variants.letterhead]
css = ["letterhead.css"]
includes = [{ file = "header.html", skip_if_body_contains = ["letterhead-block"] }]

[variants.confidential]
css = ["confidential.css"]
output_suffix = "-confidential"
metadata = { confidential = "true" }
ensure_in_front_matter = ["confidential"]
includes = [{ file = "notice.html" }]

[variants.letterhead-confidential]
css = ["letterhead-confidential.css"]
output_suffix = "-confidential"
metadata = { confidential = "true" }
ensure_in_front_matter = ["confidential"]
includes = [{ file = "header.html" }, { file = "notice.html" }]

[builtins.sample]
source = "sample.md"
output = "output/sample.pdf"
variant = "letterhead"
```

Run `quire --config acme sample` to render the built-in. Its variant forces its
letterhead/confidential flags; CLI flags can add either. Built-in variants must
use one of the four names above. Built-in names take precedence over file paths
with the same text.

By default, PDFs are written beside the input, or to the built-in's `output`.
`output_suffix` is appended before the extension. An explicit `-o` overrides
both the default output and suffix.

`css` is a list of stylesheets; an omitted or empty list falls back to
`plain.css`. CLI `--css` replaces that list; `--no-css` passes none.
`metadata` contains string values passed to Pandoc. `ensure_in_front_matter`
adds missing keys to YAML front matter, using the metadata value or `true`.
The current missing-key check is a case-insensitive substring check, not a YAML
parser. Existing values are left in place.

## HTML fragments

Includes are processed in their listed order and inserted into the Markdown
body after any YAML front matter. Their optional fields are:

| Field | Behavior |
| --- | --- |
| `file` | Required fragment path |
| `only_if_body_contains` | Include only if every listed string occurs in the original body |
| `skip_if_body_contains` | Skip if any listed string occurs in the original body |
| `after_marker` | Search for this string to locate an insertion block |
| `after_anchor` | Start the closing-tag search here when found after the marker |
| `close_tag` | Closing string to match; defaults to `</div>` |
| `close_count` | Number of closing strings to pass; positive integer, defaults to 1 |

Without a matching insertion point, the fragment is prepended. Matching uses
literal strings, not an HTML parser. When using multiple insertion points, list
includes in document order. CLI `--include-before` fragments are prepended after
the profile's prepended fragments; they do not disable profile includes.

## Resource paths

The resource search roots are, in order: CLI `--resource-path`, profile
`resource_root`, or the working directory (whichever first applies); then the
source directory, Quire directory, and profile directory. Duplicate roots are
removed. Pandoc runs with the first root as its working directory.

`resource_root` must exist and is relative to the profile directory. It also
provides a fallback location for input paths not found in the working directory.

## Mermaid styling and HTML

Profiles can set Mermaid styling explicitly; document CSS is not used to infer
diagram colors or the selected font:

```toml
[mermaid]
theme = "base"
font_family = "sans-serif"
theme_variables = { primaryColor = "#eeeeee", lineColor = "#333333" }

[variants.confidential.mermaid]
theme_variables = { primaryColor = "#dddddd" }
```

The selected variant overrides the profile's settings. Individual
`theme_variables` merge, so the example preserves `lineColor`. Values must be
strings, numbers, or booleans. Supported themes are `default`, `neutral`, `dark`,
`forest`, and `base`; Mermaid's theme variables are intended for the `base`
theme. Unknown settings and invalid values are errors. Existing profiles default
to the `neutral` theme and `Helvetica, Arial, sans-serif`.

Use fonts available to both Chromium and WeasyPrint, or declare local font files
with `@font-face` in the selected CSS. Keep font declarations outside print-only
media queries so the diagram renderer can load them. Remote fonts and resources
are rejected. Diagram text uses this `font_family`. Flowchart labels are SVG
text, centred in their nodes. Changing the font or the selected stylesheet
invalidates diagram cache entries.

Plain and confidential stylesheets cap each diagram SVG at 65% of the page
content height and the full content width. The PDF fitter applies the
`max-height` percentage on `.quire-diagram > svg` and never draws a diagram
larger than its intrinsic size. Set that percentage in a profile stylesheet to
override it. When the stylesheet does not declare one, the cap stays 65%.

PDF and HTML share profile selection, variants, includes, and CSS. Use
`--format html` or `-o document.html` for a standalone HTML document. `--no-css`
still disables document styles; diagram containment rules remain active for
rendered diagrams. `--no-mermaid` preserves source blocks in either format.

Use `@media screen` in profile CSS for browser presentation. Running elements
and page-margin content are print features; provide screen rules when letterhead
or notices must appear in the browser body. The built-in plain confidential
stylesheet supplies its own screen rule. HTML does not simulate PDF pages.
