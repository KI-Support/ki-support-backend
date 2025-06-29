# main.py
import os
import stripe
import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    stripe_customer_id = Column(String)
    stripe_price_id = Column(String)
    subscription_status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

class ChatRequest(BaseModel):
    user_id: int
    message: str

@app.post("/chat")
async def chat(request: ChatRequest):
    db = SessionLocal()
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user or user.subscription_status != "active":
        raise HTTPException(status_code=403, detail="Abo erforderlich.")
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": request.message}]
    )
    return {"reply": response.choices[0].message.content}

class CheckoutRequest(BaseModel):
    email: str
    price_id: str

@app.post("/create-checkout-session")
async def create_checkout_session(req: CheckoutRequest):
    db = SessionLocal()
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        customer = stripe.Customer.create(email=req.email)
        user = User(email=req.email, stripe_customer_id=customer.id)
        db.add(user); db.commit(); db.refresh(user)
    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        mode="subscription",
        line_items=[{"price": req.price_id, "quantity": 1}],
        success_url=os.getenv("FRONTEND_URL") + "/success",
        cancel_url=os.getenv("FRONTEND_URL") + "/cancel"
    )
    return {"sessionId": session.id}

@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db = SessionLocal()
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user = db.query(User).filter(User.stripe_customer_id == session["customer"]).first()
        user.subscription_status = "active"
        user.stripe_price_id = session["subscription"]
        db.commit()
    return {"status": "ok"}
