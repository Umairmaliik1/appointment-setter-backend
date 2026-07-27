"""
Telephony ownership service.
"""

from typing import Any, Dict, List

from app.api.v1.services.phone_number import phone_number_service


class TelephonyService:
    async def get_tenant_status(self, tenant_id: str) -> Dict[str, Any]:
        return await phone_number_service.get_telephony_status(tenant_id)

    async def list_tenant_numbers(self, tenant_id: str) -> List[Dict[str, Any]]:
        return await phone_number_service.list_phones_by_tenant(tenant_id)

    async def bind_cold_caller_outbound(self, tenant_id: str, phone_number: str) -> Dict[str, Any]:
        return await phone_number_service.bind_cold_caller_outbound_number(tenant_id=tenant_id, phone_number=phone_number)

    async def unbind_cold_caller_outbound(self, tenant_id: str, phone_number: str) -> Dict[str, Any]:
        return await phone_number_service.unbind_cold_caller_outbound_number(
            tenant_id=tenant_id, phone_number=phone_number
        )


telephony_service = TelephonyService()
