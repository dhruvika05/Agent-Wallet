from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import FRONTEND_ORIGIN

app = FastAPI(
    title="Agent Wallet API",
    description="A secure payment wallet for AI agents",
    version="0.1.0",
)

# Allow the React dashboard (Vite dev server) to call this API from the browser.
# Without this, the browser blocks the requests (a "CORS error").
# Before deploying, add your real frontend URL to this list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["FRONTEND_ORIGIN"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "Welcome to Agent Wallet API", "status": "running"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}