Agent Wallet

The Financial Firewall for Autonomous AI Agents.

Agent Wallet is a payment-control layer between AI agents and PayPal. It allows AI agents to request payments while ensuring every transaction follows predefined spending policies.

Problem Statement

As AI agents become capable of making autonomous purchases, giving them unrestricted access to money creates security and financial risks.

Agent Wallet addresses this problem by introducing spending limits, recipient restrictions, human approvals and transaction monitoring.

Key Features

* Policy Engine: Evaluates payment requests using predefined rules.
* Spending Limits: Enforces per-transaction, daily and weekly budgets.
* Human Approval: Allows users to approve or reject selected payments.
* PayPal Integration: Processes approved payments through PayPal Sandbox.
* Audit Logging: Records every important transaction event.
* Agent Management: Allows users to manage and freeze AI agents.

Tech Stack

Layer	Technology
Backend	Python 3.11, FastAPI
Database	SQLite, SQLAlchemy
HTTP Client	httpx
Frontend	React, Vite, Tailwind CSS
Table UI	AG Grid Community
Payments	PayPal Sandbox Payouts API
Testing	pytest

Project Structure

Coming soon.

Getting Started

Setup instructions will be added as the project develops.

Architecture

Coming soon.

License

This project is licensed under the MIT License.