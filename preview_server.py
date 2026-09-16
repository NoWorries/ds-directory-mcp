"""Local dev server approximating Netlify's pretty_urls (netlify.toml) —
/directory resolves to directory.html, /systems/<slug> to systems/<slug>.html,
etc. — since a plain `python -m http.server` 404s on every extensionless
link this site now uses. Not needed in production; Netlify does this itself.
"""
import http.server
from pathlib import Path

ROOT = Path(__file__).parent


class PrettyURLHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        fs_path = Path(super().translate_path(clean))
        # home.html only becomes index.html in the real deploy (the
        # workflows' `cp home.html site/index.html` step) — mimic that here
        # since there's no actual index.html sitting in the repo root.
        if fs_path == ROOT:
            fs_path = ROOT / "home.html"
        elif fs_path.is_dir():
            fs_path = fs_path / "index.html"
        elif not fs_path.exists() and fs_path.with_suffix(".html").exists():
            fs_path = fs_path.with_suffix(".html")
        return str(fs_path)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Open http://localhost:{port} in your browser (ctrl-C to stop)")
    http.server.test(PrettyURLHandler, port=port, bind="localhost")
