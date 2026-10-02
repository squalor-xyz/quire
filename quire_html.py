"""Embed local document resources without ever fetching from the network."""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes, urlsplit


SCRIPT_URL_ATTRIBUTES = {"action", "background", "formaction", "href", "poster", "src", "xlink:href"}


def script_url(value: str) -> bool:
    """HTML strips ASCII whitespace before reading a URL scheme."""
    compact = "".join(char for char in value if char not in " \t\n\r\f")
    return compact.lower().startswith(("javascript:", "vbscript:"))


def css_unescape(value: str) -> str:
    return re.sub(
        r"\\([0-9a-fA-F]{1,6}\s?|\r\n|\n|\r|.)",
        lambda match: (
            chr(int(match[1].strip(), 16)) if re.fullmatch(r"[0-9a-fA-F]{1,6}\s?", match[1])
            else "" if match[1] in {"\n", "\r", "\r\n"} else match[1]
        ), value,
    )


def quoted_end(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    raise SystemExit("error: unterminated CSS string")


class Resources:
    def __init__(self, roots: list[Path]):
        self.roots = roots
        self.active: set[Path] = set()

    def resolve(self, uri: str, base: Path | None = None) -> Path:
        parsed = urlsplit(uri)
        if parsed.scheme not in {"", "file"} or parsed.netloc:
            raise SystemExit(f"error: offline build cannot load resource: {uri}")
        path = Path(unquote(parsed.path))
        candidates = [path] if path.is_absolute() else [root / path for root in ([base] if base else self.roots)]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise SystemExit(f"error: resource not found: {uri}")

    def embed(self, uri: str, base: Path | None = None) -> str:
        if uri.startswith("#"):
            return uri
        parsed = urlsplit(uri)
        if parsed.scheme == "data":
            try:
                header, payload = uri.partition("#")[0].split(",", 1)
                mime = header[5:].split(";")[0].lower()
                data = base64.b64decode(payload, validate=True) if ";base64" in header else unquote_to_bytes(payload)
            except (ValueError, TypeError) as exc:
                raise SystemExit("error: invalid data URI") from exc
            if mime not in {"text/css", "image/svg+xml"}:
                return uri
            origin = base
            path = None
        else:
            path = self.resolve(uri, base)
            if path in self.active:
                raise SystemExit(f"error: circular resource import: {path}")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            origin = path.parent
            self.active.add(path)
        try:
            if mime == "text/css":
                data = self.css(data.decode("utf-8"), origin).encode("utf-8")
            elif mime == "image/svg+xml":
                data = self.document(data.decode("utf-8"), origin).encode("utf-8")
        finally:
            if path is not None:
                self.active.remove(path)
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}{fragment}"

    def css(self, text: str, base: Path | None = None) -> str:
        """Scan strings/comments as tokens so examples are not treated as URLs."""
        result = []
        index = 0
        importing = False
        functions = []
        while index < len(text):
            if text.startswith("/*", index):
                end = text.find("*/", index + 2)
                if end == -1:
                    raise SystemExit("error: unterminated CSS comment")
                result.append(text[index:end + 2])
                index = end + 2
                continue
            if text[index] in "\"'":
                end = quoted_end(text, index)
                if importing or (functions and functions[-1] in {"image-set", "-webkit-image-set"}):
                    uri = self.embed(css_unescape(text[index + 1:end - 1]), base)
                    result.append(f'url("{uri}")')
                    importing = False
                else:
                    result.append(text[index:end])
                index = end
                continue
            if text[index].isalnum() or text[index] in "@_-\\":
                start = index
                while index < len(text) and (text[index].isalnum() or text[index] in "@_-\\"):
                    if text[index] == "\\":
                        match = re.match(r"\\(?:[0-9a-fA-F]{1,6}\s?|.)", text[index:])
                        index += len(match[0]) if match else 1
                    else:
                        index += 1
                word = css_unescape(text[start:index]).lower()
                after = index
                while after < len(text) and text[after].isspace():
                    after += 1
                if word in {"url", "src"} and after < len(text) and text[after] == "(":
                    value_start = after + 1
                    while value_start < len(text) and text[value_start].isspace():
                        value_start += 1
                    if value_start < len(text) and text[value_start] in "\"'":
                        end = quoted_end(text, value_start)
                        uri = text[value_start + 1:end - 1]
                        close = end
                        while close < len(text) and text[close].isspace():
                            close += 1
                    else:
                        close = value_start
                        while close < len(text):
                            if text[close] == "\\":
                                close += 2
                            elif text[close] == ")":
                                break
                            else:
                                close += 1
                        uri = text[value_start:close].strip()
                    if close >= len(text) or text[close] != ")":
                        raise SystemExit("error: malformed CSS url()")
                    result.append(f'url("{self.embed(css_unescape(uri), base)}")')
                    index = close + 1
                    importing = False
                else:
                    if after < len(text) and text[after] == "(":
                        functions.append(word)
                        result.append(text[start:after + 1])
                        index = after + 1
                    else:
                        result.append(text[start:index])
                    if word == "@import":
                        importing = True
                continue
            result.append(text[index])
            if text[index] == ")" and functions:
                functions.pop()
            if text[index] == ";":
                importing = False
            index += 1
        return "".join(result)

    def srcset(self, value: str, base: Path | None) -> str:
        candidates = []
        while value.strip():
            value = value.lstrip(" ,\t\n")
            match = re.match(r"(data:\S+|[^\s,]+)", value)
            if not match:
                raise SystemExit("error: malformed image srcset")
            uri = match[1]
            value = value[match.end():]
            if uri.endswith(","):
                uri = uri.rstrip(",")
                descriptor = ""
            else:
                descriptor, _, value = value.partition(",")
                descriptor = descriptor.strip()
                if descriptor and not re.fullmatch(r"\d+(?:\.\d+)?[wx]", descriptor):
                    raise SystemExit("error: unsupported image srcset descriptor")
            candidates.append(self.embed(uri, base) + (f" {descriptor}" if descriptor else ""))
        return ", ".join(candidates)

    def document(self, text: str, base: Path | None = None) -> str:
        parser = EmbeddedHTML(self, base)
        parser.feed(text)
        parser.close()
        return "".join(parser.output)


