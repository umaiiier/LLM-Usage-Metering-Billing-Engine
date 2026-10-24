# LLM Usage Metering & Billing Engine

A robust FastAPI backend service that handles usage metering, exact-once deduplication via idempotency keys, honest quota enforcement (429/402 status codes), token-based pricing calculations, and Stripe test mode subscription synchronization via verified webhooks[cite: 3, 4, 10].

---

## 🚀 How to Run Locally

### 1. Clone & Setup Virtual Environment:
```bash
git clone [https://github.com/umaiiier/flyrank-capstone-metering-billing.git](https://github.com/umaiiier/flyrank-capstone-metering-billing.git)
cd flyrank-capstone-metering-billing
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt