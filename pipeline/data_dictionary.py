"""LATAM Bank data dictionary (Factored Datathon 2026, dataset v1.0.0).

Transcribed from kickoff_docs/LATAM_Bank_Complete_Data_Dictionary.pdf.
`rows` are the documented approximate counts; `enums` are the documented allowed values, plus four
values the data carries but the PDF omits (products.product_type Insurance, call_center_interactions
channel Web, reason_category Retention, detected_sentiment Very Positive): see docs/known_issues.md.
"""

TABLES = {
    # ---------------------------------------------------------------- dimensions
    "customers": {
        "kind": "dimension", "rows": 150_000, "partition": "monthly_snapshot",
        "pk": ["customer_id"], "unique": ["document_number"],
        "columns": [
            "customer_id", "document_number", "document_type", "first_name", "last_name",
            "date_of_birth", "gender", "email", "mobile_phone", "landline_phone", "address",
            "city", "state", "country", "postal_code", "detected_accent", "segment",
            "credit_score", "estimated_monthly_income", "occupation", "marital_status",
            "education_level", "registration_date", "registration_branch_id",
            "customer_status", "last_updated", "accepts_marketing",
        ],
        "not_null": [
            "customer_id", "document_number", "document_type", "first_name", "last_name",
            "date_of_birth", "city", "state", "country", "segment", "registration_date",
            "registration_branch_id", "customer_status", "last_updated", "accepts_marketing",
        ],
        "enums": {
            "document_type": ["DNI", "CURP", "CC", "CE", "Passport"],
            "gender": ["M", "F", "O"],
            "country": ["Mexico", "Colombia", "Argentina"],
            "detected_accent": ["mexican", "colombian", "argentine", "neutral"],
            "segment": ["Premium", "Plus", "Basic", "Student"],
            "customer_status": ["Active", "Inactive", "Suspended", "Closed"],
        },
        "ranges": {"credit_score": (300, 850)},
    },
    "products": {
        "kind": "dimension", "rows": 400_000, "partition": "monthly_snapshot",
        "pk": ["product_id"], "unique": ["product_number"],
        "columns": [
            "product_id", "customer_id", "product_type", "product_number", "currency",
            "current_balance", "credit_limit", "interest_rate", "opening_date",
            "expiration_date", "opening_branch_id", "product_status", "opening_channel",
            "has_linked_app", "days_past_due", "last_transaction_date", "last_updated",
        ],
        "not_null": [
            "product_id", "customer_id", "product_type", "product_number", "currency",
            "current_balance", "opening_date", "opening_branch_id", "product_status",
            "opening_channel", "has_linked_app", "last_updated",
        ],
        "enums": {
            "product_type": [
                "Checking Account", "Savings Account", "Credit Card", "Debit Card",
                "Personal Loan", "Mortgage", "Investment",
                "Insurance",
            ],
            "currency": ["MXN", "COP", "ARS", "USD"],
            "product_status": ["Active", "Blocked", "Closed", "Suspended"],
            "opening_channel": ["Branch", "Web", "App", "Call Center"],
        },
    },
    "branches": {
        "kind": "dimension", "rows": 350, "partition": "full_snapshot",
        "pk": ["branch_id"], "unique": ["branch_code"],
        "columns": [
            "branch_id", "branch_code", "branch_name", "branch_type", "address", "city",
            "state", "country", "postal_code", "geographic_zone", "phone", "email",
            "opening_time", "closing_time", "has_atms", "atm_count", "has_teller_windows",
            "teller_window_count", "latitude", "longitude", "branch_opening_date",
            "branch_status",
        ],
        "not_null": [
            "branch_id", "branch_code", "branch_name", "branch_type", "address", "city",
            "state", "country", "geographic_zone", "phone", "opening_time", "closing_time",
            "has_atms", "has_teller_windows", "branch_opening_date", "branch_status",
        ],
        "enums": {
            "branch_type": ["Main", "Express", "Premium", "Corporate"],
            "geographic_zone": ["Urban", "Suburban", "Rural"],
            "branch_status": ["Active", "Temporarily Closed", "Closed"],
        },
    },
    "service_agents": {
        "kind": "dimension", "rows": 1_200, "partition": "monthly_snapshot",
        "pk": ["agent_id"], "unique": ["employee_code"],
        "columns": [
            "agent_id", "employee_code", "first_name", "last_name", "email", "phone",
            "native_accent", "country_of_origin", "assigned_branch_id", "agent_type",
            "experience_level", "languages", "specialty", "hire_date", "avg_csat",
            "total_monthly_interactions", "agent_status", "work_shift",
        ],
        "not_null": [
            "agent_id", "employee_code", "first_name", "last_name", "email", "native_accent",
            "country_of_origin", "agent_type", "experience_level", "languages", "hire_date",
            "agent_status", "work_shift",
        ],
        "enums": {
            "native_accent": ["mexican", "colombian", "argentine"],
            "agent_type": ["Phone", "In-Person", "Digital", "Hybrid"],
            "experience_level": ["Junior", "Mid-Senior", "Senior", "Specialist"],
            "agent_status": ["Active", "Vacation", "Leave", "Inactive"],
            "work_shift": ["Morning", "Afternoon", "Night", "Rotating"],
        },
        "ranges": {"avg_csat": (1, 5)},
    },
    "marketing_campaigns": {
        "kind": "dimension", "rows": 200, "partition": "full_snapshot",
        "pk": ["campaign_id"], "unique": [],
        "columns": [
            "campaign_id", "campaign_name", "description", "campaign_type",
            "campaign_objective", "promoted_product", "target_segment", "target_country",
            "start_date", "end_date", "budget", "campaign_status", "expected_conversion_rate",
        ],
        "not_null": [
            "campaign_id", "campaign_name", "campaign_type", "campaign_objective",
            "start_date", "end_date", "campaign_status",
        ],
        "enums": {
            "campaign_type": ["Email", "SMS", "Push", "WhatsApp", "Voice", "Mix"],
            "campaign_objective": ["Acquisition", "Retention", "Cross-sell", "Up-sell", "Reactivation"],
            "campaign_status": ["Planned", "Active", "Paused", "Completed"],
        },
    },
    # --------------------------------------------------------------------- facts
    "transactions": {
        "kind": "fact", "rows": 5_000_000, "partition": "daily", "event_ts": "transaction_date",
        "pk": ["transaction_id"], "unique": [],
        "columns": [
            "transaction_id", "transaction_date", "process_date", "product_id", "customer_id",
            "transaction_type", "transaction_category", "amount", "currency", "amount_usd",
            "channel", "branch_id", "merchant_name", "merchant_category",
            "transaction_country", "transaction_city", "transaction_status", "response_code",
            "is_fraud", "fraud_score", "latitude", "longitude",
        ],
        "not_null": [
            "transaction_id", "transaction_date", "process_date", "product_id", "customer_id",
            "transaction_type", "amount", "currency", "channel", "transaction_country",
            "transaction_status", "is_fraud",
        ],
        "enums": {
            "transaction_type": ["Deposit", "Withdrawal", "Transfer", "Payment", "Purchase", "Adjustment"],
            "transaction_category": ["Food", "Transport", "Services", "Entertainment", "Health", "Other"],
            "currency": ["MXN", "COP", "ARS", "USD"],
            "channel": ["ATM", "Branch", "Web", "App", "POS", "Transfer"],
            "transaction_status": ["Approved", "Declined", "Pending", "Reversed"],
        },
        "ranges": {"fraud_score": (0, 100)},
    },
    "call_center_interactions": {
        "kind": "fact", "rows": 800_000, "partition": "daily", "event_ts": "interaction_date",
        "pk": ["interaction_id"], "unique": [],
        "columns": [
            "interaction_id", "interaction_date", "process_date", "customer_id", "agent_id",
            "interaction_type", "channel", "contact_reason", "reason_category",
            "duration_seconds", "wait_time_seconds", "was_resolved", "requires_followup",
            "detected_sentiment", "sentiment_score", "customer_detected_accent",
            "agent_used_accent", "was_escalated", "mentioned_products", "has_transcript",
            "has_recording",
        ],
        "not_null": [
            "interaction_id", "interaction_date", "process_date", "customer_id",
            "interaction_type", "channel", "contact_reason", "reason_category",
            "requires_followup", "was_escalated", "has_transcript", "has_recording",
        ],
        "enums": {
            "interaction_type": ["Inbound Call", "Outbound Call", "Chat", "Email", "Video"],
            "channel": ["Phone", "Web Chat", "WhatsApp", "Email", "App", "Web"],
            "reason_category": ["Transactional", "Product", "Technical", "Commercial", "Complaint", "Retention"],
            "detected_sentiment": ["Positive", "Neutral", "Negative", "Very Negative", "Very Positive"],
        },
        "ranges": {"sentiment_score": (-1, 1)},
    },
    "call_transcripts": {
        "kind": "fact", "rows": 200_000, "partition": "daily", "event_ts": None,
        "pk": ["transcript_id"], "unique": [],
        "columns": [
            "transcript_id", "interaction_id", "process_date", "customer_id", "agent_id",
            "full_text", "customer_text", "agent_text", "detected_language", "detected_accent",
            "accent_confidence", "detected_keywords", "mentioned_entities", "detected_intents",
            "main_topics", "transcription_model", "audio_quality", "duration_seconds",
        ],
        "not_null": [
            "transcript_id", "interaction_id", "process_date", "customer_id", "agent_id",
            "full_text", "detected_language", "transcription_model", "duration_seconds",
        ],
        "enums": {"audio_quality": ["High", "Medium", "Low"]},
        "ranges": {"accent_confidence": (0, 1)},
    },
    "satisfaction_surveys": {
        "kind": "fact", "rows": 250_000, "partition": "daily", "event_ts": "survey_date",
        "pk": ["survey_id"], "unique": [],
        "columns": [
            "survey_id", "survey_date", "process_date", "interaction_id", "customer_id",
            "agent_id", "survey_type", "send_channel", "main_score", "nps_category",
            "question_1_text", "question_1_response", "question_2_text", "question_2_response",
            "question_3_text", "question_3_response", "open_comments", "comment_sentiment",
            "response_time_hours", "campaign_response_rate",
        ],
        "not_null": [
            "survey_id", "survey_date", "process_date", "customer_id", "survey_type",
            "send_channel", "main_score",
        ],
        "enums": {
            "survey_type": ["CSAT", "NPS", "CES"],
            "send_channel": ["Email", "SMS", "IVR", "App", "Web"],
            "nps_category": ["Promoter", "Passive", "Detractor"],
        },
        "ranges": {
            "main_score": (0, 10),
            "question_1_response": (1, 5), "question_2_response": (1, 5), "question_3_response": (1, 5),
        },
    },
    "digital_events": {
        "kind": "fact", "rows": 10_000_000, "partition": "daily", "event_ts": "event_date",
        "pk": ["event_id"], "unique": [],
        "columns": [
            "event_id", "event_date", "process_date", "customer_id", "session_id",
            "event_type", "event_category", "channel", "platform", "browser", "app_version",
            "page_url", "page_title", "action", "element_id", "product_id", "event_value",
            "duration_seconds", "ip_address", "ip_country", "ip_city", "is_mobile", "referrer",
            "utm_source", "utm_medium", "utm_campaign",
        ],
        "not_null": [
            "event_id", "event_date", "process_date", "session_id", "event_type",
            "event_category", "channel", "is_mobile",
        ],
        "enums": {
            "event_type": ["PageView", "Click", "FormSubmit", "Login", "Logout", "Error", "Purchase"],
            "event_category": ["Navigation", "Transaction", "Authentication", "Product"],
            "channel": ["Android App", "iOS App", "Desktop Web", "Mobile Web"],
            "platform": ["Android", "iOS", "Windows", "MacOS", "Linux"],
        },
    },
    "complaints": {
        "kind": "fact", "rows": 80_000, "partition": "daily", "event_ts": "creation_date",
        "pk": ["complaint_id"], "unique": [],
        "columns": [
            "complaint_id", "creation_date", "process_date", "customer_id", "case_type",
            "category", "subcategory", "reception_channel", "affected_product_id",
            "related_branch_id", "origin_interaction_id", "description", "claimed_amount",
            "currency", "priority", "status", "assigned_agent_id", "assignment_date",
            "first_response_date", "resolution_date", "closing_date", "sla_breached",
            "resolution_days", "resolution", "compensation_granted", "resolution_satisfaction",
            "is_repeat_complainer",
        ],
        "not_null": [
            "complaint_id", "creation_date", "process_date", "customer_id", "case_type",
            "category", "reception_channel", "description", "priority", "status",
            "sla_breached", "is_repeat_complainer",
        ],
        "enums": {
            "case_type": ["Complaint", "Claim", "Request", "Suggestion"],
            "reception_channel": ["Call Center", "Email", "Web", "App", "Branch", "Regulator"],
            "priority": ["Low", "Medium", "High", "Critical"],
            "status": ["Open", "In Process", "Escalated", "Resolved", "Closed", "Rejected"],
        },
        "ranges": {"resolution_satisfaction": (1, 5)},
    },
    "campaign_sends": {
        "kind": "fact", "rows": 2_000_000, "partition": "daily", "event_ts": "send_date",
        "pk": ["send_id"], "unique": [],
        "columns": [
            "send_id", "send_date", "process_date", "campaign_id", "customer_id",
            "send_channel", "template_used", "subject", "send_status", "was_delivered",
            "was_opened", "open_date", "was_clicked", "click_date", "click_count",
            "had_conversion", "conversion_date", "conversion_value", "open_device",
            "open_country", "failure_reason", "send_cost",
        ],
        "not_null": [
            "send_id", "send_date", "process_date", "campaign_id", "customer_id",
            "send_channel", "send_status", "was_delivered", "had_conversion",
        ],
        "enums": {
            "send_channel": ["Email", "SMS", "Push", "WhatsApp", "Voice"],
            "send_status": ["Sent", "Failed", "Bounced", "Blocked"],
        },
    },
    # ----------------------------------------------------------------- reference
    "daily_exchange_rates": {
        "kind": "reference", "rows": 3_000, "partition": "daily",
        "pk": ["date", "source_currency", "target_currency"], "unique": [],
        "columns": [
            "date", "source_currency", "target_currency", "exchange_rate", "buy_rate",
            "sell_rate", "source",
        ],
        "not_null": ["date", "source_currency", "target_currency", "exchange_rate"],
        "enums": {
            "source_currency": ["MXN", "COP", "ARS", "USD"],
            "target_currency": ["MXN", "COP", "ARS", "USD"],
        },
    },
}

