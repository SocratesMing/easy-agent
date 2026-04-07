"""Configuration management module

Provides unified configuration loading and management functionality
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """Retry configuration"""

    enabled: bool = True
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0


class LLMConfig(BaseModel):
    """LLM configuration"""

    api_key: str
    api_base: str | None = None
    model: str = "claude-sonnet-4-6"
    provider: str = "anthropic"
    retry: RetryConfig = Field(default_factory=RetryConfig)


class ToolsConfig(BaseModel):
    """Tools configuration"""

    enable_file_tools: bool = True
    enable_bash: bool = True
    enable_skills: bool = True
    skills_dir: str = "./skills"
    enable_mcp: bool = True
    mcp_config_path: str = "mcp.json"
    mcp_connect_timeout: float = 10.0
    mcp_execute_timeout: float = 60.0
    mcp_sse_read_timeout: float = 120.0


class SQLiteConfig(BaseModel):
    """SQLite configuration"""

    path: str = "./data/wukong_agent.db"


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
    database: str = "wukong_agent"
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
    collection_name: str = "wukong_agent_docs"
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
            raise FileNotFoundError("Configuration file not found. Place config.yaml in wukong_agent/config/ directory.")
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
            initial_delay=retry_data.get("initial_delay", 1.0),
            max_delay=retry_data.get("max_delay", 60.0),
            exponential_base=retry_data.get("exponential_base", 2.0),
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
        mcp_data = tools_data.get("mcp", {}) if isinstance(tools_data.get("mcp"), dict) else {}
        tools_config = ToolsConfig(
            enable_file_tools=tools_data.get("enable_file_tools", True),
            enable_bash=tools_data.get("enable_bash", True),
            enable_skills=tools_data.get("enable_skills", True),
            skills_dir=tools_data.get("skills_dir", "./skills"),
            enable_mcp=tools_data.get("enable_mcp", True),
            mcp_config_path=tools_data.get("mcp_config_path", "mcp.json"),
            mcp_connect_timeout=mcp_data.get("connect_timeout", 10.0),
            mcp_execute_timeout=mcp_data.get("execute_timeout", 60.0),
            mcp_sse_read_timeout=mcp_data.get("sse_read_timeout", 120.0),
        )

        db_data = data.get("database", {})
        sqlite_data = db_data.get("sqlite", {}) if isinstance(db_data.get("sqlite"), dict) else {}
        mysql_data = db_data.get("mysql", {}) if isinstance(db_data.get("mysql"), dict) else {}
        mysql_pool_data = mysql_data.get("pool", {}) if isinstance(mysql_data.get("pool"), dict) else {}
        db_config = DatabaseConfig(
            type=db_data.get("type", "sqlite"),
            sqlite=SQLiteConfig(path=sqlite_data.get("path", "./data/wukong_agent.db")),
            mysql=MySQLConfig(
                host=mysql_data.get("host", "127.0.0.1"),
                port=mysql_data.get("port", 3306),
                user=mysql_data.get("user", "root"),
                password=mysql_data.get("password", ""),
                database=mysql_data.get("database", "wukong_agent"),
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
            collection_name=vs_data.get("collection_name", "wukong_agent_docs"),
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
        dev_config = Path.cwd() / "wukong_agent" / "config" / filename
        if dev_config.exists():
            return dev_config

        user_config = Path.home() / ".wukong-agent" / "config" / filename
        if user_config.exists():
            return user_config

        package_config = cls.get_package_dir() / "config" / filename
        if package_config.exists():
            return package_config

        return None

    @classmethod
    def get_default_config_path(cls) -> Path:
        config_path = cls.find_config_file("config.yaml")
        if config_path:
            return config_path

        return cls.get_package_dir() / "config" / "config.yaml"
