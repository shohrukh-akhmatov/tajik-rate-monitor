"""Install stub requests/playwright modules so pure functions can be tested
without the runtime dependencies or any network access."""

import os
import sys
import tempfile
import types


class _NetworkDisabledError(RuntimeError):
    pass


def _deny(*args, **kwargs):
    raise _NetworkDisabledError("network disabled in tests")


def install():
    # Run in a scratch dir so importing monitor (which mkdirs ./site) does not
    # pollute the repository working tree.
    os.chdir(tempfile.mkdtemp(prefix="rate_monitor_tests_"))
    if "requests" in sys.modules:
        return
    requests_stub = types.ModuleType("requests")
    requests_stub.get = _deny
    requests_stub.post = _deny
    requests_stub.request = _deny
    requests_stub.RequestException = _NetworkDisabledError
    exceptions = types.ModuleType("requests.exceptions")
    exceptions.RequestException = _NetworkDisabledError
    exceptions.ConnectionError = _NetworkDisabledError
    exceptions.Timeout = _NetworkDisabledError
    requests_stub.exceptions = exceptions
    sys.modules["requests"] = requests_stub
    sys.modules["requests.exceptions"] = exceptions

    api_stub = types.ModuleType("playwright.async_api")
    api_stub.Browser = type("Browser", (), {})
    api_stub.Page = type("Page", (), {})
    api_stub.TimeoutError = _NetworkDisabledError
    api_stub.async_playwright = lambda: _deny()
    playwright_stub = types.ModuleType("playwright")
    playwright_stub.async_api = api_stub
    sys.modules["playwright"] = playwright_stub
    sys.modules["playwright.async_api"] = api_stub
