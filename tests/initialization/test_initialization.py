"""运行时初始化测试：环境加载结果、三个服务日志文件及按天 / 按大小轮转、
initialize_runtime 先配日志再加载环境和配置、web_runner 关闭 uvicorn 默认日志配置。
"""

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

    from easy_agent.bootstrap.initialization import load_environment

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
    from easy_agent.bootstrap.initialization import log as logging_config
    from easy_agent.bootstrap.initialization import setup_logging
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
        # proc 收录全量日志：过程日志、错误日志、通信日志（uvicorn.access）均写入
        assert proc_file.read_text(encoding="utf-8") == (
            "WARNING log ready\n"
            "ERROR error ready\n"
            "WARNING uvicorn error ready\n"
            "WARNING uvicorn access ready\n"
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
    from easy_agent.bootstrap.initialization import setup_logging
    from easy_agent.bootstrap.initialization import log as logging_config

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
    from easy_agent.bootstrap.initialization import setup_logging
    from easy_agent.bootstrap.initialization import log as logging_config

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

    from easy_agent.bootstrap.initialization import runtime as runtime_module

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
    import easy_agent.bootstrap.initialization as initialization
    import easy_agent.bootstrap.web_runner as web_runner

    monkeypatch.setattr(initialization, "initialize_runtime", lambda: None)
    captured_kwargs = {}

    def fake_uvicorn_run(*args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(web_runner.uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr("sys.argv", ["easy-web"])

    web_runner.run_web()

    assert captured_kwargs["log_config"] is None


# ── 运行期日志级别（EASY_LOG_LEVEL / easy-web --log-level）────────────────


def _snapshot_loggers(names):
    loggers = [logging.getLogger(), *(logging.getLogger(n) for n in names)]
    return [(lg, lg.level, lg.handlers[:], lg.propagate) for lg in loggers]


def _restore_loggers(saved):
    for logger, level, handlers, propagate in saved:
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate


def test_parse_log_level_accepts_names_case_insensitively():
    from easy_agent.bootstrap.initialization.log import parse_log_level

    assert parse_log_level("debug") == logging.DEBUG
    assert parse_log_level(" INFO ") == logging.INFO
    assert parse_log_level("Warn") == logging.WARNING
    assert parse_log_level("error") == logging.ERROR
    assert parse_log_level("verbose") is None
    assert parse_log_level("") is None
    assert parse_log_level(None) is None


def test_set_log_level_debug_moves_handlers_but_keeps_err_and_quiet_loggers(tmp_path, monkeypatch):
    from easy_agent.bootstrap.initialization import log as logging_config

    names = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi", "fastapi",
             *logging_config.LOG_QUIET_WHEN_DEBUG)
    saved = _snapshot_loggers(names)
    saved_quiet = [(n, logging.getLogger(n).level) for n in logging_config.LOG_QUIET_WHEN_DEBUG]
    try:
        monkeypatch.setattr(logging_config, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(logging_config, "LOG_LEVEL", "info")
        logging_config.setup_logging()
        root = logging.getLogger()

        logging_config.set_log_level(logging.DEBUG)

        assert root.level == logging.DEBUG
        file_levels = {
            Path(h.baseFilename).name.split("-")[0]: h.level
            for h in root.handlers
            if isinstance(h, TimedRotatingFileHandler)
        }
        assert file_levels == {"proc": logging.DEBUG, "comm": logging.DEBUG, "err": logging.ERROR}
        console = [h for h in root.handlers if not isinstance(h, TimedRotatingFileHandler)]
        assert [h.level for h in console] == [logging.DEBUG]
        assert logging.getLogger("uvicorn.error").level == logging.DEBUG
        # 第三方噪声（aiosqlite 每次读写、openai SDK 打完整请求体）在 DEBUG 下仍停在 INFO
        assert logging.getLogger("aiosqlite").getEffectiveLevel() == logging.INFO
        assert logging.getLogger("openai._base_client").getEffectiveLevel() == logging.INFO
        assert logging.getLogger("httpcore.http11").getEffectiveLevel() == logging.INFO
        # deepagents 每次建图重复报的 prompt caching 不可用 / HarnessProfile 解析
        assert logging.getLogger("deepagents.middleware._prompt_caching").getEffectiveLevel() == logging.INFO
        assert logging.getLogger("deepagents.profiles.harness.harness_profiles").getEffectiveLevel() == logging.INFO
        # 大模型报文 logger 跟随 root：DEBUG 时开启
        assert logging.getLogger("easy_agent.llm_wire").isEnabledFor(logging.DEBUG)

        logging_config.set_log_level(logging.INFO)

        assert root.level == logging.INFO
        assert logging.getLogger("aiosqlite").level == logging.NOTSET
        assert not logging.getLogger("easy_agent.llm_wire").isEnabledFor(logging.DEBUG)
    finally:
        for handler in logging.getLogger().handlers:
            if isinstance(handler, TimedRotatingFileHandler):
                handler.close()
        _restore_loggers(saved)
        for name, level in saved_quiet:
            logging.getLogger(name).setLevel(level)
        monkeypatch.setattr(logging_config, "_quieted", False)


def _fake_runtime(monkeypatch, tmp_path, calls, *, env=None):
    from types import SimpleNamespace

    from easy_agent.bootstrap.initialization import runtime as runtime_module

    def fake_load_environment():
        calls.append("environment")
        for key, value in (env or {}).items():  # 模拟 .env 文件注入
            monkeypatch.setenv(key, value)
        return SimpleNamespace(env_file=None, loaded_keys=[], agent_env="test", cors_allow_origins=["*"])

    monkeypatch.setattr(runtime_module, "_initialization", None)
    monkeypatch.setattr(runtime_module, "setup_logging", lambda: calls.append("logging") or {
        "proc": str(tmp_path / "p.log"), "err": str(tmp_path / "e.log"), "comm": str(tmp_path / "c.log")})
    monkeypatch.setattr(runtime_module, "load_environment", fake_load_environment)
    monkeypatch.setattr(runtime_module, "set_log_level", lambda level: calls.append(("level", level)))
    monkeypatch.setattr(runtime_module.Config, "resolve_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(runtime_module.Config, "from_yaml", lambda path: calls.append("config") or SimpleNamespace())
    return runtime_module


def test_initialize_runtime_applies_log_level_from_env_file(monkeypatch, tmp_path):
    """EASY_LOG_LEVEL 可以写在 .env 里：环境加载之后、读配置之前生效。"""
    monkeypatch.delenv("EASY_LOG_LEVEL", raising=False)
    calls: list = []
    runtime_module = _fake_runtime(monkeypatch, tmp_path, calls, env={"EASY_LOG_LEVEL": "debug"})

    runtime_module.initialize_runtime(force=True)

    assert calls == ["logging", "environment", ("level", logging.DEBUG), "config"]


def test_initialize_runtime_ignores_invalid_log_level(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("EASY_LOG_LEVEL", "verbose")
    calls: list = []
    runtime_module = _fake_runtime(monkeypatch, tmp_path, calls)

    with caplog.at_level(logging.WARNING, logger=runtime_module.__name__):
        runtime_module.initialize_runtime(force=True)

    assert calls == ["logging", "environment", "config"]
    assert "EASY_LOG_LEVEL='verbose' 无效" in caplog.text


def test_initialize_runtime_warns_that_llm_request_log_switch_is_retired(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("EASY_LOG_LEVEL", raising=False)
    monkeypatch.setenv("EASY_LLM_REQUEST_LOG", "1")
    runtime_module = _fake_runtime(monkeypatch, tmp_path, [])

    with caplog.at_level(logging.WARNING, logger=runtime_module.__name__):
        runtime_module.initialize_runtime(force=True)

    assert "EASY_LLM_REQUEST_LOG 已不再生效" in caplog.text


def test_web_runner_log_level_flag_sets_env(monkeypatch):
    import os

    import easy_agent.bootstrap.initialization as initialization
    import easy_agent.bootstrap.web_runner as web_runner

    # 先登记「原本没有」，测试结束时 monkeypatch 会把 run_web 写入的值删掉
    monkeypatch.delenv("EASY_LOG_LEVEL", raising=False)
    monkeypatch.setattr(initialization, "initialize_runtime", lambda: None)
    monkeypatch.setattr(web_runner.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["easy-web", "--log-level", "debug"])

    web_runner.run_web()

    assert os.environ["EASY_LOG_LEVEL"] == "debug"


def test_load_environment_defaults_agent_env_to_dev(monkeypatch):
    """未显式设置 AGENT_ENV 时统一按 dev 处理，并使用回环 CORS 默认值。"""
    from easy_agent.bootstrap.initialization import load_environment
    from easy_agent.bootstrap.initialization.environment import LOCAL_CORS_ORIGINS

    monkeypatch.setattr(env_loader, "_loaded", True)
    monkeypatch.delenv("AGENT_ENV", raising=False)
    monkeypatch.delenv("EASY_CORS_ALLOW_ORIGINS", raising=False)

    environment = load_environment()

    assert environment.agent_env == "dev"
    assert environment.cors_allow_origins == LOCAL_CORS_ORIGINS
