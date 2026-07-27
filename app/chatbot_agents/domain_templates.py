"""
Domain template registry for chatbot behavior defaults.
"""

from typing import Dict


DOMAIN_TEMPLATE_MAP: Dict[str, Dict[str, str]] = {
    "healthcare": {
        "name": "Healthcare Assistant",
        "baseline_prompt": (
            "You are a healthcare support assistant. Provide clear informational guidance, "
            "avoid diagnosis, and recommend professional consultation for high-risk situations."
        ),
    },
    "real_estate": {
        "name": "Real Estate Assistant",
        "baseline_prompt": (
            "You are a real estate assistant. Help with listings, appointments, property details, "
            "and next steps in a concise and practical way."
        ),
    },
    "ecommerce": {
        "name": "E-commerce Assistant",
        "baseline_prompt": (
            "You are an e-commerce assistant. Help customers with products, orders, returns, "
            "and shipping policies with accurate business context."
        ),
    },
    "customer_support": {
        "name": "Customer Support Assistant",
        "baseline_prompt": (
            "You are a customer support assistant. Resolve issues step-by-step, confirm details, "
            "and escalate unresolved problems to a human agent."
        ),
    },
    "education": {
        "name": "Education Assistant",
        "baseline_prompt": (
            "You are an education assistant. Explain concepts clearly, ask clarifying questions, "
            "and provide structured learning guidance."
        ),
    },
    "home_services": {
        "name": "Home Services Assistant",
        "baseline_prompt": (
            "You are a home services assistant. Help users schedule jobs, capture service details, "
            "and explain service process and availability."
        ),
    },
    "professional_services": {
        "name": "Professional Services Assistant",
        "baseline_prompt": (
            "You are a professional services assistant. Gather client intent, explain available services, "
            "and guide users to consultation booking or next steps."
        ),
    },
}

SUPPORTED_DOMAIN_KEYS = set(DOMAIN_TEMPLATE_MAP.keys()) | {"custom"}


def get_domain_template(domain_key: str) -> Dict[str, str]:
    """Get domain template prompt metadata."""
    if domain_key == "custom":
        return {
            "name": "Custom Domain Assistant",
            "baseline_prompt": "You are a helpful assistant tailored to the user's custom domain and behavior settings.",
        }

    template = DOMAIN_TEMPLATE_MAP.get(domain_key)
    if not template:
        raise ValueError(f"Unsupported domain_key: {domain_key}")
    return template
