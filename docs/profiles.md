# Profiles

A profile supplies styling and reusable content. Keep it in its own directory;
Quire stores a reference to it rather than copying it into this repository.

## Register and select

```sh
quire install /path/to/acme --name acme
quire --config acme notes.md
acme-pdf notes.md --letterhead
quire configs
quire default acme
quire remove acme
```

Omit `--name` to use the directory name. Names start with an ASCII letter and
contain only letters, digits, or hyphens; `quire` is reserved. A `profile.toml`
file path is also accepted. `--no-command` skips writing the shortcut.
Registration defaults to the existing `quire` executable's resolved directory,
or `~/.local/bin` when none is found. Use `--bin-dir` to override it.
Re-register after moving a profile or the Quire checkout.

`quire remove NAME` deletes the name from the config and deletes its
`NAME-pdf` shortcut when the file is exactly what Quire wrote; any other
file with that name is left in place with a warning. It never touches the
profile directory. Removing the current default is refused; run
`quire default --clear` or choose another default first.

The user config is `$XDG_CONFIG_HOME/quire/config.toml`, or
`~/.config/quire/config.toml` when that variable is unset. To choose a default,
run `quire default acme`; `quire default --clear` returns to plain output.
The command accepts only registered names and sets the top-level `default`
value, leaving other lines alone:

```toml
default = "acme" # Use "" for no default profile

[profiles]
acme = "/absolute/path/to/acme"
```

Registration and re-registration preserve `default` and other profiles.
Invalid configuration or formatting that cannot be updated safely produces an
error and leaves the configuration file unchanged. `quire install`,
`quire configs`, `quire default`, and `quire remove` accept `--config-dir`;
PDF builds use the standard location above. Use `XDG_CONFIG_HOME` to relocate
it for all commands.

Selection order, first match:

1. `--profile PATH` (directory or TOML file)
2. `--no-config` (plain output)
3. `--config NAME`
4. `--prefer-config NAME` (skipped with a warning if NAME is not registered)
5. `QUIRE_CONFIG` (registered name)
6. `QUIRE_PROFILE` (profile path)
7. User config `default`
8. Plain output with `plain.css`

Unknown names and missing profiles produce errors, and so does an empty `--config` name
(it is not treated as unset). Surrounding whitespace is trimmed from `--config` and
`--prefer-config` names, so `--config " acme "` selects `acme`; a name that is blank after
trimming is empty and fails the same way. An unknown name's error names the
config file it read (noting if that file is missing), where the name came from, and the
command that fixes it. `--no-config` overrides environment variables and the default,
but an explicit `--profile` wins.

`--prefer-config NAME` is for a shared script that wants its brand when the machine has
it. A registered NAME is used as `--config` would. An unregistered NAME prints one warning
(naming the config file and the `quire install` command) and selection continues down the
list. The warning is held until the arguments parse, so `--help` and usage errors stay quiet.
A registered NAME whose profile directory is gone is still an error, an empty NAME is an
error, `--letterhead` with no profile to fall back on still fails, and `--config` together
with `--prefer-config` is a usage error (exit 2).

The words `install`, `configs`, `default`, and `remove` as the first argument
run those subcommands. To build a built-in with one of those names, put an
option first, as in `quire --config acme default`; the `acme-pdf` shortcut
already does.

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
`plain.css`. CLI `--css` replaces that list; `--no-css` passes none. CLI `--add-css PATH`
(repeatable) appends stylesheets after the selected list, whether it came from
the profile variant, `plain.css`, `plain-confidential.css`, or `--css`;
`--no-css` drops them too. Added rules override earlier ones of equal
specificity, but a profile's named (`@page letterhead { ... }`) and pseudo-class
(`@page :first { ... }`) rules outrank an unnamed `@page`, so override those
with `!important`, e.g. `@page { margin: 0.5in !important; }`. That works only
when the profile's own rule is not `!important`. Unless `--no-css` is set, a
missing added stylesheet is an error before any output is written.
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

## Plain-text input

A `.txt` target is wrapped, before includes run, in a fenced block (longer than
any backtick run in the text) inside a fenced div, so Pandoc emits
`<div class="quire-verbatim"><pre class="..."><code>...</code></pre></div>`
with the text unchanged. Includes see the wrapped text, so their body
conditions match words in the file. Both plain stylesheets set
`.quire-verbatim pre` to `white-space: pre; font-size: 9pt`. Pandoc expands
tabs to 4-column stops.

A profile stylesheet can give these pages their own margins with a WeasyPrint
named page, so one profile covers prose and fixed-width documents:

```css
.quire-verbatim { page: verbatim; }
@page verbatim { margin: 0.7in; }
```

The unnamed `@page` still applies, so running headers and footers carry over;
`@page verbatim` overrides only what it sets. Rules on other named pages
(`@page letterhead`) do not apply. Pseudo-class rules such as `@page :first`
still match a verbatim page that falls first; use `@page verbatim:first` to
target that case.

Switching to a named page forces a page break. An include that stays in the
flow (an intro fragment, a letterhead block) therefore lands alone on its own
page before the text, while running elements such as the confidential banner do
not. Use running elements for those fragments, or skip the named page.

Includes with an insertion point (`after_marker`) are prepended for `.txt`
input, since markers inside verbatim text mean nothing.

## Required fonts

A profile can list the font families its stylesheets depend on, so a missing
font stops the build instead of being replaced silently:

```toml
fonts = ["Brand Sans", "Brand Serif"]

[variants.letterhead]
fonts = ["Brand Sans"]
```

`fonts` is an optional list of family names at profile level and in a variant.
A variant's list replaces the profile's for that variant (an empty list checks
nothing). Names are trimmed. Before any target is built, Quire lists the installed
families with fontconfig's `fc-list` tool and compares each name, ignoring case. A missing one fails the call, writing nothing:

```text
error: profile acme needs font "Brand Sans", which is not installed
```

The profile name is the config name that selected the profile (`--config`,
`--prefer-config`, `QUIRE_CONFIG`, or the config default). A profile chosen by
path (`--profile` or `QUIRE_PROFILE`) is named by its directory.
The check runs for PDF output only: HTML does not embed system fonts, so a
different machine decides what the reader sees. It runs once per call however
many targets there are, and whenever a profile is selected, including with
`--css`, `--no-css`, or `--add-css`, because the profile's Mermaid `font_family`
and HTML fragments can still use its fonts. If `fc-list` is missing, fails, or
times out (60 seconds), Quire warns once on stderr and builds anyway.

Fonts loaded with `@font-face` need not be listed: they do not have to be
installed. Fonts named only in a `--css` or `--add-css` file are not checked.
Quire does not install fonts or read font stacks from CSS, so list only the
families that must be installed.

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

Those stylesheets also keep a table row on one page, and keep the header with
the first row (`tr { break-inside: avoid; }` and `thead { break-after: avoid; }`).
A profile stylesheet can replace either rule.

PDF and HTML share profile selection, variants, includes, and CSS. Use
`--format html` or `-o document.html` for a standalone HTML document. `--no-css`
still disables document styles; diagram containment rules remain active for
rendered diagrams. `--no-mermaid` preserves source blocks in either format.

Use `@media screen` in profile CSS for browser presentation. Running elements
and page-margin content are print features; provide screen rules when letterhead
or notices must appear in the browser body. The built-in plain confidential
stylesheet supplies its own screen rule. HTML does not simulate PDF pages.
