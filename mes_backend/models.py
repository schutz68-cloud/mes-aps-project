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
