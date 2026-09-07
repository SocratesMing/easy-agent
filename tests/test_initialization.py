import logging
from pathlib import Path

from easy_agent.utils import env_loader


def test_load_environment_returns_isolated_environment_state(tmp_path, monkeypatch):
    env_file = tmp_path / "test.env"
    env_file.write_text("EASY_INITIALIZATION_TEST=loaded\n", encoding="utf-8")

    monkeypatch.setattr(env_loader, "_loaded", False)
    monkeypatch.setattr(env_loader, "_env_file", None)
    monkeypatch.setattr(env_loader, "_env_keys", [])
    monkeypatch.setenv("EASY_ENV_FILE", str(env_file))
    monkeypatch.setenv("EASY_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setenv("EASY_CORS_ALLOW_ORIGINS", "https://api.example.com, https://app.example.com")
    monkeypatch.delenv("EASY_INITIALIZATION_TEST", raising=False)

    from easy_agent.initialization import load_environment

    environment = load_environment()

    assert environment.env_file == str(env_file)
    assert environment.loaded_keys == ["EASY_INITIALIZATION_TEST"]
    assert environment.log_dir == str(tmp_path / "logs")
    assert environment.agent_env == "prod"
    assert environment.cors_allow_origins == [
        "https://api.example.com",
        "https://app.example.com",
    ]


def test_setup_logging_uses_only_explicit_arguments(tmp_path):
    from easy_agent.initialization import setup_logging

    root_logger = logging.getLogger()
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    saved_state = [
        (logger, logger.level, logger.handlers[:], logger.propagate)
        for logger in (root_logger, uvicorn_logger, uvicorn_access_logger)
    ]

    try:
        log_file = setup_logging(
            {
                "dir": str(tmp_path / "config-logs"),
                "file": "agent.log",
                "level": "warning",
                "format": "%(levelname)s %(message)s",
            },
            log_dir=str(tmp_path / "env-logs"),
        )

        logging.getLogger("initialization-test").warning("log ready")

        assert log_file == str(tmp_path / "env-logs" / "agent.log")
        assert root_logger.level == logging.WARNING
        assert uvicorn_logger.level == logging.WARNING
        assert uvicorn_access_logger.level == logging.WARNING
        assert Path(log_file).read_text(encoding="utf-8") == "WARNING log ready\n"
    finally:
        for logger, level, handlers, propagate in saved_state:
            logger.setLevel(level)
            logger.handlers[:] = handlers
            logger.propagate = propagate
