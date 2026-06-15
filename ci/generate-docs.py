#!/usr/bin/env python3

# TODO: explain what this script does / is about!

from __future__ import annotations

import abc
import base64
import json
import os
import re
import typing as ty
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO


type RenderMode = Literal["mdbook", "mkdocs"]

class BasePage(abc.ABC):
    title: str

    @abc.abstractmethod
    def render(self, output: TextIO, *, depth: int = 0, mode: RenderMode) -> None:
        pass


class Page(BasePage):
    def __init__(
        self,
        title: str,
        filepath: Path | str | None,
        *,
        # note: need `Sequence` here not `list`, because python mutable containers are invariant 😬
        children: ty.Sequence[BasePage] | None = None,
    ):
        self.title = title
        self.filepath = Path(filepath) if filepath else None
        self.children: list[BasePage] = list(children or [])

    @ty.override
    def render(self, output: TextIO, *, depth: int = 0, mode: RenderMode) -> None:
        indent = "  " * depth
        bullet = "- " if depth > 0 else ""
        if mode == "mdbook":
            if self.filepath:
                output.write(f"{indent}{bullet}[{self.title}]({self.filepath})\n")
        elif mode == "mkdocs":
            if depth > 0:
                if len(self.children) == 0:
                    output.write(f'{indent}{bullet}"{self.title}": {self.filepath}\n')
                else:
                    output.write(f'{indent}{bullet}"{self.title}":\n')
                    if self.filepath:
                        output.write(
                            f'{indent}  {bullet}"{self.title}": {self.filepath}\n'
                        )
        for child_page in self.children:
            child_page.render(output, depth=depth + 1, mode=mode)


# autogenerate an index page from the contents of a directory
class GenIndexPage(BasePage):
    def __init__(self, title: str, dirname: str, *, index=None, extract_title: bool = False):
        self.title = title
        self.dir = Path(dirname)
        self.index = index  # FIXME: index is never initialized to anything else than None, remove?
        self.extract_title = extract_title

    def render(self, output: TextIO, *, depth: int = 0, mode: RenderMode) -> None:
        paths = sorted(self.dir.glob("*.md"))
        children: list[Page] = []
        for filepath in paths:
            title = filepath.stem  # foo in /path/to/foo.md
            if title == "index":
                continue

            if self.extract_title:
                with open(filepath) as f:
                    title = f.readline().strip("#").strip()

            children.append(Page(title, filepath))

        index_filepath = self.dir / "index.md"
        index_page = Page(self.title, index_filepath, children=children)
        index_page.render(output, depth=depth, mode=mode)
        with open(self.dir / "index.md", "w") as idx_file:
            if self.index:
                idx_file.write(self.index)
                idx_file.write("\n\n")
            else:
                try:
                    with open(self.dir / "index.markdown") as f:
                        idx_file.write(f.read())
                        idx_file.write("\n\n")
                except FileNotFoundError:
                    pass
            for page in children:
                assert page.filepath  # (hint for type system)
                idx_file.write(f"  - [{page.title}]({page.filepath.name})\n")


class RawColorSchemeData_Colors(ty.TypedDict):
    """The "colors" section of a raw colorscheme definition"""
    ansi: list[str]
    brights: list[str]
    indexed: list[str]
    background: str
    cursor_bg: str
    cursor_border: str
    cursor_fg: str
    foreground: str
    selection_bg: str
    selection_fg: str


class RawColorSchemeData_Metadata(ty.TypedDict):
    """The "metadata" section of a raw colorscheme definition"""
    aliases: list[str]
    name: str
    prefix: str
    author: ty.NotRequired[str]
    origin_url: ty.NotRequired[str]
    wezterm_version: ty.NotRequired[str]


class RawColorSchemeData(ty.TypedDict):
    """A raw colorscheme definition"""
    colors: RawColorSchemeData_Colors
    metadata: RawColorSchemeData_Metadata


class LoadedColorScheme(ty.TypedDict):
    """A loaded colorscheme"""
    name: str
    prefix: str
    ident: str
    fg: str
    bg: str
    cursor: str
    metadata: RawColorSchemeData_Metadata
    css: str


