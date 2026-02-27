import time
import uuid
from decimal import Decimal
from typing import List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, BigInteger, Column, Numeric, String, or_

from open_webui.env import (
    REDIS_URL,
    REDIS_SENTINEL_HOSTS,
    REDIS_SENTINEL_PORT,
    REDIS_CLUSTER,
)
from open_webui.internal.db import Base, get_db
from open_webui.utils.redis import get_redis_connection, get_sentinels_from_env


####################
# User Credit DB Schema
####################


class Credit(Base):
    __tablename__ = "credit"

    id = Column(String, primary_key=True)
    user_id = Column(String, unique=True, nullable=False)
    credit = Column(Numeric(precision=24, scale=12))

    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)


class CreditLog(Base):
    __tablename__ = "credit_log"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    credit = Column(Numeric(precision=24, scale=12))
    detail = Column(JSON, nullable=True)

    created_at = Column(BigInteger, index=True)


class TradeTicket(Base):
    __tablename__ = "trade_ticket"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    amount = Column(Numeric(precision=24, scale=12))
    detail = Column(JSON, nullable=True)

    created_at = Column(BigInteger, index=True)


class RedemptionCode(Base):
    __tablename__ = "redemption_code"

    code = Column(String, primary_key=True)
    purpose = Column(String, index=True)
    user_id = Column(String, index=True, nullable=True)
    amount = Column(Numeric(precision=24, scale=12))

    created_at = Column(BigInteger, index=True)
    expired_at = Column(BigInteger, index=True, nullable=True)
    received_at = Column(BigInteger, index=True, nullable=True)


####################
# Forms
####################


class CreditModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str
    credit: Decimal = Field(default_factory=lambda: Decimal("0"))
    updated_at: int = Field(default_factory=lambda: int(time.time()))
    created_at: int = Field(default_factory=lambda: int(time.time()))


class CreditLogModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str
    credit: Decimal = Field(default_factory=lambda: Decimal("0"))
    detail: dict = Field(default_factory=lambda: {})
    created_at: int = Field(default_factory=lambda: int(time.time()))


