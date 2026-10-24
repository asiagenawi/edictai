from edictai import docs
from edictai.paths import Paths

from .conftest import REPO


def test_dictionary_renders_every_record_type(tree):
    text = docs.render(tree)
    for title in (
        "Legislation",
        "Policy document",
        "Executive action",
        "Research item",
        "Organization",
    ):
        assert f"### {title}" in text
    assert "| `actions[].classification` |" in text
    assert "| `verification.last_verified` |" in text
    assert "### action_classifications" in text
    assert "### `legislation`" in text


def test_committed_dictionary_is_current():
    assert docs.write(Paths(REPO), check=True), "run `edictai docs`"