def load_scheme(scheme: RawColorSchemeData) -> LoadedColorScheme:
    ident = re.sub(
        "[^a-z0-9_]", "_", scheme["metadata"]["name"].lower().replace("+", "plus")
    )

    if "ansi" not in scheme["colors"]:
        raise Exception(f"scheme {scheme} is missing ansi colors!!?")
    colors = scheme["colors"]["ansi"] + scheme["colors"]["brights"]

    data = {
        "name": scheme["metadata"]["name"],
        "prefix": scheme["metadata"]["prefix"],
        "ident": ident,
        "fg": scheme["colors"]["foreground"],
        "bg": scheme["colors"]["background"],
        "metadata": scheme["metadata"],
    }

    # <https://github.com/asciinema/asciinema-player/wiki/Custom-terminal-themes>
    css = f"""
.asciinema-theme-{ident} .asciinema-terminal {{
    color: {data["fg"]};
    background-color: {data["bg"]};
    border-color: {data["bg"]};
}}

.asciinema-theme-{ident} .fg-bg {{
    color: {data["bg"]};
}}

.asciinema-theme-{ident} .bg-fg {{
    background-color: {data["fg"]};
}}
"""

    if "cursor_border" in scheme["colors"]:
        data["cursor"] = scheme["colors"]["cursor_border"]

        css += f"""
.asciinema-theme-{ident} .cursor-b {{
    background-color: {data["cursor"]} !important;
}}
"""

    if "selection_fg" in scheme["colors"] and "selection_bg" in scheme["colors"]:
        selection_bg = scheme["colors"]["selection_bg"]
        selection_fg = scheme["colors"]["selection_fg"]

        css += f"""
.asciinema-theme-{ident} .asciinema-terminal ::selection {{
    color: {selection_fg};
    background-color: {selection_bg};
}}
"""

    for idx, color in enumerate(colors):
        css += f"""
.asciinema-theme-{ident} .fg-{idx} {{
    color: {color};
}}
.asciinema-theme-{ident} .bg-{idx} {{
    background-color: {color};
}}
"""

    data["css"] = css
    return ty.cast(LoadedColorScheme, data)


@dataclass
class AsciinemaTerminal:
    """Represents an Asciinema Terminal"""

    class Header(ty.TypedDict):
        width: int
        height: int
        title: str

    title: str
    width: int
    height: int
    lines: list[str]

    def to_base64(self) -> str:
        """Render the terminal in asciinema base64 format, to be passed to an AsciinemaPlayer"""
        # FIXME: link to spec 🤔 (/!\ impl doesn't match v2 spec /!\)
        asciinema_header = {
            "version": 2,
            "width": self.width,
            "height": self.height,
            "title": self.title,
        }
        screen_content = "\r\n".join(self.lines)

        header_json = json.dumps(asciinema_header, sort_keys=True)
        data_json = json.dumps([0.0, "o", screen_content])
        return base64.b64encode(f"{header_json}\n{data_json}\n".encode("UTF-8")).decode("UTF-8")


def screen_shot_table(scheme: LoadedColorScheme) -> AsciinemaTerminal:
    example_text = "gYw"
    lines = [
        scheme["name"],
        "",
        "         def     40m     41m     42m     43m     44m     45m     46m     47m",
    ]
    for fg_space in [
        "    m",
        "   1m",
        "  30m",
        "1;30m",
        "  31m",
        "1;31m",
        "  32m",
        "1;32m",
        "  33m",
        "1;33m",
        "  34m",
        "1;34m",
        "  35m",
        "1;35m",
        "  36m",
        "1;36m",
        "  37m",
        "1;37m",
    ]:
        fg = fg_space.strip()
        line = f" {fg_space} \033[{fg}  {example_text}  "

        for bg in ["40m", "41m", "42m", "43m", "44m", "45m", "46m", "47m"]:
            line += f" \033[{fg}\033[{bg}  {example_text}  \033[0m"
        lines.append(line)

    lines.append("")
    lines.append("")

    return AsciinemaTerminal(
        title=scheme["name"],
        width=80,
        height=len(lines),
        lines=lines,
    )


