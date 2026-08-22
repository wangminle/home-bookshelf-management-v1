from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# BUG-204：members.username 的大小写不敏感唯一索引是 lower(username) 表达式索引。
# SQLAlchemy 的 SQLite 方言不支持反射表达式索引（Skipped unsupported reflection），
# autogen/check 会因"看不见数据库侧索引"而持续报差异——这里按名称显式豁免比较，
# 索引本身由迁移 g7c4d5e6f8b0 创建、由模型声明，真实约束在数据库层生效。
_EXPRESSION_INDEXES_EXCLUDED_FROM_COMPARE = {"ix_members_username"}


def _include_object(object_, name, type_, reflected, compare_to):
    if type_ == "index" and name in _EXPRESSION_INDEXES_EXCLUDED_FROM_COMPARE:
        return False
    return True



def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
