# flake8: noqa
# Operator command groups — always available, wheel and checkout alike.
# Developer groups (dev, test, agent, backend, reference) are imported by
# main.py only when running from a source checkout: they drive the monorepo
# itself and pull in the `dev` extra's dependencies (alembic, sqlalchemy,
# psycopg2, mascope_reference).
from .cert import cert_app
from .demo import demo_app
from .env import env_app
from .logs import logs_app
from .prod import prod_app
