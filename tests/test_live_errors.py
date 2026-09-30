import pytest

from fairfare import cli
from fairfare.tools.fetch import HttpFetcher


def test_fetch_error_keeps_root_cause(monkeypatch):
    f = HttpFetcher()
    f.browser = False
    monkeypatch.setattr(f, "_http", lambda url: (_ for _ in ()).throw(ConnectionError("proxy 403")))
    with pytest.raises(RuntimeError, match="proxy 403"):
        f.fetch("https://example.com")


def test_cli_turns_failures_into_readable_exit(monkeypatch):
    def boom(args):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(cli, "cmd_traces", boom)
    monkeypatch.setattr("sys.argv", ["fairfare", "traces", "list"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "no LLM reachable" in str(e.value)