# (child_table, child_column, parent_table, parent_column)
FOREIGN_KEYS = [
    ("products", "customer_id", "customers", "customer_id"),
    ("transactions", "customer_id", "customers", "customer_id"),
    ("call_center_interactions", "customer_id", "customers", "customer_id"),
    ("call_transcripts", "customer_id", "customers", "customer_id"),
    ("satisfaction_surveys", "customer_id", "customers", "customer_id"),
    ("digital_events", "customer_id", "customers", "customer_id"),
    ("complaints", "customer_id", "customers", "customer_id"),
    ("campaign_sends", "customer_id", "customers", "customer_id"),
    ("customers", "registration_branch_id", "branches", "branch_id"),
    ("products", "opening_branch_id", "branches", "branch_id"),
    ("service_agents", "assigned_branch_id", "branches", "branch_id"),
    ("transactions", "branch_id", "branches", "branch_id"),
    ("complaints", "related_branch_id", "branches", "branch_id"),
    ("call_center_interactions", "agent_id", "service_agents", "agent_id"),
    ("call_transcripts", "agent_id", "service_agents", "agent_id"),
    ("satisfaction_surveys", "agent_id", "service_agents", "agent_id"),
    ("complaints", "assigned_agent_id", "service_agents", "agent_id"),
    ("transactions", "product_id", "products", "product_id"),
    ("digital_events", "product_id", "products", "product_id"),
    ("complaints", "affected_product_id", "products", "product_id"),
    ("campaign_sends", "campaign_id", "marketing_campaigns", "campaign_id"),
    ("call_transcripts", "interaction_id", "call_center_interactions", "interaction_id"),
    ("satisfaction_surveys", "interaction_id", "call_center_interactions", "interaction_id"),
    ("complaints", "origin_interaction_id", "call_center_interactions", "interaction_id"),
]

