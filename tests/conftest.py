import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_app: pure unit tests that must not call create_app() (no DB required)",
    )


@pytest.fixture(scope="session")
def app():
    from main import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(autouse=True)
def app_context(request):
    """All tests run inside Flask app context unless marked no_app."""
    if request.node.get_closest_marker("no_app"):
        yield
        return
    app = request.getfixturevalue("app")
    with app.app_context():
        yield


@pytest.fixture
def client(app):
    return app.test_client()
