import pytest

from app.config import settings
from app.fpbx import extensions


def test_pool_matches_settings():
    assert extensions.POOL.start == settings.ext_pool_start
    assert extensions.POOL.stop - 1 == settings.ext_pool_end


def test_validate_inside_pool():
    assert extensions.validate(settings.ext_pool_start) == settings.ext_pool_start
    assert extensions.validate(settings.ext_pool_end) == settings.ext_pool_end


@pytest.mark.parametrize("ext", [9549, 9600, 0, 9000])
def test_validate_outside_pool_raises(ext):
    with pytest.raises(ValueError):
        extensions.validate(ext)
