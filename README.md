# Quire

Turn Markdown into PDF with Pandoc and WeasyPrint. Quire provides a plain
stylesheet and supports separate profiles for custom styling and reusable content.

## Install

Requires Python 3.11 or newer, Pandoc, and WeasyPrint's native libraries.
On macOS with Homebrew:

```sh
brew install pandoc pango gdk-pixbuf libffi
python3 install.py
```

Run the installer from this checkout. It creates `.venv`, installs WeasyPrint
there if needed, and writes `~/.local/bin/quire`. It does not edit your shell
settings. If that directory is not on PATH, add this to your shell configuration:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

On other systems, install Pandoc and WeasyPrint's native libraries for your
platform before running the Python installer.

The launcher points at this checkout: keep it in place. After moving it, rerun
`python3 install.py` and re-register any profile shortcuts. Use
`--bin-dir PATH` to choose another launcher directory.

## Make a PDF

```sh
quire notes.md
```

Writes `notes.pdf` beside the source. With no selected profile, `plain.css`
provides US letter pages, sans-serif text, and page numbers.

```sh
quire notes.md -o output/notes.pdf       # Choose an output path
quire notes.md --no-config              # Use the plain style
quire notes.md --css custom.css         # Replace the selected stylesheet
quire notes.md --no-css                 # Pass no stylesheet to Pandoc
quire notes.md --include-before intro.html
quire notes.md --resource-path assets   # Set the resource root
```

Input files must end in `.md` or `.markdown`. Output directories are created
automatically; an existing output PDF is overwritten. `--css` and
`--include-before` can be repeated. `--no-css` takes precedence over `--css`.
Relative command-line paths resolve from the working directory.

## Use a profile

A profile is a separate directory containing `profile.toml` and its assets.
Register it under a name, or use its path directly:

```sh
quire install /path/to/profile --name acme
quire --config acme notes.md
acme-pdf notes.md
quire --profile /path/to/profile notes.md
quire configs
```

Registration stores the profile's path and creates an `acme-pdf` shortcut;
it does not copy assets or select a default. `--no-command` skips the shortcut.
Letterhead and confidential flags require a profile with the matching variant.

See [Profiles](docs/profiles.md) for defaults, selection order, and authoring.

## Help and development

```sh
quire --help
quire install --help
python3 install.py --help
python3 test_quire.py
```

See [Contributing](CONTRIBUTING.md) for local checks and the code layout.
If a build reports missing tools, check that Pandoc is on PATH and WeasyPrint
is available in `.venv/bin` or on PATH. Rerunning the installer restores a
missing local Python runtime.
