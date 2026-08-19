"""
Domain Enumerations — Single source of truth for all status codes and types.
"""
from __future__ import annotations

from enum import Enum


class ApprovalStatus(str, Enum):
    """Test case lifecycle status."""
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVISION = "REVISION"
    PUBLISHED = "PUBLISHED"


class TestCaseType(str, Enum):
    """Categories of generated test cases."""
    FUNCTIONAL = "functional"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    EDGE_CASE = "edge_case"
    ERROR_HANDLING = "error_handling"
    API = "api"
    UI = "ui"
    REGRESSION = "regression"
    SMOKE = "smoke"
    INTEGRATION = "integration"


class TestCasePriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserRole(str, Enum):
    SYSTEM_ADMIN = "system_admin"
    QA_MANAGER = "qa_manager"
    SENIOR_TESTER = "senior_tester"
    TESTER = "tester"
    DEVELOPER = "developer"
    READ_ONLY = "read_only"
    APPROVER = "approver"
    ARCHITECT = "architect"


class AuditAction(str, Enum):
    """Immutable audit actions for compliance."""
    SESSION_CREATED = "session_created"
    STORY_FETCHED = "story_fetched"
    GHERKIN_GENERATED = "gherkin_generated"
    TEST_CASES_GENERATED = "test_cases_generated"
    REVIEW_REQUESTED = "review_requested"
    COMMENT_ADDED = "comment_added"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    ADO_UPDATED = "ado_updated"
    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_DELETED = "document_deleted"
    PROMPT_UPDATED = "prompt_updated"
    SETTINGS_CHANGED = "settings_changed"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"


class AgentName(str, Enum):
    INTENT_ROUTER = "intent_router"
    ADO_READER = "ado_reader"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    TEST_CREATION = "test_creation"
    KNOWLEDGE = "knowledge"
    APPROVAL = "approval"
    ADO_UPDATE = "ado_update"


class DocumentCategory(str, Enum):
    TESTING_STANDARDS = "testing_standards"
    ORG_GUIDELINES = "org_guidelines"
    EXISTING_TEST_CASES = "existing_test_cases"
    DOMAIN_DOCUMENTS = "domain_documents"
    BUSINESS_RULES = "business_rules"
    TEST_TEMPLATES = "test_templates"
    REGULATORY_DOCUMENTS = "regulatory_documents"
    QA_CHECKLISTS = "qa_checklists"
    NAMING_STANDARDS = "naming_standards"


class EventType(str, Enum):
    STORY_FETCHED = "story.fetched"
    GHERKIN_GENERATED = "gherkin.generated"
    TEST_CASES_DRAFTED = "testcases.drafted"
    REVIEW_REQUESTED = "review.requested"
    TEST_CASES_APPROVED = "testcases.approved"
    TEST_CASES_REJECTED = "testcases.rejected"
    ADO_UPDATED = "ado.updated"
    DOCUMENT_INGESTED = "document.ingested"
