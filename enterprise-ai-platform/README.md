# Enterprise AI Test Automation Platform (EATAP)

> **Principal Enterprise Architect · AI Solution Architect · Staff Software Engineer design**
> Production-grade, Fortune 100 ready. Azure-native. Agentic AI + Enterprise RAG.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.3-61DAFB?logo=react)](https://react.dev)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-FF6B35)](https://langchain-ai.github.io/langgraph)
[![Azure](https://img.shields.io/badge/Azure-AKS-0078D4?logo=microsoft-azure)](https://azure.microsoft.com)

---

## Overview

EATAP automatically generates, reviews, and publishes enterprise-grade test cases from Azure DevOps
User Stories using a multi-agent AI pipeline backed by Enterprise RAG and mandatory human approval.

## Architecture

```
React SPA → Azure APIM → FastAPI → LangGraph Multi-Agent → Azure OpenAI
                                                        ↓
                                             Enterprise RAG (Azure AI Search)
                                                        ↓
                                          Human Approval → Azure DevOps
```

## Quick Start (Development)

```bash
# Prerequisites: Python 3.12+, Node 20+, Docker Desktop

# 1. Clone + setup
git clone https://github.com/org/enterprise-ai-platform.git
cd enterprise-ai-platform

# 2. Backend
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # Fill in Azure credentials
uvicorn app.main:app --reload --port 8000

# 3. Frontend (separate terminal)
cd frontend
npm install
npm run dev  # http://localhost:5173

# 4. Full stack with Docker Compose
docker compose up -d
```

## Key Features

| Feature | Status |
|---------|--------|
| Azure AD + OAuth2 + OIDC + MFA | ✅ |
| 6 Specialized AI Agents (LangGraph) | ✅ |
| Enterprise RAG (Azure AI Search + FAISS) | ✅ |
| MCP Server Integrations | ✅ |
| Human-in-the-Loop Approval Workflow | ✅ |
| RBAC + ABAC Authorization | ✅ |
| Prompt Injection Protection | ✅ |
| PII Detection (Presidio) | ✅ |
| Token Optimization (Semantic Cache) | ✅ |
| Event-Driven Architecture (Service Bus) | ✅ |
| OpenTelemetry Observability | ✅ |
| AKS Deployment (Helm + Terraform) | ✅ |
| CI/CD (GitHub Actions) | ✅ |

## Project Structure

See [docs/architecture/folder-structure.md](docs/architecture/folder-structure.md) for the full annotated structure.

## Documentation

- [Architecture Overview](docs/architecture/README.md)
- [API Reference](docs/api/openapi.yaml)
- [Deployment Guide](docs/deployment/README.md)
- [Security Model](docs/security/README.md)
- [RAG Pipeline](docs/rag/README.md)

## License

Enterprise Proprietary — All rights reserved.
