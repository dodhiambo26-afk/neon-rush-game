from html.parser import HTMLParser
from pathlib import Path
import subprocess
import tempfile


class InlineScriptExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_inline_script = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self.in_inline_script = not any(
                key.lower() == "src" for key, _ in attrs
            )

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            self.in_inline_script = False

    def handle_data(self, data):
        if self.in_inline_script:
            self.parts.append(data)


def main():
    html = Path("index.html").read_text(encoding="utf-8")
    parser = InlineScriptExtractor()
    parser.feed(html)
    source = "\n".join(parser.parts)

    if not source.strip():
        raise SystemExit("No inline game JavaScript was found.")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", encoding="utf-8"
    ) as temp:
        temp.write(source)
        temp.flush()
        subprocess.run(["node", "--check", temp.name], check=True)

    print("Game JavaScript syntax check passed.")


if __name__ == "__main__":
    main()
