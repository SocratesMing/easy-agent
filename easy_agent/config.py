"""Configuration management module

Provides unified configuration loading and management functionality
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    enabled: bool = True
    max_retries: int = 3


class LLMConfig(BaseModel):
    """LLM configuration"""

    api_key: str
    api_base: str | None = None
    model: str = "claude-sonnet-4-6"
    provider: str = "anthropic"
    retry: RetryConfig = Field(default_factory=RetryConfig)


class ToolsConfig(BaseModel):
    skills_dir: str = "./skills"
    prompts_dir: str = "./prompts"


class SQLiteConfig(BaseModel):
    """SQLite configuration"""

    path: str = "./data/easy_agent.db"


class MySQLPoolConfig(BaseModel):
    """MySQL connection pool configuration"""

    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 3600


class MySQLConfig(BaseModel):
    """MySQL configuration"""

    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "easy_agent"
    charset: str = "utf8mb4"
    pool: MySQLPoolConfig = Field(default_factory=MySQLPoolConfig)
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30


class DatabaseConfig(BaseModel):
    """Database configuration - supports SQLite and MySQL"""

    type: str = "sqlite"
    sqlite: SQLiteConfig = Field(default_factory=SQLiteConfig)
    mysql: MySQLConfig = Field(default_factory=MySQLConfig)


class VectorStoreConfig(BaseModel):
    """Vector store configuration (ChromaDB + Sentence Transformers)"""

    enabled: bool = False
    db_path: str = "./data/chroma_db"
    collection_name: str = "easy_agent_docs"
    embedding_provider: str = "sentence_transformers"
    embedding_dimension: int = 1024
    batch_size: int = 32
    zhipu_api_key: str = ""
    zhipu_model: str = "embedding-3"
    sentence_transformers_model: str = "Qwen/Qwen3-Embedding-0.6B"


class AgentConfig(BaseModel):
    """Agent configuration"""

    max_steps: int = 50
    workspace_dir: str = "./workspace"
    system_prompt_path: str = "system_prompt.md"


class Config(BaseModel):
    """Main configuration class"""

    llm: LLMConfig
    agent: AgentConfig
    tools: ToolsConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from the default search path."""
        config_path = cls.get_default_config_path()
        if not config_path.exists():
            raise FileNotFoundError("Configuration file not found. Place config.yaml in easy_agent/config/ directory.")
        return cls.from_yaml(config_path)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """Load configuration from YAML file"""
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file does not exist: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Configuration file is empty")

        if "api_key" not in data:
            raise ValueError("Configuration file missing required field: api_key")

        retry_data = data.get("retry", {})
        retry_config = RetryConfig(
            enabled=retry_data.get("enabled", True),
            max_retries=retry_data.get("max_retries", 3),
        )

        llm_config = LLMConfig(
            api_key=data["api_key"],
            api_base=data.get("api_base"),
            model=data.get("model", "claude-sonnet-4-6"),
            provider=data.get("provider", "anthropic"),
            retry=retry_config,
        )

        agent_config = AgentConfig(
            max_steps=data.get("max_steps", 50),
            workspace_dir=data.get("workspace_dir", "./workspace"),
            system_prompt_path=data.get("system_prompt_path", "system_prompt.md"),
        )

        tools_data = data.get("tools", {})
        tools_config = ToolsConfig(
            skills_dir=tools_data.get("skills_dir", "./skills"),
            prompts_dir=tools_data.get("prompts_dir", "./prompts"),
        )

        db_data = data.get("database", {})
        sqlite_data = db_data.get("sqlite", {}) if isinstance(db_data.get("sqlite"), dict) else {}
        mysql_data = db_data.get("mysql", {}) if isinstance(db_data.get("mysql"), dict) else {}
        mysql_pool_data = mysql_data.get("pool", {}) if isinstance(mysql_data.get("pool"), dict) else {}
        db_config = DatabaseConfig(
            type=db_data.get("type", "sqlite"),
            sqlite=SQLiteConfig(path=sqlite_data.get("path", "./data/easy_agent.db")),
            mysql=MySQLConfig(
                host=mysql_data.get("host", "127.0.0.1"),
                port=mysql_data.get("port", 3306),
                user=mysql_data.get("user", "root"),
                password=mysql_data.get("password", ""),
                database=mysql_data.get("database", "easy_agent"),
                charset=mysql_data.get("charset", "utf8mb4"),
                pool=MySQLPoolConfig(
                    pool_size=mysql_pool_data.get("pool_size", 5),
                    max_overflow=mysql_pool_data.get("max_overflow", 10),
                    pool_timeout=mysql_pool_data.get("pool_timeout", 30),
                    pool_recycle=mysql_pool_data.get("pool_recycle", 3600),
                ),
                connect_timeout=mysql_data.get("connect_timeout", 10),
                read_timeout=mysql_data.get("read_timeout", 30),
                write_timeout=mysql_data.get("write_timeout", 30),
            ),
        )

        vs_data = data.get("vector_store", {})
        vs_config = VectorStoreConfig(
            enabled=vs_data.get("enabled", False),
            db_path=vs_data.get("db_path", "./data/chroma_db"),
            collection_name=vs_data.get("collection_name", "easy_agent_docs"),
            embedding_provider=vs_data.get("embedding_provider", "sentence_transformers"),
            embedding_dimension=vs_data.get("embedding_dimension", 1024),
            batch_size=vs_data.get("batch_size", 32),
            zhipu_api_key=vs_data.get("zhipu_api_key", ""),
            zhipu_model=vs_data.get("zhipu_model", "embedding-3"),
            sentence_transformers_model=vs_data.get("sentence_transformers_model", "Qwen/Qwen3-Embedding-0.6B"),
        )

        return cls(
            llm=llm_config,
            agent=agent_config,
            tools=tools_config,
            database=db_config,
            vector_store=vs_config,
        )

    @staticmethod
    def get_package_dir() -> Path:
        return Path(__file__).parent

    @classmethod
    def find_config_file(cls, filename: str) -> Path | None:
        dev_config = Path.cwd() / "easy_agent" / "config" / filename
        if dev_config.exists():
            return dev_config

        user_config = Path.home() / ".easy-agent" / "config" / filename
        if user_config.exists():
            return user_config

        package_config = cls.get_package_dir() / "config" / filename
        if package_config.exists():
            return package_config

        return None

    @staticmethod
    def sanitize_username(username: str) -> str:
        """将用户名转换为安全的目录名"""
        return "".join(c for c in username if c.isalnum() or c in ('_', '-')) or "user"

    @staticmethod
    def get_user_workspace_dir(username: str) -> Path:
        """获取用户的工作空间目录路径

        Args:
            username: 用户名

        Returns:
            用户工作空间路径，如 workspace/{username}/
        """
        safe_name = Config.sanitize_username(username)
        return Path("./workspace") / safe_name

    @staticmethod
    def get_user_upload_dir(username: str) -> Path:
        """获取用户的上传文件目录路径

        Args:
            username: 用户名

        Returns:
            用户上传目录路径，如 data/uploads/{username}/
        """
        safe_name = Config.sanitize_username(username)
        return Path("./data/uploads") / safe_name

    @classmethod
    def get_default_config_path(cls) -> Path:
        config_path = cls.find_config_file("config.yaml")
        if config_path:
            return config_path

        return cls.get_package_dir() / "config" / "config.yaml"
