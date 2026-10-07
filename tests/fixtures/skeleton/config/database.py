"""Provide an offline database syntax fixture without executing Orionis imports."""

from orionis.environment import Env
from orionis.foundation.config.database import PGSQL, Connections, MySQL, Oracle, SQLite, SQLServer


def configuration():
    """
    Build the database configuration inspected by offline tests.

    Returns
    -------
    dict
        Default connection and representative connection entities.
    """
    return {
        "default": Env.get("DB_CONNECTION", "sqlite"),
        "connections": Connections(
            sqlite=SQLite(database=Env.get("DB_DATABASE", "database/database.sqlite")),
            mysql=MySQL(host=Env.get("DB_HOST", "127.0.0.1")),
            pgsql=PGSQL(port=Env.get("DB_PORT", 5432)),
            oracle=Oracle(
                username=Env.get("DB_USERNAME", "sys"),
                service_name=Env.get("DB_SERVICE_NAME", "ORCL"),
            ),
            sqlserver=SQLServer(password=Env.get("DB_PASSWORD", "")),
        ),
    }