class CreditLogUsage(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")
    total_price: Optional[Decimal] = None
    prompt_unit_price: Optional[Decimal] = None
    completion_unit_price: Optional[Decimal] = None
    request_unit_price: Optional[Decimal] = None
    feature_price: Optional[Decimal] = None
    completion_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class SimpleModelModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[str] = None
    name: Optional[str] = None


class CreditLogSimpleDetailAPIParams(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    model: SimpleModelModel = Field(default_factory=lambda: {})


class CreditLogSimpleDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    desc: str = Field(default_factory=lambda: "")
    api_params: CreditLogSimpleDetailAPIParams = Field(default_factory=lambda: {})
    usage: CreditLogUsage = Field(default_factory=lambda: {})


class CreditLogSimpleModel(CreditLogModel):
    model_config = ConfigDict(from_attributes=True)
    detail: CreditLogSimpleDetail
    username: Optional[str] = Field(default="")


class SetCreditFormDetail(BaseModel):
    api_path: str = Field(default="")
    api_params: dict = Field(default_factory=lambda: {})
    desc: str = Field(default="")
    usage: dict = Field(default_factory=lambda: {})


class AddCreditForm(BaseModel):
    user_id: str
    amount: Decimal
    detail: SetCreditFormDetail


class SetCreditForm(BaseModel):
    user_id: str
    credit: Decimal
    detail: SetCreditFormDetail


class TradeTicketModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str
    amount: Decimal = Field(default_factory=lambda: Decimal("0"))
    detail: dict = Field(default_factory=lambda: {})
    created_at: int = Field(default_factory=lambda: int(time.time()))


class RedemptionCodeModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")
    code: str
    purpose: str
    user_id: Optional[str] = None
    amount: Decimal
    created_at: int
    expired_at: Optional[int] = None
    received_at: Optional[int] = None


####################
# Tables
####################


class CreditsTable:
    def insert_new_credit(self, user_id: str) -> Optional[CreditModel]:
        from open_webui.config import CREDIT_DEFAULT_CREDIT

        try:
            credit_model = CreditModel(
                user_id=user_id, credit=Decimal(CREDIT_DEFAULT_CREDIT.value)
            )
            with get_db() as db:
                result = Credit(**credit_model.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if credit_model:
                    return credit_model
                return None
        except Exception:
            return None

    def init_credit_by_user_id(self, user_id: str) -> CreditModel:
        credit_model = self.get_credit_by_user_id(
            user_id=user_id
        ) or self.insert_new_credit(user_id=user_id)
        if credit_model is not None:
            return credit_model
        raise HTTPException(status_code=500, detail="credit initialize failed")

    def get_credit_by_user_id(self, user_id: str) -> Optional[CreditModel]:
        try:
            with get_db() as db:
                credit = db.query(Credit).filter(Credit.user_id == user_id).first()
                return CreditModel.model_validate(credit)
        except Exception:
            return None

    def list_credits_by_user_id(self, user_ids: List[str]) -> List[CreditModel]:
        try:
            with get_db() as db:
                credits = db.query(Credit).filter(Credit.user_id.in_(user_ids)).all()
                return [CreditModel.model_validate(credit) for credit in credits]
        except Exception:
            return []

    def set_credit_by_user_id(self, form_data: SetCreditForm) -> CreditModel:
        credit_model = self.init_credit_by_user_id(user_id=form_data.user_id)
        log = CreditLogModel(
            user_id=form_data.user_id,
            credit=form_data.credit,
            detail=form_data.detail.model_dump(),
        )
        with get_db() as db:
            db.add(CreditLog(**log.model_dump()))
            db.query(Credit).filter(Credit.user_id == credit_model.user_id).update(
                {"credit": form_data.credit, "updated_at": int(time.time())},
                synchronize_session=False,
            )
            db.commit()
        return self.get_credit_by_user_id(user_id=form_data.user_id)

    def add_credit_by_user_id(self, form_data: AddCreditForm) -> Optional[CreditModel]:
        credit_model = self.init_credit_by_user_id(user_id=form_data.user_id)
        log = CreditLogModel(
            user_id=form_data.user_id,
            credit=credit_model.credit + form_data.amount,
            detail=form_data.detail.model_dump(),
        )
        with get_db() as db:
            db.add(CreditLog(**log.model_dump()))
            db.query(Credit).filter(Credit.user_id == form_data.user_id).update(
                {
                    "credit": Credit.credit + form_data.amount,
                    "updated_at": int(time.time()),
                },
                synchronize_session=False,
            )
            db.commit()
        return self.get_credit_by_user_id(form_data.user_id)


Credits = CreditsTable()


class TradeTicketTable:
    def insert_new_ticket(
        self, id: str, user_id: str, amount: float, detail: dict
    ) -> TradeTicketModel:
        try:
            ticket = TradeTicketModel(
                id=id,
                user_id=user_id,
                amount=Decimal(amount),
                detail=detail,
            )
            with get_db() as db:
                db.add(TradeTicket(**ticket.model_dump()))
                db.commit()
            return ticket
        except Exception as err:
            raise HTTPException(status_code=500, detail=str(err))

    def get_ticket_by_id(self, id: str) -> Optional[TradeTicketModel]:
        try:
            with get_db() as db:
                ticket = db.query(TradeTicket).filter(TradeTicket.id == id).first()
                return TradeTicketModel.model_validate(ticket)
        except Exception:
            return None

    def get_ticket_by_time(
        self, start_time: int, end_time: int, user_ids: Optional[List[str]] = None
    ) -> list[TradeTicketModel]:
        try:
            with get_db() as db:
                logs = (
                    db.query(TradeTicket)
                    .filter(TradeTicket.created_at >= start_time)
                    .filter(TradeTicket.created_at < end_time)
                )
                if user_ids:
                    logs = logs.filter(TradeTicket.user_id.in_(user_ids))
                logs = logs.order_by(TradeTicket.created_at.asc())
                return [TradeTicketModel.model_validate(log) for log in logs]
        except Exception:
            return []

    def update_credit_by_id(self, id: str, detail: dict) -> None:
        from open_webui.config import CREDIT_EXCHANGE_RATIO

        try:
            with get_db() as db:
                db.query(TradeTicket).filter(TradeTicket.id == id).update(
                    {"detail": detail}
                )
                db.commit()
                ticket = self.get_ticket_by_id(id)
                Credits.add_credit_by_user_id(
                    AddCreditForm(
                        user_id=ticket.user_id,
                        amount=ticket.amount * Decimal(CREDIT_EXCHANGE_RATIO.value),
                        detail=SetCreditFormDetail(desc="payment success"),
                    )
                )
                return None
        except Exception:
            return None


TradeTickets = TradeTicketTable()


class CreditLogTable:
    def count_credit_log(self, user_ids: list[str] = None) -> int:
        with get_db() as db:
            query = db.query(CreditLog).order_by(CreditLog.created_at.desc())
            if user_ids:
                query = query.filter(CreditLog.user_id.in_(user_ids))
            return query.count()

    def get_credit_log_by_page(
        self,
        user_ids: list[str] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[CreditLogSimpleModel]:
        with get_db() as db:
            query = db.query(CreditLog).order_by(CreditLog.created_at.desc())
            if user_ids:
                query = query.filter(CreditLog.user_id.in_(user_ids))
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            all_logs = query.all()
            return [CreditLogSimpleModel.model_validate(log) for log in all_logs]

    def get_log_by_time(
        self, start_time: int, end_time: int, user_ids: Optional[List[str]] = None
    ) -> list[CreditLogSimpleModel]:
        try:
            with get_db() as db:
                logs = (
                    db.query(CreditLog)
                    .filter(CreditLog.created_at >= start_time)
                    .filter(CreditLog.created_at < end_time)
                )
                if user_ids:
                    logs = logs.filter(CreditLog.user_id.in_(user_ids))
                logs = logs.order_by(CreditLog.created_at.asc())
                return [CreditLogSimpleModel.model_validate(log) for log in logs]
        except Exception:
            return []

    def delete_log_by_timestamp(self, timestamp: int) -> int:
        try:
            with get_db() as db:
                query = db.query(CreditLog).filter(CreditLog.created_at < timestamp)
                total = query.count()
                query.delete()
                db.commit()
                return total
        except Exception as err:
            raise HTTPException(status_code=500, detail=str(err))


CreditLogs = CreditLogTable()


class RedemptionCodeTable:
    def get_code(self, code: str) -> Optional[RedemptionCodeModel]:
        try:
            with get_db() as db:
                redemption_code = (
                    db.query(RedemptionCode).filter(RedemptionCode.code == code).first()
                )
                return (
                    RedemptionCodeModel.model_validate(redemption_code)
                    if redemption_code
                    else None
                )
        except Exception:
            return None

    def get_codes(
        self, keyword: str = None, offset: int = None, limit: int = None
    ) -> Tuple[int, List[RedemptionCodeModel]]:
        with get_db() as db:
            query = db.query(RedemptionCode).order_by(RedemptionCode.created_at.desc())
            if keyword:
                query = query.filter(
                    or_(
                        RedemptionCode.code == keyword,
                        RedemptionCode.purpose == keyword,
                    )
                )
            total = query.count()
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            return total, [
                RedemptionCodeModel.model_validate(code) for code in query.all()
            ]

    def insert_codes(self, redemption_codes: List[RedemptionCodeModel]) -> None:
        try:
            with get_db() as db:
                db.add_all(
                    [RedemptionCode(**code.model_dump()) for code in redemption_codes]
                )
                db.commit()
            return None
        except Exception as err:
            raise HTTPException(status_code=500, detail=str(err))

    def update_code(self, code: RedemptionCodeModel) -> None:
        try:
            with get_db() as db:
                db.query(RedemptionCode).filter(
                    RedemptionCode.code == code.code
                ).update(
                    {
                        "purpose": code.purpose,
                        "amount": code.amount,
                        "expired_at": code.expired_at,
                    }
                )
                db.commit()
            return None
        except Exception as err:
            raise HTTPException(status_code=500, detail=str(err))

    def delete_code(self, code: str) -> None:
        try:
            with get_db() as db:
                db.query(RedemptionCode).filter(RedemptionCode.code == code).delete()
                db.commit()
            return None
        except Exception as err:
            raise HTTPException(status_code=500, detail=str(err))

    def receive_code(self, code: str, user_id: str) -> None:
        # receive
        try:
            # load code
            redemption_code = self.get_code(code)
            if redemption_code is None:
                raise HTTPException(status_code=404, detail="Code not found")
            # check if code is received
            if redemption_code.user_id is not None:
                raise HTTPException(status_code=400, detail="Code already received")
            # check expired
            now = int(time.time())
            if (
                redemption_code.expired_at is not None
                and redemption_code.expired_at < now
            ):
                raise HTTPException(status_code=400, detail="Code expired")
            # concurrency control
            cache_key = f"redemption_code:{code}"
            redis = get_redis_connection(
                redis_url=REDIS_URL,
                redis_sentinels=get_sentinels_from_env(
                    REDIS_SENTINEL_HOSTS, REDIS_SENTINEL_PORT
                ),
                redis_cluster=REDIS_CLUSTER,
            )
            if not redis.set(cache_key, cache_key, nx=True, ex=60):
                raise HTTPException(status_code=400, detail="Too many requests")
            # receive
            with get_db() as db:
                db.query(RedemptionCode).filter(RedemptionCode.code == code).update(
                    {
                        "user_id": user_id,
                        "received_at": int(time.time()),
                    }
                )
                db.commit()
            Credits.add_credit_by_user_id(
                AddCreditForm(
                    user_id=user_id,
                    amount=redemption_code.amount,
                    detail=SetCreditFormDetail(
                        desc="redemption code received", api_params={"code": code}
                    ),
                )
            )
            return
        except HTTPException as err:
            raise err
        except Exception as err:
            raise HTTPException(status_code=500, detail=str(err))


RedemptionCodes = RedemptionCodeTable()
