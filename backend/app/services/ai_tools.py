import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArguments(ToolArguments):
    pass


class ShopHoursArguments(ToolArguments):
    day: date | None = None


class AppointmentSlotArguments(ToolArguments):
    service_ids: list[UUID] = Field(min_length=1, max_length=20)
    day: date
    barber_preference: UUID | Literal["any"] = "any"


class CreateBookingArguments(ToolArguments):
    service_ids: list[UUID] = Field(min_length=1, max_length=20)
    booking_type: Literal["queue", "appointment"]
    barber_preference: UUID | Literal["any"] = "any"
    slot_start: datetime | None = None
    request_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class BookingIdArguments(ToolArguments):
    booking_id: UUID
    reason: str | None = Field(default=None, min_length=3, max_length=500)


class RescheduleArguments(ToolArguments):
    booking_id: UUID
    slot_start: datetime
    request_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class EscalationArguments(ToolArguments):
    category: Literal["booking_help", "service_question", "safety", "complaint", "other"]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_type: str
    rendered: str
    data: dict[str, Any] = Field(default_factory=dict)


TOOL_ARGUMENT_MODELS: dict[str, type[ToolArguments]] = {
    "list_services": EmptyArguments,
    "get_shop_hours": ShopHoursArguments,
    "get_live_queue": EmptyArguments,
    "find_appointment_slots": AppointmentSlotArguments,
    "create_booking": CreateBookingArguments,
    "get_my_booking": EmptyArguments,
    "cancel_my_booking": BookingIdArguments,
    "reschedule_my_booking": RescheduleArguments,
    "escalate_to_management": EscalationArguments,
}

TOOL_DESCRIPTIONS = {
    "list_services": "List active services and authoritative gross prices for this shop.",
    "get_shop_hours": "Get authoritative shop hours for an optional date.",
    "get_live_queue": "Get the current privacy-safe queue summary.",
    "find_appointment_slots": "Find scoped appointment slots for selected services.",
    "create_booking": "Create this customer's booking using a unique request key.",
    "get_my_booking": "Get only this customer's current booking.",
    "cancel_my_booking": "Cancel only this customer's booking.",
    "reschedule_my_booking": "Reschedule only this customer's appointment.",
    "escalate_to_management": "Create a sanitized management escalation.",
}

ALLOWLISTED_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": model.model_json_schema(),
        },
    }
    for name, model in TOOL_ARGUMENT_MODELS.items()
]


def parse_tool_arguments(tool_name: str, raw_arguments: str) -> ToolArguments:
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    if model is None:
        raise ValueError("unsupported_tool")
    try:
        decoded = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_tool_arguments") from exc
    return TypeAdapter(model).validate_python(decoded)


async def _list_services(pool: Any, business_id: UUID, shop_id: UUID) -> ToolResult:
    async with pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select id, name, price_gross, duration_minutes
            from public.services
            where business_id = %s and shop_id = %s and active
            order by sort_order, id
            limit 100
            """,
            (business_id, shop_id),
        )
        rows = await cursor.fetchall()
    services = [
        {
            "id": str(row[0]),
            "name": str(row[1]),
            "price": str(Decimal(row[2]).quantize(Decimal("0.01"))),
            "duration_minutes": int(row[3]),
        }
        for row in rows
    ]
    rendered = "Services:\n" + "\n".join(
        f"• {item['name']} — AED {item['price']} ({item['duration_minutes']} min)"
        for item in services
    )
    return ToolResult(result_type="services", rendered=rendered, data={"items": services})


async def _shop_hours(
    pool: Any,
    business_id: UUID,
    shop_id: UUID,
    arguments: ShopHoursArguments,
) -> ToolResult:
    async with pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select iso_weekday, open_time, close_time, closes_next_day
            from public.shop_business_hours
            where business_id = %s and shop_id = %s and active
              and (%s::date is null or iso_weekday = extract(isodow from %s::date))
              and (%s::date is null or effective_from <= %s::date)
              and (%s::date is null or effective_until is null or effective_until >= %s::date)
            order by iso_weekday, effective_from desc, id
            """,
            (
                business_id,
                shop_id,
                arguments.day,
                arguments.day,
                arguments.day,
                arguments.day,
                arguments.day,
                arguments.day,
            ),
        )
        rows = await cursor.fetchall()
    hours = [
        {
            "weekday": int(row[0]),
            "open": str(row[1]),
            "close": str(row[2]),
            "closes_next_day": bool(row[3]),
        }
        for row in rows
    ]
    rendered = "Shop hours:\n" + "\n".join(
        f"• Day {item['weekday']}: {item['open']}–{item['close']}" for item in hours
    )
    return ToolResult(result_type="shop_hours", rendered=rendered, data={"items": hours})


