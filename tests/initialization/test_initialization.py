import logging
import logging.config
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from easy_agent.utils import env_loader


def test_load_environment_returns_isolated_environment_state(tmp_path, monkeypatch):
    env_file = tmp_path / "test.env"
    env_file.write_text("EASY_INITIALIZATION_TEST=loaded\n", encoding="utf-8")

    monkeypatch.setattr(env_loader, "_loaded", False)
    monkeypatch.setattr(env_loader, "_env_file", None)
    monkeypatch.setattr(env_loader, "_env_keys", [])
    monkeypatch.setenv("EASY_ENV_FILE", str(env_file))
    monkeypatch.setenv("EASY_LOG_DIR", str(tmp_path / "ignored-logs"))
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setenv("EASY_CORS_ALLOW_ORIGINS", "https://api.example.com, https://app.example.com")
    monkeypatch.delenv("EASY_INITIALIZATION_TEST", raising=False)

    from easy_agent.initialization import load_environment

    environment = load_environment()

    assert environment.env_file == str(env_file)
    assert environment.loaded_keys == ["EASY_INITIALIZATION_TEST"]
    assert not hasattr(environment, "log_dir")
    assert environment.agent_env == "prod"
    assert environment.cors_allow_origins == [
        "https://api.example.com",
        "https://app.example.com",
    ]


def test_setup_logging_creates_three_service_log_files(tmp_path, monkeypatch):
    from easy_agent.initialization import logging as logging_config
    from easy_agent.initialization import setup_logging
    from uvicorn.config import LOGGING_CONFIG

    root_logger = logging.getLogger()
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    fastapi_logger = logging.getLogger("fastapi")
    saved_state = [
        (logger, logger.level, logger.handlers[:], logger.propagate)
        for logger in (
            root_logger,
            uvicorn_logger,
            uvicorn_error_logger,
            uvicorn_access_logger,
            fastapi_logger,
        )
    ]

    try:
        logging.config.dictConfig(LOGGING_CONFIG)
        monkeypatch.setattr(logging_config, "LOG_DIR", str(tmp_path / "service-logs"))
        monkeypatch.setattr(logging_config, "LOG_SERVICE_NAME", "easy-agent")
        monkeypatch.setattr(logging_config, "LOG_VERSION", "v01")
        monkeypatch.setattr(logging_config, "LOG_LEVEL", "warning")
        monkeypatch.setattr(
            logging_config, "LOG_FORMAT", "%(levelname)s %(message)s"
        )
        log_files = setup_logging()

        logging.getLogger("initialization-test").warning("log ready")
        logging.getLogger("initialization-test").error("error ready")
        uvicorn_error_logger.warning("uvicorn error ready")
        uvicorn_access_logger.warning("uvicorn access ready")
        fastapi_logger.warning("fastapi ready")

        assert log_files == {
            "proc": str(
                tmp_path / "service-logs" / "proc-v01-easy-agent.log"
            ),
            "err": str(tmp_path / "service-logs" / "err-v01-easy-agent.log"),
            "comm": str(tmp_path / "service-logs" / "comm-v01-easy-agent.log"),
        }
        assert root_logger.level == logging.WARNING
        assert uvicorn_logger.level == logging.WARNING
        assert uvicorn_access_logger.level == logging.WARNING
        assert Path(log_files["proc"]).exists()
        assert Path(log_files["err"]).exists()
        assert Path(log_files["comm"]).exists()
        for log_file in log_files.values():
            for handler in root_logger.handlers:
                if isinstance(handler, TimedRotatingFileHandler):
                    handler.flush()

        proc_file = Path(log_files["proc"])
        err_file = Path(log_files["err"])
        comm_file = Path(log_files["comm"])
        assert proc_file.read_text(encoding="utf-8") == (
            "WARNING log ready\n"
            "ERROR error ready\n"
            "WARNING uvicorn error ready\n"
            "WARNING fastapi ready\n"
        )
        assert err_file.exists()
        assert err_file.read_text(encoding="utf-8") == "ERROR error ready\n"
        assert comm_file.read_text(encoding="utf-8") == "WARNING uvicorn access ready\n"
    finally:
        for logger, level, handlers, propagate in saved_state:
            logger.setLevel(level)
            logger.handlers[:] = handlers
            logger.propagate = propagate


