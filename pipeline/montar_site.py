"""Gera site/index.html (documento completo para GitHub Pages / Cloudflare Pages) a partir de web/radar.html."""
import pathlib

CABECALHO = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}[hidden]{display:none!important}</style>
</head>
<body>
"""


def main():
    raiz = pathlib.Path(__file__).resolve().parent.parent
    corpo = (raiz / "web/radar.html").read_text(encoding="utf-8")
    (raiz / "site/index.html").write_text(CABECALHO + corpo + "\n</body>\n</html>\n", encoding="utf-8")


if __name__ == "__main__":
    main()