# Documented column types (DuckDB spelling of the PDF's SQL types; TEXT -> varchar).
V = "varchar"
TYPES = {
    "customers": {
        "customer_id": V, "document_number": V, "document_type": V, "first_name": V, "last_name": V,
        "date_of_birth": "date", "gender": V, "email": V, "mobile_phone": V, "landline_phone": V,
        "address": V, "city": V, "state": V, "country": V, "postal_code": V, "detected_accent": V,
        "segment": V, "credit_score": "integer", "estimated_monthly_income": "decimal(12,2)",
        "occupation": V, "marital_status": V, "education_level": V, "registration_date": "timestamp",
        "registration_branch_id": V, "customer_status": V, "last_updated": "timestamp",
        "accepts_marketing": "boolean",
    },
    "products": {
        "product_id": V, "customer_id": V, "product_type": V, "product_number": V, "currency": V,
        "current_balance": "decimal(15,2)", "credit_limit": "decimal(15,2)", "interest_rate": "decimal(5,2)",
        "opening_date": "date", "expiration_date": "date", "opening_branch_id": V, "product_status": V,
        "opening_channel": V, "has_linked_app": "boolean", "days_past_due": "integer",
        "last_transaction_date": "timestamp", "last_updated": "timestamp",
    },
    "branches": {
        "branch_id": V, "branch_code": V, "branch_name": V, "branch_type": V, "address": V, "city": V,
        "state": V, "country": V, "postal_code": V, "geographic_zone": V, "phone": V, "email": V,
        "opening_time": "time", "closing_time": "time", "has_atms": "boolean", "atm_count": "integer",
        "has_teller_windows": "boolean", "teller_window_count": "integer", "latitude": "decimal(10,7)",
        "longitude": "decimal(10,7)", "branch_opening_date": "date", "branch_status": V,
    },
    "service_agents": {
        "agent_id": V, "employee_code": V, "first_name": V, "last_name": V, "email": V, "phone": V,
        "native_accent": V, "country_of_origin": V, "assigned_branch_id": V, "agent_type": V,
        "experience_level": V, "languages": V, "specialty": V, "hire_date": "date",
        "avg_csat": "decimal(3,2)", "total_monthly_interactions": "integer", "agent_status": V,
        "work_shift": V,
    },
    "marketing_campaigns": {
        "campaign_id": V, "campaign_name": V, "description": V, "campaign_type": V,
        "campaign_objective": V, "promoted_product": V, "target_segment": V, "target_country": V,
        "start_date": "date", "end_date": "date", "budget": "decimal(12,2)", "campaign_status": V,
        "expected_conversion_rate": "decimal(5,2)",
    },
    "transactions": {
        "transaction_id": V, "transaction_date": "timestamp", "process_date": "date", "product_id": V,
        "customer_id": V, "transaction_type": V, "transaction_category": V, "amount": "decimal(15,2)",
        "currency": V, "amount_usd": "decimal(15,2)", "channel": V, "branch_id": V, "merchant_name": V,
        "merchant_category": V, "transaction_country": V, "transaction_city": V,
        "transaction_status": V, "response_code": V, "is_fraud": "boolean", "fraud_score": "decimal(5,2)",
        "latitude": "decimal(10,7)", "longitude": "decimal(10,7)",
    },
    "call_center_interactions": {
        "interaction_id": V, "interaction_date": "timestamp", "process_date": "date", "customer_id": V,
        "agent_id": V, "interaction_type": V, "channel": V, "contact_reason": V, "reason_category": V,
        "duration_seconds": "integer", "wait_time_seconds": "integer", "was_resolved": "boolean",
        "requires_followup": "boolean", "detected_sentiment": V, "sentiment_score": "decimal(3,2)",
        "customer_detected_accent": V, "agent_used_accent": V, "was_escalated": "boolean",
        "mentioned_products": V, "has_transcript": "boolean", "has_recording": "boolean",
    },
    "call_transcripts": {
        "transcript_id": V, "interaction_id": V, "process_date": "date", "customer_id": V, "agent_id": V,
        "full_text": V, "customer_text": V, "agent_text": V, "detected_language": V, "detected_accent": V,
        "accent_confidence": "decimal(3,2)", "detected_keywords": V, "mentioned_entities": V,
        "detected_intents": V, "main_topics": V, "transcription_model": V, "audio_quality": V,
        "duration_seconds": "integer",
    },
    "satisfaction_surveys": {
        "survey_id": V, "survey_date": "timestamp", "process_date": "date", "interaction_id": V,
        "customer_id": V, "agent_id": V, "survey_type": V, "send_channel": V, "main_score": "integer",
        "nps_category": V, "question_1_text": V, "question_1_response": "integer", "question_2_text": V,
        "question_2_response": "integer", "question_3_text": V, "question_3_response": "integer",
        "open_comments": V, "comment_sentiment": V, "response_time_hours": "decimal(8,2)",
        "campaign_response_rate": "decimal(5,2)",
    },
    "digital_events": {
        "event_id": V, "event_date": "timestamp", "process_date": "date", "customer_id": V,
        "session_id": V, "event_type": V, "event_category": V, "channel": V, "platform": V, "browser": V,
        "app_version": V, "page_url": V, "page_title": V, "action": V, "element_id": V, "product_id": V,
        "event_value": "decimal(15,2)", "duration_seconds": "integer", "ip_address": V, "ip_country": V,
        "ip_city": V, "is_mobile": "boolean", "referrer": V, "utm_source": V, "utm_medium": V,
        "utm_campaign": V,
    },
    "complaints": {
        "complaint_id": V, "creation_date": "timestamp", "process_date": "date", "customer_id": V,
        "case_type": V, "category": V, "subcategory": V, "reception_channel": V,
        "affected_product_id": V, "related_branch_id": V, "origin_interaction_id": V, "description": V,
        "claimed_amount": "decimal(15,2)", "currency": V, "priority": V, "status": V,
        "assigned_agent_id": V, "assignment_date": "timestamp", "first_response_date": "timestamp",
        "resolution_date": "timestamp", "closing_date": "timestamp", "sla_breached": "boolean",
        "resolution_days": "integer", "resolution": V, "compensation_granted": "decimal(15,2)",
        "resolution_satisfaction": "integer", "is_repeat_complainer": "boolean",
    },
    "campaign_sends": {
        "send_id": V, "send_date": "timestamp", "process_date": "date", "campaign_id": V,
        "customer_id": V, "send_channel": V, "template_used": V, "subject": V, "send_status": V,
        "was_delivered": "boolean", "was_opened": "boolean", "open_date": "timestamp",
        "was_clicked": "boolean", "click_date": "timestamp", "click_count": "integer",
        "had_conversion": "boolean", "conversion_date": "timestamp", "conversion_value": "decimal(15,2)",
        "open_device": V, "open_country": V, "failure_reason": V, "send_cost": "decimal(10,4)",
    },
    "daily_exchange_rates": {
        "date": "date", "source_currency": V, "target_currency": V, "exchange_rate": "decimal(12,6)",
        "buy_rate": "decimal(12,6)", "sell_rate": "decimal(12,6)", "source": V,
    },
}

for _t, _spec in TABLES.items():
    assert list(TYPES[_t]) == _spec["columns"], f"TYPES and columns differ for {_t}"
