"""Pytest configuration and fixtures."""
import pytest


@pytest.fixture
def mock_runner(mocker):
    """Mock command runner."""
    return mocker.MagicMock()


@pytest.fixture
def mock_detector(mocker):
    """Mock system detector."""
    return mocker.MagicMock()