class EmbeddedHTML(HTMLParser):
    def __init__(self, resources: Resources, base: Path | None):
        super().__init__(convert_charrefs=False)
        self.resources = resources
        self.base = base
        self.output: list[str] = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "iframe", "frame", "object", "embed", "base", "animate", "animatemotion", "animatetransform", "set"}:
            raise SystemExit(f"error: offline document does not support <{tag}>")
        if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            raise SystemExit("error: offline document does not support redirects")
        if any(name.startswith("on") for name, _ in attrs):
            raise SystemExit("error: offline document does not support event handlers")
        if tag == "link" and "stylesheet" in (values.get("rel") or "").lower().split():
            uri = values.get("href") or ""
            embedded = self.resources.embed(uri, self.base)
            css = base64.b64decode(embedded.split(",", 1)[1]).decode("utf-8")
            media = values.get("media")
            if media:
                css = f"@media {media} {{\n{css}\n}}"
            self.output.append("<style>" + re.sub(r"(?i)</style", r"<\\/style", css) + "</style>")
            return
        original = self.get_starttag_text()
        original_names = {match[1].lower(): match[1] for match in re.finditer(r"([^\s<>/=]+)\s*=", original)}
        rewritten = []
        for name, value in attrs:
            if value is not None:
                if name in SCRIPT_URL_ATTRIBUTES and script_url(value):
                    raise SystemExit("error: offline document does not support JavaScript links")
                if name == "style" or re.search(r"(?i)url\s*\(", value):
                    value = self.resources.css(value, self.base)
                elif name in {"src", "poster", "background"} or (name in {"href", "xlink:href"} and tag != "a"):
                    value = self.resources.embed(value, self.base)
                elif name == "srcset":
                    value = self.resources.srcset(value, self.base)
            spelling = original_names.get(name, name)
            rewritten.append(spelling if value is None else f'{spelling}="{html.escape(value, quote=True)}"')
        spelling = re.match(r"<([^\s/>]+)", original)[1]
        ending = " />" if original.rstrip().endswith("/>") else ">"
        self.output.append("<" + spelling + (" " + " ".join(rewritten) if rewritten else "") + ending)
        self.in_style = tag == "style"

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.in_style = False

    def handle_endtag(self, tag):
        self.output.append(f"</{tag}>")
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        self.output.append(self.resources.css(data, self.base) if self.in_style else data)

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")

    def handle_comment(self, data):
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        if decl.lower() != "doctype html":
            raise SystemExit("error: unsupported document declaration")
        self.output.append(f"<!{decl}>")