class GenColorSchemePageTree(BasePage):
    def __init__(self, title: str, dirname: str):
        self.title = title
        self.dir = Path(dirname)

    def render(self, output: TextIO, *, depth: int = 0, mode: RenderMode) -> None:
        with open("colorschemes/data.json") as f:
            schemes_data: list[RawColorSchemeData] = json.load(f)
        by_prefix: dict[str, list[LoadedColorScheme]] = {}
        by_name: dict[str, LoadedColorScheme] = {}
        for scheme in schemes_data:
            try:
                scheme = load_scheme(scheme)
            except KeyError as err:
                raise ValueError(f"Failed to load colorscheme {scheme!r}: (KeyError) {err}")
            prefix = scheme["prefix"]
            if prefix not in by_prefix:
                by_prefix[prefix] = []
            by_prefix[prefix].append(scheme)
            by_name[scheme["name"]] = scheme

        style_filepath = self.dir / "scheme.css"
        with open(style_filepath, "w") as style_file:
            for scheme in by_name.values():
                style_file.write(scheme["css"])
                style_file.write("\n")
        js_filepath = self.dir / "scheme.js"
        with open(js_filepath, "w") as js_file:
            terminal_data_by_scheme: dict[str, str] = {}
            for scheme in by_name.values():
                ident = scheme["ident"]
                terminal_data = screen_shot_table(scheme)
                terminal_data_by_scheme[ident] = terminal_data.to_base64()

            js_file.write(f"SCHEME_DATA = {json.dumps(terminal_data_by_scheme)};\n")
            js_file.write(
                """
function load_scheme_player(ident) {
  var data = SCHEME_DATA[ident];
  AsciinemaPlayer.create(
    'data:text/plain;base64,' + data,
    document.getElementById(ident + '-player'), {
    theme: ident,
    autoPlay: true,
  });
}
"""
            )

        children: list[BasePage] = []
        for scheme_prefix in sorted(by_prefix.keys()):
            scheme_filepath = self.dir / scheme_prefix / "index.md"
            scheme_filepath.parent.mkdir(exist_ok=True)
            children.append(Page(scheme_prefix, scheme_filepath))

            with open(scheme_filepath, "w") as idx_file:
                idents_to_load = []

                idx_file.write(
                    f"""---
title: Color Schemes with first letter "{scheme_prefix}"
---

"""
                )

                for scheme in by_prefix[scheme_prefix]:
                    title = scheme["name"]
                    idx_file.write(f"## {title}\n")

                    ident = scheme["ident"]
                    idents_to_load.append(ident)

                    idx_file.write(
                        f"""
<div id="{ident}-player"></div>
"""
                    )

                    author = scheme["metadata"].get("author", None)
                    if author:
                        idx_file.write(f"Author: `{author}`<br/>\n")
                    origin_url = scheme["metadata"].get("origin_url", None)
                    if origin_url:
                        idx_file.write(f"Source: <{origin_url}><br/>\n")
                    version = scheme["metadata"].get("wezterm_version", None)
                    if version and version != "Always":
                        idx_file.write(f"{{{{since('{version}')}}}}<br/>\n")

                    aliases = scheme["metadata"]["aliases"]
                    if len(aliases) > 0:
                        aliases = ", ".join(f"`{a}`" for a in aliases)
                        idx_file.write(f"This scheme is also known as {aliases}.<br/>\n")

                    idx_file.write("\n")
                    idx_file.write("To use this scheme, add this to your config:\n")
                    idx_file.write(
                        f"""
```lua
config.color_scheme = '{title}'
```

"""
                    )

                idents_to_load = json.dumps(idents_to_load)
                idx_file.write(
                    f"""
<script>
document.addEventListener("DOMContentLoaded", function() {{
  {idents_to_load}.forEach(ident => load_scheme_player(ident));
}});
</script>
"""
                )

        index_filepath = self.dir / "index.md"
        index_page = Page(self.title, index_filepath, children=children)
        index_page.render(output, depth=depth, mode=mode)

        with open(self.dir / "index.md", "w") as idx_file:
            idx_file.write(f"{len(schemes_data)} Color schemes listed by first letter\n\n")
            for page in children:
                upper = page.title.upper()
                idx_file.write(f"  - [{upper}]({page.title}/index.md)\n")


