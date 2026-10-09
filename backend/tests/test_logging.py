import logging

import pytest

from app.logging_utils import log_event


def test_logs_ids_and_counts(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        log_event("ingest.done", contact_id=1, inserted=3, skipped=0)
    assert "ingest.done contact_id=1 inserted=3 skipped=0" in caplog.text


@pytest.mark.parametrize("field", ["text", "content", "final_text", "draft_text", "messages"])
def test_refuses_content_fields(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        log_event("x", **{field: "hello"})


def test_refuses_long_strings() -> None:
    with pytest.raises(ValueError, match="too long"):
        log_event("x", status="I'll call you tonight after work, promise " * 3)


def test_refuses_non_scalars() -> None:
    with pytest.raises(TypeError):
        log_event("x", ids=[1, 2, 3])  # type: ignore[arg-type]
