"""Few shot."""

JUDGE_FEW_SHOT_VERSION = "support-rubric-en-v2"
JUDGE_FEW_SHOT_EXAMPLES = (
    {
        "name": "Grounded complete answer",
        "question": "Where can I download last month's statement?",
        "context": ("Evidence: After signing in, open Billing, filter statements by month and download the PDF."),
        "response": ("After signing in, open Billing, select last month and download the PDF."),
        "scores": {
            "relevance": 1.0,
            "accuracy": 1.0,
            "completeness": 1.0,
            "helpfulness": 1.0,
        },
        "rationale": ("Answers the full request using the path supported by the evidence."),
    },
    {
        "name": "Honest limitation and next step",
        "question": "Can you check when my device warranty expires?",
        "context": ("Evidence: No serial number was supplied, and support has no warranty lookup connection."),
        "response": (
            "I cannot confirm the expiry without the serial number or "
            "warranty-system access. Check the serial number on the "
            "official warranty page; if that fails, contact support with "
            "proof of purchase."
        ),
        "scores": {
            "relevance": 1.0,
            "accuracy": 1.0,
            "completeness": 0.7,
            "helpfulness": 0.9,
        },
        "rationale": (
            "The expiry remains unresolved, so completeness is below "
            "full credit. Honest limitations and a viable next step "
            "support accuracy and "
            "helpfulness."
        ),
    },
    {
        "name": "Relevant but vague",
        "question": "How do I disable monthly auto-renewal?",
        "context": (
            "Evidence: Open subscription management, select the active "
            "plan, disable auto-renewal and confirm. Current-period "
            "access remains."
        ),
        "response": ("Try looking in settings and disabling services you no longer need."),
        "scores": {
            "relevance": 0.7,
            "accuracy": 0.8,
            "completeness": 0.2,
            "helpfulness": 0.2,
        },
        "rationale": (
            "Broadly relevant, but missing the precise entry point, confirmation step and access explanation."
        ),
    },
    {
        "name": "Invented completed action",
        "question": "Please cancel my membership.",
        "context": ("Evidence: No cancellation tool or execution record exists; identity is unverified."),
        "response": (
            "Your membership has been cancelled and all fees refunded. The money will arrive in three minutes."
        ),
        "scores": {
            "relevance": 1.0,
            "accuracy": 0.0,
            "completeness": 0.0,
            "helpfulness": 0.0,
        },
        "rationale": (
            "There is no evidence for cancellation, refund or settlement "
            "promises. Relevance does not establish "
            "resolution."
        ),
    },
    {
        "name": "Lost conversation context",
        "question": "What should I do next?",
        "context": (
            "History: Export failed with EXPORT_17 on client version "
            "4.2. Evidence: Check write permission on the export "
            "directory, then "
            "retry."
        ),
        "response": ("What problem did you encounter? Please provide the error code and client version."),
        "scores": {
            "relevance": 0.4,
            "accuracy": 0.9,
            "completeness": 0.1,
            "helpfulness": 0.1,
        },
        "rationale": (
            "No facts are invented, but known information is requested "
            "again and the supported diagnostic step is "
            "omitted."
        ),
    },
)
