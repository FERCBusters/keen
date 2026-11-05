"""Feature availability is read only by the keys Keen defines.

A misspelt key is refused rather than read as on or off, and every key the
entity and section maps hand to the feature state is a defined one.
"""

import pytest
from app.services import features


@pytest.mark.parametrize("configured", [{}, {"isms": False}])
def test_an_unknown_feature_key_is_refused_whatever_the_settings(configured):
    state = features.compile_state(1, configured)
    for read in (state.enabled, state.require):
        with pytest.raises(ValueError, match="^Unknown feature$"):
            read("telepathy")
    # A refused key in a list refuses the whole requirement.
    with pytest.raises(ValueError, match="^Unknown feature$"):
        state.require("controls", "telepathy")


def test_every_mapped_feature_key_is_defined():
    mapped = set(features.ENTITY_FEATURES.values()) | set(
        features.SECTION_FEATURES.values()
    )
    assert mapped <= features.KEYS
    state = features.compile_state(1, {})
    assert all(state.enabled(key) for key in sorted(mapped))
