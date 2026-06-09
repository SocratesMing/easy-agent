"""Basic tests for Easy Agent"""
import pytest
from pathlib import Path
import tempfile


class TestConfig:
    """Test configuration module"""

    def test_config_file_not_found(self):
        """Test that Config.load raises FileNotFoundError when config doesn't exist"""
        from easy_agent.config import Config

        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with pytest.raises(FileNotFoundError):
                    Config.load()
            finally:
                os.chdir(original_cwd)

    def test_config_from_yaml_missing_fields(self):
        """Test that from_yaml raises ValueError for missing required fields"""
        from easy_agent.config import Config

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
        from easy_agent.logger import AgentLogger

        logger = AgentLogger()

        assert logger.log_dir.exists()
        assert logger.log_file is None
        assert logger.log_index == 0

    def test_logger_start_new_run(self):
        """Test that logger creates new log file"""
        from easy_agent.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        assert logger.log_file is not None
        assert logger.log_file.exists()
        assert logger.log_index == 0

    def test_logger_log_request(self):
        """Test logging requests"""
        from easy_agent.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        messages = [{"role": "user", "content": "test"}]
        logger.log_request(messages=messages)

        assert logger.log_index == 1

    def test_logger_log_response(self):
        """Test logging responses"""
        from easy_agent.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        logger.log_response(content="test response", finish_reason="stop")

        assert logger.log_index == 1


class TestAgent:
    """Test agent module"""

    def test_colors_defined(self):
        """Test that Colors class has expected attributes"""
        from easy_agent.agent import Colors

        assert hasattr(Colors, 'RESET')
        assert hasattr(Colors, 'BOLD')
        assert hasattr(Colors, 'GREEN')


def test_project_structure():
    """Test that project structure is correct"""
    base_path = Path(__file__).parent.parent

    # Core modules
    assert (base_path / "easy_agent" / "__init__.py").exists()
    assert (base_path / "easy_agent" / "config.py").exists()
    assert (base_path / "easy_agent" / "agent.py").exists()
    assert (base_path / "easy_agent" / "model.py").exists()
    assert (base_path / "easy_agent" / "logger.py").exists()
    assert (base_path / "easy_agent" / "skills.py").exists()

    # Config
    assert (base_path / "easy_agent" / "config" / "config.yaml.example").exists()
    assert (base_path / "easy_agent" / "config" / "system_prompt.md").exists()

    # Web package
    assert (base_path / "easy_agent" / "web" / "__init__.py").exists()
    assert (base_path / "easy_agent" / "web" / "server.py").exists()
    assert (base_path / "easy_agent" / "web" / "db" / "__init__.py").exists()
    assert (base_path / "easy_agent" / "web" / "db" / "database.py").exists()
    assert (base_path / "easy_agent" / "web" / "db" / "models.py").exists()
    assert (base_path / "easy_agent" / "web" / "service" / "__init__.py").exists()
    assert (base_path / "easy_agent" / "web" / "service" / "streaming.py").exists()
    assert (base_path / "easy_agent" / "web" / "service" / "agent_manager.py").exists()

    assert (base_path / "pyproject.toml").exists()


class TestImports:
    """Test that all public imports work correctly"""

    def test_top_level_imports(self):
        from easy_agent import EasyAgent, Config, create_model, discover_skills
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
        from easy_agent.services import (
            chat_stream_generator,
            init_agent_config,
        )
        assert chat_stream_generator is not None
        assert init_agent_config is not None

    def test_skills_module(self):
        from easy_agent.skills import discover_skills
        assert callable(discover_skills)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