TOC = [
    Page(
        "WezTerm",
        "index.md",
        children=[
            Page("Features", "features.md"),
            Page("Scrollback", "scrollback.md"),
            Page("Quick Select Mode", "quickselect.md"),
            Page("Copy Mode", "copymode.md"),
            Page("Hyperlinks", "hyperlinks.md"),
            Page("Shell Integration", "shell-integration.md"),
            Page("iTerm Image Protocol", "imgcat.md"),
            Page("SSH", "ssh.md"),
            Page("Serial Ports & Arduino", "serial.md"),
            Page("Multiplexing", "multiplexing.md"),
        ],
    ),
    Page(
        "Download",
        "installation.md",
        children=[
            Page("Windows", "install/windows.md"),
            Page("macOS", "install/macos.md"),
            Page("Linux", "install/linux.md"),
            Page("FreeBSD", "install/freebsd.md"),
            Page("NetBSD", "install/netbsd.md"),
            Page("Build from source", "install/source.md"),
        ],
    ),
    Page(
        "Configuration",
        "config/files.md",
        children=[
            Page("Colors & Appearance", "config/appearance.md"),
            Page("Launching Programs", "config/launch.md"),
            Page("Fonts", "config/fonts.md"),
            Page("Font Shaping", "config/font-shaping.md"),
            Page("Keyboard Concepts", "config/keyboard-concepts.md"),
            Page("Key Binding", "config/keys.md"),
            Page("Key Tables", "config/key-tables.md"),
            Page("Default Key Assignments", "config/default-keys.md"),
            Page("Keyboard Encoding", "config/key-encoding.md"),
            Page("Mouse Binding", "config/mouse.md"),
            Page("Plugins", "config/plugins.md"),
            GenColorSchemePageTree("Color Schemes", "colorschemes"),
            GenIndexPage("Recipes", "recipes", extract_title=True),
        ],
    ),
    Page(
        "Full Config & Lua Reference",
        "config/lua/general.md",
        children=[
            GenIndexPage(
                "Config Options",
                "config/lua/config",
            ),
            GenIndexPage(
                "module: wezterm",
                "config/lua/wezterm",
            ),
            GenIndexPage(
                "module: wezterm.color",
                "config/lua/wezterm.color",
            ),
            GenIndexPage(
                "module: wezterm.gui",
                "config/lua/wezterm.gui",
            ),
            GenIndexPage(
                "module: wezterm.mux",
                "config/lua/wezterm.mux",
            ),
            GenIndexPage(
                "module: wezterm.plugin",
                "config/lua/wezterm.plugin",
            ),
            GenIndexPage(
                "module: wezterm.procinfo",
                "config/lua/wezterm.procinfo",
            ),
            GenIndexPage(
                "module: wezterm.serde",
                "config/lua/wezterm.serde",
            ),
            GenIndexPage(
                "module: wezterm.time",
                "config/lua/wezterm.time",
            ),
            GenIndexPage(
                "module: wezterm.url",
                "config/lua/wezterm.url",
            ),
            GenIndexPage(
                "enum: KeyAssignment",
                "config/lua/keyassignment",
            ),
            GenIndexPage(
                "enum: CopyModeAssignment",
                "config/lua/keyassignment/CopyMode",
            ),
            GenIndexPage("object: Color", "config/lua/color"),
            Page("object: ExecDomain", "config/lua/ExecDomain.md"),
            Page("object: LocalProcessInfo", "config/lua/LocalProcessInfo.md"),
            GenIndexPage("object: MuxDomain", "config/lua/MuxDomain"),
            GenIndexPage("object: MuxWindow", "config/lua/mux-window"),
            GenIndexPage("object: MuxTab", "config/lua/MuxTab"),
            Page("object: PaneInformation", "config/lua/PaneInformation.md"),
            Page("object: TabInformation", "config/lua/TabInformation.md"),
            Page("object: SshDomain", "config/lua/SshDomain.md"),
            Page("object: SpawnCommand", "config/lua/SpawnCommand.md"),
            GenIndexPage("object: Time", "config/lua/wezterm.time/Time"),
            Page("object: TlsDomainClient", "config/lua/TlsDomainClient.md"),
            Page("object: TlsDomainServer", "config/lua/TlsDomainServer.md"),
            GenIndexPage(
                "object: Pane",
                "config/lua/pane",
            ),
            GenIndexPage(
                "object: Window",
                "config/lua/window",
            ),
            Page("object: WslDomain", "config/lua/WslDomain.md"),
            GenIndexPage(
                "events: Gui",
                "config/lua/gui-events",
            ),
            GenIndexPage(
                "events: Multiplexer",
                "config/lua/mux-events",
            ),
            GenIndexPage(
                "events: Window",
                "config/lua/window-events",
            ),
        ],
    ),
    Page(
        "CLI Reference",
        "cli/general.md",
        children=[
            GenIndexPage("wezterm cli", "cli/cli"),
            Page("wezterm connect", "cli/connect.md"),
            Page("wezterm imgcat", "cli/imgcat.md"),
            Page("wezterm ls-fonts", "cli/ls-fonts.md"),
            Page("wezterm record", "cli/record.md"),
            Page("wezterm replay", "cli/replay.md"),
            Page("wezterm serial", "cli/serial.md"),
            Page("wezterm set-working-directory", "cli/set-working-directory.md"),
            Page("wezterm show-keys", "cli/show-keys.md"),
            Page("wezterm ssh", "cli/ssh.md"),
            Page("wezterm start", "cli/start.md"),
        ],
    ),
    Page(
        "Reference",
        None,
        children=[
            Page("Escape Sequences", "escape-sequences.md"),
            Page("What is a Terminal?", "what-is-a-terminal.md"),
        ],
    ),
    Page(
        "Get Help",
        None,
        children=[
            Page("Troubleshooting", "troubleshooting.md"),
            Page("F.A.Q.", "faq.md"),
            Page("Getting Help", "help.md"),
            Page("Contributing", "contributing.md"),
        ],
    ),
    Page("Change Log", "changelog.md"),
    Page("Sponsor", "sponsor.md"),
]

def main():
    os.chdir("docs")

    with open("../mkdocs.yml", "w") as f:
        f.write("# this is auto-generated by docs/generate-toc.py, do not edit\n")
        f.write("INHERIT: docs/mkdocs-base.yml\n")
        f.write("nav:\n")
        for page in TOC:
            page.render(f, depth=1, mode="mkdocs")


    with open("SUMMARY.md", "w") as f:
        f.write("[root](index.md)\n")
        for page in TOC:
            page.render(f, depth=1, mode="mdbook")


if __name__ == "__main__":
    main()
