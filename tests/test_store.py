import pytest

from canopy import store


def test_shipped_migrations_are_numbered_without_gaps():
    assert [n for n, _ in store.available()][0] == 1


def test_a_gap_in_the_numbers_is_refused(tmp_path):
    (tmp_path / "001_a.sql").write_text("")
    (tmp_path / "003_b.sql").write_text("")
    with pytest.raises(RuntimeError, match="no gaps"):
        store.available(tmp_path)
