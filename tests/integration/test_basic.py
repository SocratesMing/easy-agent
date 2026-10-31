"""Basic tests for Easy Agent"""
import pytest
from pathlib import Path
import tempfile


class TestConfig:
    """Test configuration module"""

    def test_config_from_yaml_missing_file(self):
        """Test that Config.from_yaml raises FileNotFoundError when file doesn't exist"""
        from easy_agent.bootstrap.config import Config

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                Config.from_yaml(Path(tmpdir) / "no_such_config.yaml")

    def test_config_from_yaml_missing_fields(self):
        """Test that from_yaml raises ValueError for missing required fields"""
        from easy_agent.bootstrap.config import Config

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("model: test-model\n")
            f.flush()
            temp_path = f.name

        try:
            with pytest.raises(ValueError, match="api_key"):
                Config.from_yaml(temp_path)
        finally:
            Path(temp_path).unlink()


class TestLogger:
    """Test logger module"""

    def test_logger_initialization(self):
        """Test that logger initializes correctly"""
        from easy_agent.bootstrap.logger import AgentLogger

        logger = AgentLogger()

        assert logger.log_dir.exists()
        assert logger.log_file is None
        assert logger.log_index == 0

    def test_logger_start_new_run(self):
        """Test that logger creates new log file"""
        from easy_agent.bootstrap.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        assert logger.log_file is not None
        assert logger.log_file.exists()
        assert logger.log_index == 0

    def test_logger_log_request(self):
        """Test logging requests"""
        from easy_agent.bootstrap.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        messages = [{"role": "user", "content": "test"}]
        logger.log_request(messages=messages)

        assert logger.log_index == 1

    def test_logger_log_response(self):
        """Test logging responses"""
        from easy_agent.bootstrap.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        logger.log_response(content="test response", finish_reason="stop")

        assert logger.log_index == 1


def test_project_structure():
    """Test that project structure is correct"""
    base_path = Path(__file__).resolve().parents[2]

    # Core modules
    assert (base_path / "easy_agent" / "__init__.py").exists()
    assert (base_path / "easy_agent" / "bootstrap" / "config.py").exists()
    assert (base_path / "easy_agent" / "agent" / "core.py").exists()
    assert (base_path / "easy_agent" / "agent" / "model.py").exists()
    assert (base_path / "easy_agent" / "bootstrap" / "logger.py").exists()
    assert (base_path / "easy_agent" / "agent" / "skills.py").exists()

    # Config（示例配置与提示词目录）
    assert (base_path / "easy_agent" / "config" / "config-example.yaml").exists()
    assert (base_path / "easy_agent" / "config" / "prompts" / "system.md").exists()

    # 各功能包结构
    assert (base_path / "easy_agent" / "api" / "__init__.py").exists()
    assert (base_path / "easy_agent" / "api" / "chat.py").exists()
    assert (base_path / "easy_agent" / "services" / "prompt_loader.py").exists()
    assert (base_path / "easy_agent" / "bootstrap" / "initialization" / "runtime.py").exists()
    assert (base_path / "easy_agent" / "db" / "database.py").exists()

    assert (base_path / "frontend-vue2" / "package.json").exists()
    assert (base_path / "pyproject.toml").exists()


class TestImports:
    """Test that all public imports work correctly"""

    def test_top_level_package_only_holds_the_version(self):
        """导入任何 easy_agent.* 都会先执行根包，所以它不转出符号，类和函数从定义它们的模块导入。"""
        import easy_agent
        from easy_agent.agent import EasyAgent
        from easy_agent.agent.model import create_model
        from easy_agent.agent.skills import discover_skills
        from easy_agent.bootstrap.config import Config

        assert easy_agent.__version__
        assert not hasattr(easy_agent, "EasyAgent")
        assert EasyAgent is not None
        assert Config is not None
        assert create_model is not None
        assert discover_skills is not None

    def test_db_package_imports(self):
        from easy_agent.db import Database
        from easy_agent.models.db import SessionModel, UserModel
        assert Database is not None
        assert SessionModel is not None
        assert UserModel is not None

    def test_service_imports(self):
        from easy_agent.services.agent_manager import init_agent_config
        from easy_agent.services.streaming.orchestrator import chat_stream_generator

        assert chat_stream_generator is not None
        assert init_agent_config is not None

    def test_skills_module(self):
        from easy_agent.agent.skills import discover_skills
        assert callable(discover_skills)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
