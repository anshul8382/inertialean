"""Web server entry point."""

import typer


def main():
    typer.run(lambda: __import__("iis.cli.main", fromlist=["app"]).app(["serve"]))