async def _live_queue(pool: Any, business_id: UUID, shop_id: UUID) -> ToolResult:
    async with pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select count(*), min(estimated_start_at)
            from public.bookings
            where business_id = %s and shop_id = %s
              and status in ('requested', 'confirmed', 'in_service')
            """,
            (business_id, shop_id),
        )
        row = await cursor.fetchone()
    count = int(row[0]) if row is not None else 0
    estimate = row[1].isoformat() if row is not None and row[1] is not None else None
    rendered = f"There are {count} active queue bookings."
    if estimate is not None:
        rendered += f" The earliest current estimate is {estimate}."
    return ToolResult(
        result_type="live_queue",
        rendered=rendered,
        data={"active_count": count, "earliest_estimate": estimate},
    )


async def _my_booking(
    pool: Any,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
) -> ToolResult:
    async with pool.connection(timeout=5) as connection:
        cursor = await connection.execute(
            """
            select id, status::text, queue_number, scheduled_start, estimated_start_at
            from public.bookings
            where business_id = %s and shop_id = %s and customer_id = %s
              and status in ('held', 'requested', 'confirmed', 'in_service')
            order by created_at desc, id desc
            limit 1
            """,
            (business_id, shop_id, customer_id),
        )
        row = await cursor.fetchone()
    if row is None:
        return ToolResult(result_type="my_booking", rendered="You have no active booking.")
    data = {
        "booking_id": str(row[0]),
        "status": str(row[1]),
        "queue_number": row[2],
        "scheduled_start": row[3].isoformat() if row[3] is not None else None,
        "estimated_start": row[4].isoformat() if row[4] is not None else None,
    }
    rendered = f"Your booking status is {data['status']}."
    if data["queue_number"] is not None:
        rendered += f" Your queue token is {data['queue_number']}."
    if data["scheduled_start"] is not None:
        rendered += f" Scheduled start: {data['scheduled_start']}."
    return ToolResult(result_type="my_booking", rendered=rendered, data=data)


async def execute_allowlisted_tool(
    pool: Any,
    tool_name: str,
    arguments: ToolArguments,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
) -> ToolResult:
    if tool_name == "list_services" and isinstance(arguments, EmptyArguments):
        return await _list_services(pool, business_id, shop_id)
    if tool_name == "get_shop_hours" and isinstance(arguments, ShopHoursArguments):
        return await _shop_hours(pool, business_id, shop_id, arguments)
    if tool_name == "get_live_queue" and isinstance(arguments, EmptyArguments):
        return await _live_queue(pool, business_id, shop_id)
    if tool_name == "get_my_booking" and isinstance(arguments, EmptyArguments):
        return await _my_booking(pool, business_id, shop_id, customer_id)
    if tool_name in {
        "find_appointment_slots",
        "create_booking",
        "cancel_my_booking",
        "reschedule_my_booking",
        "escalate_to_management",
    }:
        return ToolResult(
            result_type="button_required",
            rendered="Please use the secure menu button to continue this action.",
        )
    raise ValueError("unsupported_tool")


__all__ = [
    "ALLOWLISTED_TOOLS",
    "TOOL_ARGUMENT_MODELS",
    "ToolArguments",
    "ToolResult",
    "execute_allowlisted_tool",
    "parse_tool_arguments",
]
