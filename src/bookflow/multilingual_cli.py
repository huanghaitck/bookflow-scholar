from __future__ import annotations

import json
from pathlib import Path
import typer

from .translation_runner import TranslationRunner
from .translation_units import build_multilingual_layer

app = typer.Typer()
@app.command()
def build(): print(json.dumps(build_multilingual_layer(Path("."))["manifest"],ensure_ascii=False,indent=2))
@app.command()
def plan(max_units:int|None=None): print(json.dumps(TranslationRunner(Path(".")).plan(max_units),indent=2))
@app.command("dry-run")
def dry_run(max_units:int|None=None): print(json.dumps(TranslationRunner(Path(".")).plan(max_units),indent=2))
@app.command()
def status(): print(json.dumps(TranslationRunner(Path(".")).status(),indent=2))
@app.command()
def validate(): print(json.dumps(json.loads(Path("data/fullbook/multilingual/reports/multilingual_validation_zh-Hans_v1.json").read_text("utf-8")),indent=2))
@app.command()
def resume(max_units:int=10): print(json.dumps(TranslationRunner(Path(".")).run_mock(max_units),indent=2))
@app.command()
def translate(max_units:int=10, allow_real_api:bool=False):
    if allow_real_api: raise typer.BadParameter("real API execution is disabled for Phase 7+8")
    print(json.dumps(TranslationRunner(Path(".")).run_mock(max_units),indent=2))

if __name__ == "__main__": app()
