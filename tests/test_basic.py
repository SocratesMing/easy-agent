"""Basic tests for Easy Agent"""
import pytest
from pathlib import Path
import tempfile


class TestConfig:
    """Test configuration module"""

    def test_config_file_not_found(self):
        """Test that Config.load raises FileNotFoundError when config doesn't exist"""
        from wukong_agent.config import Config

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
        from wukong_agent.config import Config

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
        from wukong_agent.logger import AgentLogger

        logger = AgentLogger()

        assert logger.log_dir.exists()
        assert logger.log_file is None
        assert logger.log_index == 0

    def test_logger_start_new_run(self):
        """Test that logger creates new log file"""
        from wukong_agent.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        assert logger.log_file is not None
        assert logger.log_file.exists()
        assert logger.log_index == 0

    def test_logger_log_request(self):
        """Test logging requests"""
        from wukong_agent.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        messages = [{"role": "user", "content": "test"}]
        logger.log_request(messages=messages)

        assert logger.log_index == 1

    def test_logger_log_response(self):
        """Test logging responses"""
        from wukong_agent.logger import AgentLogger

        logger = AgentLogger()
        logger.start_new_run()

        logger.log_response(content="test response", finish_reason="stop")

        assert logger.log_index == 1


class TestAgent:
    """Test agent module"""

    def test_colors_defined(self):
        """Test that Colors class has expected attributes"""
        from wukong_agent.agent import Colors

        assert hasattr(Colors, 'RESET')
        assert hasattr(Colors, 'BOLD')
        assert hasattr(Colors, 'GREEN')


def test_project_structure():
    """Test that project structure is correct"""
    base_path = Path(__file__).parent.parent

    assert (base_path / "wukong_agent" / "__init__.py").exists()
    assert (base_path / "wukong_agent" / "config.py").exists()
    assert (base_path / "wukong_agent" / "cli.py").exists()
    assert (base_path / "wukong_agent" / "agent.py").exists()
    assert (base_path / "wukong_agent" / "logger.py").exists()
    assert (base_path / "wukong_agent" / "config" / "config.yaml.example").exists()
    assert (base_path / "wukong_agent" / "config" / "system_prompt.md").exists()
    assert (base_path / "pyproject.toml").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
