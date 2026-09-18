"""Apply the same offline isolation to pytest before test-module collection."""
from tools.test import offline_environment


def pytest_sessionstart(session):
    session.offline_context = offline_environment()
    session.offline_context.__enter__()


def pytest_sessionfinish(session, exitstatus):
    session.offline_context.__exit__(None, None, None)