def test_setup_logging_uses_compiled_rotation_matcher(tmp_path, monkeypatch):
    from easy_agent.initialization import setup_logging
    from easy_agent.initialization import logging as logging_config

    root_logger = logging.getLogger()
    saved_handlers = root_logger.handlers[:]
    saved_level = root_logger.level
    root_logger.handlers.clear()

    try:
        monkeypatch.setattr(logging_config, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(logging_config, "LOG_SERVICE_NAME", "test-service")
        monkeypatch.setattr(logging_config, "LOG_LEVEL", "warning")
        setup_logging()
        file_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, TimedRotatingFileHandler)
        ]
        file_handler = file_handlers[0]

        assert len(file_handlers) == 3
        assert (tmp_path / "proc-v01-test-service.log").exists()
        assert (tmp_path / "err-v01-test-service.log").exists()
        assert (tmp_path / "comm-v01-test-service.log").exists()
        for handler in file_handlers:
            assert callable(handler.extMatch.fullmatch)
            assert handler.extMatch.fullmatch("2026-09-08")

        for index in range(31):
            rotation_file = tmp_path / f"proc-v01-test-service.log.2000-01-{index + 1:02d}"
            rotation_file.write_text("old log\n", encoding="utf-8")

        file_handler.doRollover()

        assert len(list(tmp_path.glob("proc-v01-test-service.log.*"))) == 30
    finally:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


def test_setup_logging_rotates_when_log_exceeds_size_limit(tmp_path, monkeypatch):
    from easy_agent.initialization import setup_logging
    from easy_agent.initialization import logging as logging_config

    assert logging_config.LOG_MAX_BYTES == 50 * 1024 * 1024

    root_logger = logging.getLogger()
    saved_handlers = root_logger.handlers[:]
    saved_level = root_logger.level
    root_logger.handlers.clear()

    try:
        monkeypatch.setattr(logging_config, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(logging_config, "LOG_SERVICE_NAME", "size-service")
        monkeypatch.setattr(logging_config, "LOG_LEVEL", "warning")
        monkeypatch.setattr(logging_config, "LOG_MAX_BYTES", 128)
        monkeypatch.setattr(
            logging_config, "LOG_FORMAT", "%(levelname)s %(message)s"
        )
        log_files = setup_logging()

        logging.getLogger("size-test").warning("small message")
        logging.getLogger("size-test").warning("x" * 256)

        for handler in root_logger.handlers:
            handler.flush()

        current_log = Path(log_files["proc"])
        rotated_logs = list(tmp_path.glob("proc-v01-size-service.log.*"))
        assert current_log.exists()
        assert len(rotated_logs) == 1
        assert current_log.read_text(encoding="utf-8") == "WARNING " + "x" * 256 + "\n"
    finally:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


def test_initialize_runtime_sets_up_logging_before_environment_and_config(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from easy_agent.initialization import runtime as runtime_module

    calls = []
    config_path = tmp_path / "config.yaml"
    log_files = {
        "proc": str(tmp_path / "proc.log"),
        "err": str(tmp_path / "err.log"),
        "comm": str(tmp_path / "comm.log"),
    }

    def fake_setup_logging():
        calls.append("logging")
        return log_files

    def fake_load_environment():
        calls.append("environment")
        return SimpleNamespace(
            env_file=None,
            loaded_keys=[],
            agent_env="test",
            cors_allow_origins=["*"],
        )

    monkeypatch.setattr(runtime_module, "_initialization", None)
    monkeypatch.setattr(runtime_module, "setup_logging", fake_setup_logging)
    monkeypatch.setattr(runtime_module, "load_environment", fake_load_environment)
    monkeypatch.setattr(
        runtime_module.Config,
        "resolve_config_path",
        lambda: config_path,
    )
    monkeypatch.setattr(
        runtime_module.Config,
        "from_yaml",
        lambda path: calls.append("config") or SimpleNamespace(),
    )

    initialization = runtime_module.initialize_runtime(force=True)

    assert calls == ["logging", "environment", "config"]
    assert initialization.log_files == log_files


def test_web_runner_disables_uvicorn_default_logging(monkeypatch):
    import easy_agent.initialization as initialization
    import easy_agent.web_runner as web_runner

    monkeypatch.setattr(initialization, "initialize_runtime", lambda: None)
    captured_kwargs = {}

    def fake_uvicorn_run(*args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(web_runner.uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr("sys.argv", ["easy-web"])

    web_runner.run_web()

    assert captured_kwargs["log_config"] is None
