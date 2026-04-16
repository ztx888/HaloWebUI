import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, JSON, Text

from open_webui.internal.db import Base, JSONField, get_db
from open_webui.models.users import UserResponse, Users
from open_webui.env import SRC_LOG_LEVELS


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


class SharedToolServer(Base):
    __tablename__ = "shared_tool_server"

    id = Column(Text, primary_key=True)
    owner_user_id = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)

    connection_payload = Column(JSONField, nullable=False)
    display_metadata = Column(JSONField, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    access_control = Column(JSON, nullable=True)

    updated_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class SharedToolServerModel(BaseModel):
    id: str
    owner_user_id: str
    kind: str

    connection_payload: dict
    display_metadata: dict
    enabled: bool
    access_control: Optional[dict] = None

    updated_at: int
    created_at: int

    model_config = ConfigDict(from_attributes=True)


class SharedToolServerUserResponse(SharedToolServerModel):
    owner: Optional[UserResponse] = None


class SharedToolServerTable:
    def insert_new_shared_tool_server(
        self,
        owner_user_id: str,
        *,
        kind: str,
        connection_payload: dict,
        display_metadata: dict,
        access_control: Optional[dict],
        enabled: bool = True,
    ) -> Optional[SharedToolServerModel]:
        now = int(time.time())
        shared_tool_server = SharedToolServerModel(
            id=str(uuid.uuid4()),
            owner_user_id=owner_user_id,
            kind=kind,
            connection_payload=connection_payload,
            display_metadata=display_metadata,
            enabled=enabled,
            access_control=access_control,
            updated_at=now,
            created_at=now,
        )

        try:
            with get_db() as db:
                result = SharedToolServer(**shared_tool_server.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                return SharedToolServerModel.model_validate(result)
        except Exception as exc:
            log.exception("Error creating shared tool server: %s", exc)
            return None

    def get_shared_tool_server_by_id(self, id: str) -> Optional[SharedToolServerModel]:
        try:
            with get_db() as db:
                result = db.get(SharedToolServer, id)
                return SharedToolServerModel.model_validate(result) if result else None
        except Exception:
            return None

    def get_shared_tool_servers_by_ids(
        self, ids: list[str]
    ) -> list[SharedToolServerModel]:
        if not ids:
            return []

        try:
            with get_db() as db:
                results = (
                    db.query(SharedToolServer)
                    .filter(SharedToolServer.id.in_(ids))
                    .order_by(SharedToolServer.updated_at.desc())
                    .all()
                )
                return [
                    SharedToolServerModel.model_validate(result) for result in results
                ]
        except Exception as exc:
            log.exception("Error loading shared tool servers by ids: %s", exc)
            return []

    def get_shared_tool_servers(self) -> list[SharedToolServerUserResponse]:
        try:
            with get_db() as db:
                results = (
                    db.query(SharedToolServer)
                    .order_by(SharedToolServer.updated_at.desc())
                    .all()
                )
                owner_ids = list({result.owner_user_id for result in results})
                owners_map = Users.get_users_map_by_ids(owner_ids) if owner_ids else {}

                responses = []
                for result in results:
                    owner = owners_map.get(result.owner_user_id)
                    responses.append(
                        SharedToolServerUserResponse.model_validate(
                            {
                                **SharedToolServerModel.model_validate(result).model_dump(),
                                "owner": owner.model_dump() if owner else None,
                            }
                        )
                    )
                return responses
        except Exception as exc:
            log.exception("Error loading shared tool servers: %s", exc)
            return []

    def get_shared_tool_servers_by_owner_user_id(
        self, owner_user_id: str
    ) -> list[SharedToolServerModel]:
        try:
            with get_db() as db:
                results = (
                    db.query(SharedToolServer)
                    .filter_by(owner_user_id=owner_user_id)
                    .order_by(SharedToolServer.updated_at.desc())
                    .all()
                )
                return [
                    SharedToolServerModel.model_validate(result) for result in results
                ]
        except Exception:
            return []

    def update_shared_tool_server_by_id(
        self, id: str, patch: dict
    ) -> Optional[SharedToolServerModel]:
        try:
            with get_db() as db:
                payload = {**patch, "updated_at": int(time.time())}
                db.query(SharedToolServer).filter_by(id=id).update(payload)
                db.commit()
                result = db.get(SharedToolServer, id)
                return SharedToolServerModel.model_validate(result) if result else None
        except Exception as exc:
            log.exception("Error updating shared tool server %s: %s", id, exc)
            return None

    def delete_shared_tool_server_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(SharedToolServer).filter_by(id=id).delete()
                db.commit()
                return True
        except Exception:
            return False

    def delete_shared_tool_servers_by_ids(self, ids: list[str]) -> bool:
        if not ids:
            return True

        try:
            with get_db() as db:
                db.query(SharedToolServer).filter(SharedToolServer.id.in_(ids)).delete(
                    synchronize_session=False
                )
                db.commit()
                return True
        except Exception as exc:
            log.exception("Error deleting shared tool servers by ids: %s", exc)
            return False


SharedToolServers = SharedToolServerTable()
