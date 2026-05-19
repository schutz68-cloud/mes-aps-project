from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class PlanOperation(Base):
    __tablename__ = "plan_operations"

    id = Column(Integer, primary_key=True)
    plan_version_id = Column(Integer)
    operation_id = Column(Integer)
    machine_id = Column(String)
    start_time = Column(Integer)
    end_time = Column(Integer)
    setup_minutes = Column(Integer, default=0)
    is_locked = Column(Boolean, default=False)
    lock_reason = Column(String)


class PlanChangeLog(Base):
    __tablename__ = "plan_change_log"

    id = Column(Integer, primary_key=True)
    change_set_id = Column(String)
    plan_version_id = Column(Integer)
    operation_id = Column(Integer)

    old_machine_id = Column(String)
    new_machine_id = Column(String)

    old_start_time = Column(Integer)
    old_end_time = Column(Integer)

    new_start_time = Column(Integer)
    new_end_time = Column(Integer)

    change_reason = Column(String)
    created_at = Column(DateTime, server_default=func.now())

    is_rolled_back = Column(Boolean, default=False)
    rollback_at = Column(DateTime)
    rollback_reason = Column(String)


class PlanVersion(Base):
    __tablename__ = "plan_versions"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    status = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String)
    approved_at = Column(DateTime)
    approved_by = Column(String)
    description = Column(String)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(String)
    description = Column(String)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class RoutingOperationMachineGroup(Base):
    __tablename__ = "routing_operation_machine_groups"

    id = Column(Integer, primary_key=True)
    routing_operation_id = Column(Integer)
    machine_group_id = Column(String)


class ShiftTemplate(Base):
    __tablename__ = "shift_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    start_minute_of_day = Column(Integer)
    end_minute_of_day = Column(Integer)
    prep_minutes = Column(Integer, default=0)
    finish_minutes = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)


class ShiftTemplateBreak(Base):
    __tablename__ = "shift_template_breaks"

    id = Column(Integer, primary_key=True)
    shift_template_id = Column(Integer)
    name = Column(String)
    start_minute_of_shift = Column(Integer)
    end_minute_of_shift = Column(Integer)


class SetupTeam(Base):
    __tablename__ = "setup_teams"

    id = Column(String, primary_key=True)
    name = Column(String)
    capacity = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)


class MachineGroupSetupTeam(Base):
    __tablename__ = "machine_group_setup_teams"

    id = Column(Integer, primary_key=True)
    machine_group_id = Column(String)
    setup_team_id = Column(String)


class Workshop(Base):
    __tablename__ = "workshops"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    responsible_name = Column(String)
    sort_order = Column(Integer, default=999)


class WorkshopMachineGroup(Base):
    __tablename__ = "workshop_machine_groups"

    id = Column(Integer, primary_key=True)
    workshop_id = Column(Integer, nullable=False)
    machine_group_id = Column(String, nullable=False)


class MesScheduleRun(Base):
    __tablename__ = "mes_schedule_runs"

    id = Column(Integer, primary_key=True)
    source_plan_version_id = Column(Integer)
    start_minute = Column(Integer)
    end_minute = Column(Integer)
    status = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String)
    released_at = Column(DateTime)
    released_by = Column(String)
    cancelled_at = Column(DateTime)
    cancelled_by = Column(String)
    description = Column(String)
    is_hidden = Column(Boolean, default=False)


class MesScheduleOperation(Base):
    __tablename__ = "mes_schedule_operations"

    id = Column(Integer, primary_key=True)
    schedule_run_id = Column(Integer)
    source_plan_operation_id = Column(Integer)
    operation_id = Column(Integer)
    order_id = Column(Integer)
    order_item_id = Column(Integer)
    product_id = Column(String)
    product_name = Column(String)
    order_no = Column(String)
    machine_id = Column(String)
    machine_name = Column(String)
    machine_group_id = Column(String)
    operation_type = Column(String)
    operation_name = Column(String)
    quantity = Column(Integer)
    setup_minutes = Column(Integer)
    planned_start_time = Column(Integer)
    planned_end_time = Column(Integer)
    status = Column(String)
    actual_start_at = Column(DateTime)
    actual_end_at = Column(DateTime)
    good_quantity = Column(Integer, default=0)
    defect_quantity = Column(Integer, default=0)
    actual_comment = Column(String)


class MesOperationReport(Base):
    __tablename__ = "mes_operation_reports"

    id = Column(Integer, primary_key=True)
    mes_schedule_operation_id = Column(Integer)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    good_quantity = Column(Integer)
    defect_quantity = Column(Integer)
    comment = Column(String)
    reported_by = Column(String)
    report_type = Column(String, default="production")
    corrected_report_id = Column(Integer)
    correction_reason = Column(String)
    created_at = Column(DateTime, server_default=func.now())
