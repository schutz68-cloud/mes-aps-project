import sys
from pathlib import Path

from sqlalchemy import text


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import engine  # noqa: E402


CHECKS = [
    (
        "counts",
        """
        SELECT 'orders' AS name, COUNT(*) AS value FROM orders
        UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
        UNION ALL SELECT 'order_operations', COUNT(*) FROM order_operations
        UNION ALL SELECT 'plan_operations', COUNT(*) FROM plan_operations
        UNION ALL SELECT 'plan_change_log', COUNT(*) FROM plan_change_log
        ORDER BY name
        """,
    ),
    (
        "route_buffer_violations",
        """
        WITH ordered AS (
            SELECT
                po.plan_version_id,
                oi.id AS order_item_id,
                oo.id AS operation_id,
                oo.sequence_no,
                po.start_time,
                po.end_time,
                LAG(po.end_time) OVER (
                    PARTITION BY po.plan_version_id, oi.id
                    ORDER BY oo.sequence_no, oo.id
                ) AS previous_end_time
            FROM plan_operations po
            JOIN order_operations oo ON oo.id = po.operation_id
            JOIN order_items oi ON oi.id = oo.order_item_id
        )
        SELECT COUNT(*) AS value
        FROM ordered
        WHERE previous_end_time IS NOT NULL
          AND start_time < previous_end_time + 30
        """,
    ),
    (
        "duration_violations",
        """
        SELECT COUNT(*) AS value
        FROM plan_operations po
        JOIN order_operations oo ON oo.id = po.operation_id
        JOIN order_items oi ON oi.id = oo.order_item_id
        JOIN machine_product_rates mpr
          ON mpr.product_id = oi.product_id
         AND mpr.machine_id = po.machine_id
         AND mpr.operation_type = oo.operation_type
        WHERE CEIL(oo.quantity::numeric / mpr.units_per_minute)
              + mpr.setup_minutes <> po.end_time - po.start_time
        """,
    ),
    (
        "machine_buffer_violations",
        """
        WITH ordered AS (
            SELECT
                po.plan_version_id,
                po.machine_id,
                po.operation_id,
                po.start_time,
                po.end_time,
                mpr.setup_minutes,
                oo.order_item_id,
                oo.sequence_no,
                MIN(oo.sequence_no) OVER (
                    PARTITION BY po.plan_version_id, oo.order_item_id
                ) AS first_sequence_no,
                LAG(po.start_time) OVER (
                    PARTITION BY po.plan_version_id, po.machine_id
                    ORDER BY po.start_time, po.end_time, po.operation_id
                ) AS previous_start_time,
                LAG(po.end_time) OVER (
                    PARTITION BY po.plan_version_id, po.machine_id
                    ORDER BY po.start_time, po.end_time, po.operation_id
                ) AS previous_end_time,
                LAG(mpr.setup_minutes) OVER (
                    PARTITION BY po.plan_version_id, po.machine_id
                    ORDER BY po.start_time, po.end_time, po.operation_id
                ) AS previous_setup_minutes
            FROM plan_operations po
            JOIN order_operations oo ON oo.id = po.operation_id
            JOIN order_items oi ON oi.id = oo.order_item_id
            JOIN machine_product_rates mpr
              ON mpr.product_id = oi.product_id
             AND mpr.machine_id = po.machine_id
             AND mpr.operation_type = oo.operation_type
        )
        SELECT COUNT(*) AS value
        FROM ordered
        WHERE previous_start_time IS NOT NULL
          AND (
              (
                  sequence_no = first_sequence_no
                  AND start_time < previous_start_time + previous_setup_minutes + 15
              )
              OR
              (
                  sequence_no <> first_sequence_no
                  AND start_time < previous_end_time + 15
              )
          )
        """,
    ),
    (
        "coiling_setup_resource_violations",
        """
        WITH coiling AS (
            SELECT
                po.operation_id,
                po.start_time,
                mpr.setup_minutes,
                LAG(po.start_time) OVER (
                    ORDER BY po.start_time, po.operation_id
                ) AS previous_start_time,
                LAG(mpr.setup_minutes) OVER (
                    ORDER BY po.start_time, po.operation_id
                ) AS previous_setup_minutes
            FROM plan_operations po
            JOIN order_operations oo ON oo.id = po.operation_id
            JOIN order_items oi ON oi.id = oo.order_item_id
            JOIN machine_product_rates mpr
              ON mpr.product_id = oi.product_id
             AND mpr.machine_id = po.machine_id
             AND mpr.operation_type = oo.operation_type
            WHERE po.plan_version_id = 1
              AND oo.operation_type = 'COILING'
        )
        SELECT COUNT(*) AS value
        FROM coiling
        WHERE previous_start_time IS NOT NULL
          AND start_time < previous_start_time + previous_setup_minutes + 15
        """,
    ),
]


def main():
    with engine.connect() as connection:
        for name, sql in CHECKS:
            print(f"-- {name}")
            rows = connection.execute(text(sql)).mappings().all()
            for row in rows:
                print(dict(row))


if __name__ == "__main__":
    main()
