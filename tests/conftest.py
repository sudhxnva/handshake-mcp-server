"""Test configuration and fixtures."""

import pytest


@pytest.fixture
def sample_student_id() -> str:
    return "12345678"


@pytest.fixture
def sample_employer_id() -> str:
    return "123456"


@pytest.fixture
def sample_job_id() -> str:
    return "9876543"


@pytest.fixture
def sample_event_id() -> str:
    return "654321"
