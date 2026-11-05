import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("gen_api_docs", ROOT / "scripts/gen-api-docs.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def test_the_api_page_matches_the_routes():
    assert (ROOT / "docs/api.md").read_text() == gen.page(), "run scripts/gen-api-docs.py"
