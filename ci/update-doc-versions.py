#!/usr/bin/env python3

# TODO: explain what this script does / is about!

import glob
import re

# TODO: explain when this should be updated 🤔
# IDEA: rename to VERSION? or require as cli arg?
NIGHTLY = "20240203-110809-5046fc22"

SINCE_RX = re.compile(re.escape("{{since('nightly'"), re.MULTILINE)

COLORSCHEME_DATA_PATH = "docs/colorschemes/data.json"

def update_docs_pages() -> None:
    for p in ["docs/**/*.md", "docs/**/*.markdown"]:
        for filename in glob.glob(p, recursive=True):
            with open(filename) as f:
                content = f.read()

            adjusted = SINCE_RX.sub(f"{{{{since('{NIGHTLY}'", content)
            if content != adjusted:
                print(f"File {filename!r} has 'nightly' docs, updating..")
                with open(filename, "w") as f:
                    f.truncate()
                    f.write(adjusted)


def update_colorscheme_defs() -> None:
    with open(COLORSCHEME_DATA_PATH) as f:
        content = f.read()

    new_content = content.replace("nightly builds only", NIGHTLY)
    if new_content != content:
        print("One or more colorschemes mentioned 'nightly', updating..")
        with open(COLORSCHEME_DATA_PATH, "w") as f:
            f.truncate()
            f.write(new_content)


def main() -> None:
    print()
    print(f":: Updating files mentioning 'nightly', replace with {NIGHTLY!r}...")

    print()
    print(":: Updating docs pages...")
    update_docs_pages()

    print()
    print(":: Updating colorschemes definitions...")
    update_colorscheme_defs()

    print()
    print("Done!")
    print()


if __name__ == "__main__":
    main()
