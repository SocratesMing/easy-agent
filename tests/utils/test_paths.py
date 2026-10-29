"""项目根常量测试：后端按项目根解析的路径都基于同一个 ``PROJECT_ROOT``。"""

from pathlib import Path

from easy_agent.utils.paths import PROJECT_ROOT


def test_project_root_is_the_repository_root():
    # 以本测试文件的位置为准，不能照抄被测模块的算法：checkpoint 路径的旧 bug 就是
    # 文件挪了目录、层数没跟着改，照同样算法写的断言没能发现
    assert PROJECT_ROOT == Path(__file__).resolve().parents[2]
    assert (PROJECT_ROOT / "pyproject.toml").is_file()


def test_consumers_resolve_paths_under_the_project_root():
    import easy_agent.app as app_module
    from easy_agent.agent.tool_errors import sanitize_detail
    from easy_agent.persistence.checkpoints import resolve_sqlite_path
    from easy_agent.utils import env_loader

    assert env_loader.PROJECT_ROOT == PROJECT_ROOT
    assert resolve_sqlite_path("data/x.db") == PROJECT_ROOT / "data" / "x.db"
    assert app_module.frontend_dist == str(PROJECT_ROOT / "frontend-vue2" / "dist")
    assert sanitize_detail(f"{PROJECT_ROOT}/easy_agent/app.py 出错") == "<app>/easy_agent/app.py 出错"
