import os
from fastapi import FastAPI, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field
from typing import Optional

from app.database import engine, Base, get_db
from app.models import Tenant, UsageEvent

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="LLM Usage Metering & Billing Engine")

# Pricing Constants (in USD)
PRICING = {
    "api_call": 0.001,               # $0.001 per API call
    "input_token": 0.000003,         # $3.00 per 1M tokens
    "cached_input_token": 0.0000015,  # $1.50 per 1M tokens (Cheaper)
    "output_token": 0.000015,        # $15.00 per 1M tokens
    "reasoning_token": 0.000015,     # Billed as output tokens
}

# --- Pydantic Schemas ---
class UsageRequest(BaseModel):
    tenant_id: str
    event_type: str = Field(..., description="'api_call' or 'ai_tokens'")
    input_tokens: Optional[int] = 0
    cached_input_tokens: Optional[int] = 0
    output_tokens: Optional[int] = 0
    reasoning_tokens: Optional[int] = 0
    quantity: Optional[int] = 1

# --- Helper Seed Function ---
@app.on_event("startup")
def seed_initial_tenants():
    db = next(get_db())
    if not db.query(Tenant).filter(Tenant.id == "tenant_demo").first():
        demo_tenant = Tenant(
            id="tenant_demo",
            name="Demo Customer",
            plan_name="Free",
            api_call_limit=1000,
            token_limit=100000
        )
        db.add(demo_tenant)
        db.commit()

# --- Core Routes ---

@app.get("/")
def health_check():
    return {"status": "online", "service": "Metering & Billing Engine"}

@app.post("/generate")
def record_usage(
    payload: UsageRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    # 1. Idempotency Check (Exactly-Once Metering)
    existing_event = db.query(UsageEvent).filter(UsageEvent.idempotency_key == idempotency_key).first()
    if existing_event:
        return {
            "status": "duplicate_ignored",
            "message": "Request already processed with this Idempotency-Key",
            "event_id": existing_event.id,
            "cost_usd": existing_event.cost_usd
        }

    # 2. Fetch Tenant
    tenant = db.query(Tenant).filter(Tenant.id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # 3. Calculate Current Usage & Enforce Quotas
    if payload.event_type == "api_call":
        current_usage = db.query(func.coalesce(func.sum(UsageEvent.quantity), 0))\
            .filter(UsageEvent.tenant_id == tenant.id, UsageEvent.event_type == "api_call").scalar()
            
        if current_usage + payload.quantity > tenant.api_call_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"API call quota exceeded. Limit: {tenant.api_call_limit}, Current: {current_usage}"
            )
            
        cost = payload.quantity * PRICING["api_call"]

    elif payload.event_type == "ai_tokens":
        current_tokens = db.query(
            func.coalesce(func.sum(
                UsageEvent.input_tokens + UsageEvent.cached_input_tokens + 
                UsageEvent.output_tokens + UsageEvent.reasoning_tokens
            ), 0)
        ).filter(UsageEvent.tenant_id == tenant.id, UsageEvent.event_type == "ai_tokens").scalar()

        total_requested_tokens = (
            payload.input_tokens + payload.cached_input_tokens + 
            payload.output_tokens + payload.reasoning_tokens
        )

        if current_tokens + total_requested_tokens > tenant.token_limit:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Token limit exceeded. Limit: {tenant.token_limit}, Current: {current_tokens}. Upgrade required."
            )

        # Money Math Calculation
        cost = (
            (payload.input_tokens * PRICING["input_token"]) +
            (payload.cached_input_tokens * PRICING["cached_input_token"]) +
            (payload.output_tokens * PRICING["output_token"]) +
            (payload.reasoning_tokens * PRICING["reasoning_token"])
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid event_type")

    # 4. Save Usage Event
    event = UsageEvent(
        idempotency_key=idempotency_key,
        tenant_id=tenant.id,
        event_type=payload.event_type,
        input_tokens=payload.input_tokens,
        cached_input_tokens=payload.cached_input_tokens,
        output_tokens=payload.output_tokens,
        reasoning_tokens=payload.reasoning_tokens,
        quantity=payload.quantity,
        cost_usd=round(cost, 6)
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    return {
        "status": "success",
        "event_id": event.id,
        "tenant_id": tenant.id,
        "cost_usd": event.cost_usd
    }

@app.get("/usage")
def get_tenant_usage(tenant_id: str, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    total_api_calls = db.query(func.coalesce(func.sum(UsageEvent.quantity), 0))\
        .filter(UsageEvent.tenant_id == tenant.id, UsageEvent.event_type == "api_call").scalar()

    total_tokens = db.query(
        func.coalesce(func.sum(
            UsageEvent.input_tokens + UsageEvent.cached_input_tokens + 
            UsageEvent.output_tokens + UsageEvent.reasoning_tokens
        ), 0)
    ).filter(UsageEvent.tenant_id == tenant.id, UsageEvent.event_type == "ai_tokens").scalar()

    total_cost = db.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0))\
        .filter(UsageEvent.tenant_id == tenant.id).scalar()

    return {
        "tenant_id": tenant.id,
        "plan_name": tenant.plan_name,
        "api_calls": {"used": total_api_calls, "limit": tenant.api_call_limit},
        "ai_tokens": {"used": total_tokens, "limit": tenant.token_limit},
        "total_cost_usd": round(total_cost, 4)
    }