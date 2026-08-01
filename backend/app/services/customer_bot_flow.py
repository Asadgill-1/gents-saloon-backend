import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.telegram import callback_data
from app.services.booking_service import (
    BookingCreateRequest,
    BookingRescheduleRequest,
    BookingTransitionRequest,
    create_booking,
    find_customer_appointment_slots,
    reschedule_booking,
    transition_booking,
)
from app.services.platform_operations import complete_idempotency, reserve_idempotency

SESSION_MAX_AGE = timedelta(minutes=15)
PAGE_SIZE = 8


class CustomerMenuExpiredError(Exception):
    """The callback no longer matches a live customer session."""


class CustomerFlowState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow: Literal["queue", "appointment", "reschedule", "cancel"]
    step: Literal[
        "services",
        "date",
        "barber",
        "slot",
        "confirm",
        "hold_confirm",
        "cancel_confirm",
        "processing",
    ]
    service_ids: list[UUID] = Field(default_factory=list, max_length=20)
    booking_id: UUID | None = None
    day: date | None = None
    barber_membership_id: UUID | None = None
    slot_start: datetime | None = None
    page: int = Field(default=0, ge=0, le=100)
    operation_id: str | None = Field(default=None, max_length=200)


class CustomerFlowResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    keyboard: InlineKeyboardMarkup | None = None
    language: Literal["en", "ar", "hi", "ur"]


class EscalationEvidence(BaseModel):
    category: Literal["reception"] = "reception"
    status: Literal["created"] = "created"


MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "choose_language": "Choose your language:",
        "welcome": "Welcome to Gents Saloon. Choose an option:",
        "choose_services": "Choose one or more services, then tap Done:",
        "choose_date": "Choose an appointment date:",
        "choose_barber": "Choose a barber:",
        "choose_slot": "Choose an available start time:",
        "no_services": "No services are currently available.",
        "no_barbers": "No barbers are currently available.",
        "no_slots": "No times are available for that selection. Choose another date or barber.",
        "need_service": "Select at least one service.",
        "confirm_queue": "Review your queue request and confirm:",
        "queue_created": "Your queue request was sent to reception.",
        "hold_created": "Your appointment is held temporarily. Confirm it before the hold expires.",
        "appointment_confirmed": "Your appointment is confirmed.",
        "cancel_prompt": "Cancel this booking?",
        "cancelled": "Your booking was cancelled.",
        "rescheduled": "Your appointment was rescheduled and the new time is held temporarily.",
        "no_booking": "You have no active booking at this shop.",
        "queue_empty": "You have no active queue booking.",
        "services": "Services and authoritative prices:",
        "escalated": "Reception has been notified with a sanitized help request.",
        "expired": "This menu expired. Start again.",
    },
    "ar": {
        "choose_language": "اختر لغتك:",
        "welcome": "أهلاً بك في صالون الرجال. اختر خدمة:",
        "choose_services": "اختر خدمة أو أكثر ثم اضغط تم:",
        "choose_date": "اختر تاريخ الموعد:",
        "choose_barber": "اختر الحلاق:",
        "choose_slot": "اختر وقت البدء المتاح:",
        "no_services": "لا توجد خدمات متاحة حالياً.",
        "no_barbers": "لا يوجد حلاقون متاحون حالياً.",
        "no_slots": "لا توجد أوقات متاحة لهذا الاختيار. اختر تاريخاً أو حلاقاً آخر.",
        "need_service": "اختر خدمة واحدة على الأقل.",
        "confirm_queue": "راجع طلب الدور ثم أكده:",
        "queue_created": "تم إرسال طلب الدور إلى الاستقبال.",
        "hold_created": "تم حجز الموعد مؤقتاً. أكده قبل انتهاء المهلة.",
        "appointment_confirmed": "تم تأكيد موعدك.",
        "cancel_prompt": "هل تريد إلغاء هذا الحجز؟",
        "cancelled": "تم إلغاء حجزك.",
        "rescheduled": "تم تغيير الموعد وحجز الوقت الجديد مؤقتاً.",
        "no_booking": "ليس لديك حجز نشط في هذا الفرع.",
        "queue_empty": "ليس لديك حجز دور نشط.",
        "services": "الخدمات والأسعار المعتمدة:",
        "escalated": "تم إشعار الاستقبال بطلب مساعدة مختصر.",
        "expired": "انتهت صلاحية هذه القائمة. ابدأ من جديد.",
    },
    "hi": {
        "choose_language": "अपनी भाषा चुनें:",
        "welcome": "जेंट्स सैलून में आपका स्वागत है। एक विकल्प चुनें:",
        "choose_services": "एक या अधिक सेवाएँ चुनें, फिर हो गया दबाएँ:",
        "choose_date": "अपॉइंटमेंट की तारीख चुनें:",
        "choose_barber": "बार्बर चुनें:",
        "choose_slot": "उपलब्ध समय चुनें:",
        "no_services": "अभी कोई सेवा उपलब्ध नहीं है।",
        "no_barbers": "अभी कोई बार्बर उपलब्ध नहीं है।",
        "no_slots": "इस चयन के लिए समय उपलब्ध नहीं है। दूसरी तारीख या बार्बर चुनें।",
        "need_service": "कम से कम एक सेवा चुनें।",
        "confirm_queue": "अपने कतार अनुरोध की समीक्षा करके पुष्टि करें:",
        "queue_created": "आपका कतार अनुरोध रिसेप्शन को भेज दिया गया है।",
        "hold_created": "आपका अपॉइंटमेंट अस्थायी रूप से होल्ड है। समय समाप्त होने से पहले पुष्टि करें।",
        "appointment_confirmed": "आपका अपॉइंटमेंट पक्का हो गया है।",
        "cancel_prompt": "क्या यह बुकिंग रद्द करनी है?",
        "cancelled": "आपकी बुकिंग रद्द कर दी गई है।",
        "rescheduled": "आपका अपॉइंटमेंट बदला गया और नया समय अस्थायी रूप से होल्ड है।",
        "no_booking": "इस दुकान में आपकी कोई सक्रिय बुकिंग नहीं है।",
        "queue_empty": "आपकी कोई सक्रिय कतार बुकिंग नहीं है।",
        "services": "सेवाएँ और अधिकृत कीमतें:",
        "escalated": "रिसेप्शन को सुरक्षित सहायता अनुरोध भेज दिया गया है।",
        "expired": "यह मेनू समाप्त हो गया है। फिर से शुरू करें।",
    },
    "ur": {
        "choose_language": "اپنی زبان منتخب کریں:",
        "welcome": "جینٹس سیلون میں خوش آمدید۔ ایک آپشن منتخب کریں:",
        "choose_services": "ایک یا زیادہ سروسز منتخب کریں، پھر مکمل دبائیں:",
        "choose_date": "اپائنٹمنٹ کی تاریخ منتخب کریں:",
        "choose_barber": "حجام منتخب کریں:",
        "choose_slot": "دستیاب وقت منتخب کریں:",
        "no_services": "فی الحال کوئی سروس دستیاب نہیں ہے۔",
        "no_barbers": "فی الحال کوئی حجام دستیاب نہیں ہے۔",
        "no_slots": "اس انتخاب کے لیے وقت دستیاب نہیں۔ دوسری تاریخ یا حجام منتخب کریں۔",
        "need_service": "کم از کم ایک سروس منتخب کریں۔",
        "confirm_queue": "اپنی قطار کی درخواست دیکھ کر تصدیق کریں:",
        "queue_created": "آپ کی قطار کی درخواست ریسپشن کو بھیج دی گئی ہے۔",
        "hold_created": "آپ کی اپائنٹمنٹ عارضی طور پر ہولڈ ہے۔ وقت ختم ہونے سے پہلے تصدیق کریں۔",
        "appointment_confirmed": "آپ کی اپائنٹمنٹ کنفرم ہو گئی ہے۔",
        "cancel_prompt": "کیا یہ بکنگ منسوخ کرنی ہے؟",
        "cancelled": "آپ کی بکنگ منسوخ کر دی گئی ہے۔",
        "rescheduled": "آپ کی اپائنٹمنٹ تبدیل ہو گئی اور نیا وقت عارضی طور پر ہولڈ ہے۔",
        "no_booking": "اس دکان میں آپ کی کوئی فعال بکنگ نہیں ہے۔",
        "queue_empty": "آپ کی کوئی فعال قطار بکنگ نہیں ہے۔",
        "services": "سروسز اور مستند قیمتیں:",
        "escalated": "ریسپشن کو محفوظ مدد کی درخواست بھیج دی گئی ہے۔",
        "expired": "اس مینو کی میعاد ختم ہو گئی۔ دوبارہ شروع کریں۔",
    },
}


MENU_LABELS: dict[str, tuple[tuple[tuple[str, str], ...], ...]] = {
    "en": (
        (("Book now", "c01"), ("Book appointment", "c02")),
        (("My booking", "c03"), ("Live queue", "c04")),
        (("Services & prices", "c05"), ("Talk to reception", "c06")),
        (("Language", "clang"),),
    ),
    "ar": (
        (("احجز الآن", "c01"), ("احجز موعداً", "c02")),
        (("حجزي", "c03"), ("الدور المباشر", "c04")),
        (("الخدمات والأسعار", "c05"), ("تحدث مع الاستقبال", "c06")),
        (("اللغة", "clang"),),
    ),
    "hi": (
        (("अभी बुक करें", "c01"), ("अपॉइंटमेंट बुक करें", "c02")),
        (("मेरी बुकिंग", "c03"), ("लाइव कतार", "c04")),
        (("सेवाएँ और कीमतें", "c05"), ("रिसेप्शन से बात करें", "c06")),
        (("भाषा", "clang"),),
    ),
    "ur": (
        (("ابھی بک کریں", "c01"), ("اپائنٹمنٹ بک کریں", "c02")),
        (("میری بکنگ", "c03"), ("لائیو قطار", "c04")),
        (("سروسز اور قیمتیں", "c05"), ("ریسپشن سے بات کریں", "c06")),
        (("زبان", "clang"),),
    ),
}

BUTTONS: dict[str, dict[str, str]] = {
    "en": {
        "done": "Done",
        "home": "Main menu",
        "any": "Any barber",
        "confirm": "Confirm",
        "cancel": "Cancel",
        "reschedule": "Reschedule",
        "date": "Choose date",
        "confirm_cancel": "Confirm cancel",
        "confirm_appointment": "Confirm appointment",
    },
    "ar": {
        "done": "تم",
        "home": "القائمة الرئيسية",
        "any": "أي حلاق",
        "confirm": "تأكيد",
        "cancel": "إلغاء",
        "reschedule": "تغيير الموعد",
        "date": "اختر التاريخ",
        "confirm_cancel": "تأكيد الإلغاء",
        "confirm_appointment": "تأكيد الموعد",
    },
    "hi": {
        "done": "हो गया",
        "home": "मुख्य मेनू",
        "any": "कोई भी बार्बर",
        "confirm": "पुष्टि करें",
        "cancel": "रद्द करें",
        "reschedule": "समय बदलें",
        "date": "तारीख चुनें",
        "confirm_cancel": "रद्द करने की पुष्टि",
        "confirm_appointment": "अपॉइंटमेंट की पुष्टि",
    },
    "ur": {
        "done": "مکمل",
        "home": "مرکزی مینو",
        "any": "کوئی بھی حجام",
        "confirm": "تصدیق",
        "cancel": "منسوخ",
        "reschedule": "وقت تبدیل کریں",
        "date": "تاریخ منتخب کریں",
        "confirm_cancel": "منسوخی کی تصدیق",
        "confirm_appointment": "اپائنٹمنٹ کی تصدیق",
    },
}


def _keyboard(rows: tuple[tuple[tuple[str, str], ...], ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=callback_data(action))
                for label, action in row
            ]
            for row in rows
        ]
    )


def customer_menu(language: str) -> InlineKeyboardMarkup:
    return _keyboard(MENU_LABELS[language])


def language_menu() -> InlineKeyboardMarkup:
    return _keyboard(
        (
            (("English", "len"), ("العربية", "lar")),
            (("हिन्दी", "lhi"), ("اردو", "lur")),
        )
    )


async def _require_customer(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
) -> str:
    cursor = await connection.execute(
        """
        select c.language::text
        from public.customers c
        where c.id = %s and c.business_id = %s and c.shop_id = %s
          and c.telegram_user_id = %s and c.blocked_at is null
          and c.anonymized_at is null
          and not exists (
            select 1 from public.telegram_user_blocks tub
            where tub.telegram_user_id = c.telegram_user_id
              and (tub.expires_at is null or tub.expires_at > now())
          )
        """,
        (customer_id, business_id, shop_id, telegram_user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CustomerMenuExpiredError
    return str(row[0])


async def _save_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    state: CustomerFlowState,
) -> None:
    await connection.execute(
        """
        insert into public.telegram_sessions (
          bot_id, business_id, shop_id, telegram_user_id, bot_role, state, payload
        ) values (%s, %s, %s, %s, 'customer', 'customer_booking', %s)
        on conflict (bot_id, telegram_user_id) do update
        set business_id = excluded.business_id, shop_id = excluded.shop_id,
            bot_role = excluded.bot_role, state = excluded.state,
            payload = excluded.payload, updated_at = now()
        """,
        (
            bot_id,
            business_id,
            shop_id,
            telegram_user_id,
            Jsonb(state.model_dump(mode="json")),
        ),
    )


async def _load_session(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
) -> CustomerFlowState:
    cursor = await connection.execute(
        """
        select payload
        from public.telegram_sessions
        where bot_id = %s and telegram_user_id = %s
          and business_id = %s and shop_id = %s and bot_role = 'customer'
          and state = 'customer_booking' and updated_at >= %s
        for update
        """,
        (
            bot_id,
            telegram_user_id,
            business_id,
            shop_id,
            datetime.now(UTC) - SESSION_MAX_AGE,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CustomerMenuExpiredError
    try:
        return CustomerFlowState.model_validate(row[0])
    except Exception as exc:
        raise CustomerMenuExpiredError from exc


async def _delete_session(connection: Any, *, bot_id: UUID, telegram_user_id: int) -> None:
    await connection.execute(
        "delete from public.telegram_sessions where bot_id = %s and telegram_user_id = %s",
        (bot_id, telegram_user_id),
    )


def _money(value: Decimal) -> str:
    return f"AED {value.quantize(Decimal('0.01'))}"


async def _service_rows(
    connection: Any, *, business_id: UUID, shop_id: UUID
) -> list[tuple[UUID, str, Decimal]]:
    cursor = await connection.execute(
        """
        select id, name, price_gross
        from public.services
        where business_id = %s and shop_id = %s and active
        order by sort_order, id
        limit 100
        """,
        (business_id, shop_id),
    )
    return [(UUID(str(row[0])), str(row[1]), Decimal(row[2])) for row in await cursor.fetchall()]


async def _barber_rows(
    connection: Any, *, business_id: UUID, shop_id: UUID
) -> list[tuple[UUID, str]]:
    cursor = await connection.execute(
        """
        select id, display_name
        from public.shop_memberships
        where business_id = %s and shop_id = %s and role = 'barber' and active
        order by display_name, id
        limit 100
        """,
        (business_id, shop_id),
    )
    return [(UUID(str(row[0])), str(row[1])) for row in await cursor.fetchall()]


def _paged_keyboard(
    rows: list[tuple[str, str]],
    *,
    page: int,
    language: str,
    done_action: str | None = None,
) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    visible = rows[start : start + PAGE_SIZE]
    keyboard_rows: list[tuple[tuple[str, str], ...]] = [
        ((label, action),) for label, action in visible
    ]
    navigation: list[tuple[str, str]] = []
    if page > 0:
        navigation.append(("‹", "pgprev"))
    if start + PAGE_SIZE < len(rows):
        navigation.append(("›", "pgnext"))
    if navigation:
        keyboard_rows.append(tuple(navigation))
    if done_action is not None:
        keyboard_rows.append(((BUTTONS[language]["done"], done_action),))
    keyboard_rows.append(((BUTTONS[language]["home"], "home"),))
    return _keyboard(tuple(keyboard_rows))


async def _render_services(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    state: CustomerFlowState,
    language: str,
) -> CustomerFlowResponse:
    services = await _service_rows(connection, business_id=business_id, shop_id=shop_id)
    if not services:
        return CustomerFlowResponse(
            text=MESSAGES[language]["no_services"],
            keyboard=customer_menu(language),
            language=language,
        )
    selected = set(state.service_ids)
    buttons = [
        (
            f"{'✓ ' if service_id in selected else ''}{name} — {_money(price)}",
            f"svc{index}",
        )
        for index, (service_id, name, price) in enumerate(services)
    ]
    return CustomerFlowResponse(
        text=MESSAGES[language]["choose_services"],
        keyboard=_paged_keyboard(
            buttons, page=state.page, language=language, done_action="svcdone"
        ),
        language=language,
    )


async def _render_barbers(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    state: CustomerFlowState,
    language: str,
) -> CustomerFlowResponse:
    barbers = await _barber_rows(connection, business_id=business_id, shop_id=shop_id)
    if not barbers:
        return CustomerFlowResponse(
            text=MESSAGES[language]["no_barbers"],
            keyboard=customer_menu(language),
            language=language,
        )
    buttons = [(name, f"bar{index}") for index, (_barber_id, name) in enumerate(barbers)]
    buttons.insert(0, (BUTTONS[language]["any"], "barany"))
    return CustomerFlowResponse(
        text=MESSAGES[language]["choose_barber"],
        keyboard=_paged_keyboard(buttons, page=state.page, language=language),
        language=language,
    )


async def _shop_today(connection: Any, *, business_id: UUID, shop_id: UUID) -> date:
    cursor = await connection.execute(
        "select timezone from public.shops where id = %s and business_id = %s",
        (shop_id, business_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise CustomerMenuExpiredError
    try:
        timezone = ZoneInfo(str(row[0]))
    except ZoneInfoNotFoundError as exc:
        raise CustomerMenuExpiredError from exc
    return datetime.now(UTC).astimezone(timezone).date()


def _date_response(today: date, language: str) -> CustomerFlowResponse:
    rows = tuple(
        ((today + timedelta(days=offset)).isoformat(), f"day{offset}") for offset in range(14)
    )
    return CustomerFlowResponse(
        text=MESSAGES[language]["choose_date"],
        keyboard=_keyboard(
            tuple(((label, action),) for label, action in rows)
            + (((BUTTONS[language]["home"], "home"),),)
        ),
        language=language,
    )


async def _booking_text(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    queue_only: bool = False,
) -> tuple[str, UUID | None, str | None]:
    cursor = await connection.execute(
        """
        select b.id, b.booking_type::text, b.status::text, b.queue_number,
               b.scheduled_start, b.estimated_start_at, sm.display_name,
               coalesce(string_agg(bs.service_name, ', ' order by bs.sort_order), '')
        from public.bookings b
        join public.shop_memberships sm
          on sm.id = b.barber_membership_id and sm.business_id = b.business_id
         and sm.shop_id = b.shop_id
        left join public.booking_services bs
          on bs.booking_id = b.id and bs.business_id = b.business_id and bs.shop_id = b.shop_id
        where b.business_id = %s and b.shop_id = %s and b.customer_id = %s
          and b.status in ('held', 'requested', 'confirmed', 'in_service')
          and (not %s or b.booking_type <> 'appointment')
        group by b.id, sm.display_name
        order by b.created_at desc, b.id desc
        limit 1
        """,
        (business_id, shop_id, customer_id, queue_only),
    )
    row = await cursor.fetchone()
    if row is None:
        return "", None, None
    when = row[4] or row[5]
    lines = [f"Status: {row[2]}", f"Services: {row[7]}", f"Barber: {row[6]}"]
    if row[3] is not None:
        lines.append(f"Queue token: {row[3]}")
    if when is not None:
        lines.append(f"Time: {when.isoformat()}")
    return "\n".join(lines), UUID(str(row[0])), str(row[1])


async def _set_language(
    connection: Any,
    *,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    language: str,
) -> None:
    cursor = await connection.execute(
        """
        update public.customers
        set language = %s, updated_at = now()
        where id = %s and business_id = %s and shop_id = %s
          and telegram_user_id = %s and blocked_at is null and anonymized_at is null
        returning id
        """,
        (language, customer_id, business_id, shop_id, telegram_user_id),
    )
    if await cursor.fetchone() is None:
        raise CustomerMenuExpiredError


async def _start_flow(
    connection: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    telegram_user_id: int,
    flow: Literal["queue", "appointment"],
    language: str,
) -> CustomerFlowResponse:
    state = CustomerFlowState(flow=flow, step="services")
    await _save_session(
        connection,
        bot_id=bot_id,
        business_id=business_id,
        shop_id=shop_id,
        telegram_user_id=telegram_user_id,
        state=state,
    )
    return await _render_services(
        connection,
        business_id=business_id,
        shop_id=shop_id,
        state=state,
        language=language,
    )


async def _direct_action(
    connection: Any,
    *,
    action: str,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    language: str,
    request_id: str,
) -> CustomerFlowResponse | None:
    if action == "clang":
        return CustomerFlowResponse(
            text=MESSAGES[language]["choose_language"],
            keyboard=language_menu(),
            language=language,
        )
    if action in {"len", "lar", "lhi", "lur"}:
        selected = {"len": "en", "lar": "ar", "lhi": "hi", "lur": "ur"}[action]
        await _set_language(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
            language=selected,
        )
        return CustomerFlowResponse(
            text=MESSAGES[selected]["welcome"],
            keyboard=customer_menu(selected),
            language=selected,
        )
    if action == "home":
        await _delete_session(connection, bot_id=bot_id, telegram_user_id=telegram_user_id)
        return CustomerFlowResponse(
            text=MESSAGES[language]["welcome"],
            keyboard=customer_menu(language),
            language=language,
        )
    if action == "c01":
        return await _start_flow(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            flow="queue",
            language=language,
        )
    if action == "c02":
        return await _start_flow(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            flow="appointment",
            language=language,
        )
    if action in {"c03", "c04"}:
        text, booking_id, booking_type = await _booking_text(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            queue_only=action == "c04",
        )
        if booking_id is None:
            key = "queue_empty" if action == "c04" else "no_booking"
            return CustomerFlowResponse(
                text=MESSAGES[language][key],
                keyboard=customer_menu(language),
                language=language,
            )
        actions: list[tuple[str, str]] = [(BUTTONS[language]["cancel"], "bookcancel")]
        if booking_type == "appointment":
            actions.append((BUTTONS[language]["reschedule"], "bookresched"))
        return CustomerFlowResponse(
            text=text,
            keyboard=_keyboard((tuple(actions), ((BUTTONS[language]["home"], "home"),))),
            language=language,
        )
    if action == "c05":
        services = await _service_rows(connection, business_id=business_id, shop_id=shop_id)
        text = MESSAGES[language]["services"]
        if services:
            text += "\n" + "\n".join(f"• {name} — {_money(price)}" for _, name, price in services)
        else:
            text = MESSAGES[language]["no_services"]
        return CustomerFlowResponse(
            text=text,
            keyboard=customer_menu(language),
            language=language,
        )
    if action == "c06":
        evidence = EscalationEvidence()
        key = "telegram:" + hashlib.sha256(request_id.encode()).hexdigest()
        replay = await reserve_idempotency(
            connection,
            scope=f"telegram.escalation:{shop_id}",
            actor_id=customer_id,
            key=key,
            payload=evidence,
            expected_status=201,
        )
        if replay is not None:
            EscalationEvidence.model_validate(replay)
            return CustomerFlowResponse(
                text=MESSAGES[language]["escalated"],
                keyboard=customer_menu(language),
                language=language,
            )
        dedupe = f"telegram:escalation:{bot_id}:{key}"
        await connection.execute(
            """
            insert into public.outbox_events (
              business_id, shop_id, topic, dedupe_key, payload
            ) values (%s, %s, 'telegram.escalation', %s, %s)
            on conflict (dedupe_key) do nothing
            """,
            (
                business_id,
                shop_id,
                dedupe,
                Jsonb({"customer_id": str(customer_id), "category": "reception"}),
            ),
        )
        await connection.execute(
            """
            insert into public.audit_log (
              business_id, shop_id, actor_type, actor_id, action,
              entity_type, entity_id, request_id, after
            ) values (%s, %s, 'telegram_user', %s, 'telegram.escalation.created',
                      'customer', %s, %s, %s)
            """,
            (
                business_id,
                shop_id,
                str(telegram_user_id),
                customer_id,
                request_id,
                Jsonb({"category": "reception"}),
            ),
        )
        await complete_idempotency(
            connection,
            scope=f"telegram.escalation:{shop_id}",
            actor_id=customer_id,
            key=key,
            response_status=201,
            response=evidence,
        )
        return CustomerFlowResponse(
            text=MESSAGES[language]["escalated"],
            keyboard=customer_menu(language),
            language=language,
        )
    return None


async def _begin_booking_action(
    connection: Any,
    *,
    action: str,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    state: CustomerFlowState,
    language: str,
    request_id: str,
) -> CustomerFlowResponse | CustomerFlowState:
    if action in {"pgprev", "pgnext"}:
        state.page = max(0, state.page + (-1 if action == "pgprev" else 1))
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        if state.step == "services":
            return await _render_services(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                state=state,
                language=language,
            )
        if state.step == "barber":
            return await _render_barbers(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                state=state,
                language=language,
            )
        if state.step == "slot":
            return state
    if action.startswith("svc") and action[3:].isdigit() and state.step == "services":
        services = await _service_rows(connection, business_id=business_id, shop_id=shop_id)
        index = int(action[3:])
        if index >= len(services):
            raise CustomerMenuExpiredError
        selected = services[index][0]
        state.service_ids = (
            [item for item in state.service_ids if item != selected]
            if selected in state.service_ids
            else [*state.service_ids, selected]
        )
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        return await _render_services(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            state=state,
            language=language,
        )
    if action == "svcdone" and state.step == "services":
        if not state.service_ids:
            return CustomerFlowResponse(
                text=MESSAGES[language]["need_service"],
                keyboard=(
                    await _render_services(
                        connection,
                        business_id=business_id,
                        shop_id=shop_id,
                        state=state,
                        language=language,
                    )
                ).keyboard,
                language=language,
            )
        state.step = "barber" if state.flow == "queue" else "date"
        state.page = 0
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        if state.step == "date":
            today = await _shop_today(connection, business_id=business_id, shop_id=shop_id)
            return _date_response(today, language)
        return await _render_barbers(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            state=state,
            language=language,
        )
    if action.startswith("day") and action[3:].isdigit() and state.step == "date":
        offset = int(action[3:])
        if offset > 13:
            raise CustomerMenuExpiredError
        today = await _shop_today(connection, business_id=business_id, shop_id=shop_id)
        state.day = today + timedelta(days=offset)
        state.step = "barber"
        state.page = 0
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        return await _render_barbers(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            state=state,
            language=language,
        )
    if state.step == "barber" and (
        action == "barany" or (action.startswith("bar") and action[3:].isdigit())
    ):
        if action == "barany":
            state.barber_membership_id = None
        else:
            barbers = await _barber_rows(connection, business_id=business_id, shop_id=shop_id)
            index = int(action[3:])
            if index >= len(barbers):
                raise CustomerMenuExpiredError
            state.barber_membership_id = barbers[index][0]
        state.page = 0
        if state.flow == "queue":
            state.step = "confirm"
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            services = await _service_rows(connection, business_id=business_id, shop_id=shop_id)
            chosen = [row for row in services if row[0] in state.service_ids]
            total = sum((row[2] for row in chosen), Decimal(0))
            text = MESSAGES[language]["confirm_queue"] + "\n"
            text += ", ".join(row[1] for row in chosen) + f"\nTotal: {_money(total)}"
            return CustomerFlowResponse(
                text=text,
                keyboard=_keyboard(
                    (
                        ((BUTTONS[language]["confirm"], "confirm"),),
                        ((BUTTONS[language]["home"], "home"),),
                    )
                ),
                language=language,
            )
        state.step = "slot"
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        return state
    if action == "bookcancel":
        _text, booking_id, _booking_type = await _booking_text(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
        )
        if booking_id is None:
            raise CustomerMenuExpiredError
        state = CustomerFlowState(flow="cancel", step="cancel_confirm", booking_id=booking_id)
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        return CustomerFlowResponse(
            text=MESSAGES[language]["cancel_prompt"],
            keyboard=_keyboard(
                (
                    ((BUTTONS[language]["confirm_cancel"], "cancelok"),),
                    ((BUTTONS[language]["home"], "home"),),
                )
            ),
            language=language,
        )
    if action == "bookresched":
        _text, booking_id, booking_type = await _booking_text(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
        )
        if booking_id is None or booking_type != "appointment":
            raise CustomerMenuExpiredError
        cursor = await connection.execute(
            """
            select service_id from public.booking_services
            where booking_id = %s and business_id = %s and shop_id = %s
            order by sort_order
            """,
            (booking_id, business_id, shop_id),
        )
        service_ids = [UUID(str(row[0])) for row in await cursor.fetchall()]
        state = CustomerFlowState(
            flow="reschedule", step="date", booking_id=booking_id, service_ids=service_ids
        )
        await _save_session(
            connection,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            telegram_user_id=telegram_user_id,
            state=state,
        )
        today = await _shop_today(connection, business_id=business_id, shop_id=shop_id)
        return _date_response(today, language)
    if action in {"confirm", "cancelok", "holdok"}:
        expected = {"confirm": "confirm", "cancelok": "cancel_confirm", "holdok": "hold_confirm"}[
            action
        ]
        if state.step == expected:
            state.step = "processing"
            state.operation_id = request_id
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            return state
        if state.step == "processing" and state.operation_id == request_id:
            return state
    raise CustomerMenuExpiredError


async def _render_slots(
    pool: Any,
    *,
    state: CustomerFlowState,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    language: str,
) -> CustomerFlowResponse:
    if state.day is None:
        raise CustomerMenuExpiredError
    slots = await find_customer_appointment_slots(
        pool,
        business_id=business_id,
        shop_id=shop_id,
        customer_id=customer_id,
        telegram_user_id=telegram_user_id,
        service_ids=state.service_ids,
        day=state.day,
        barber_membership_id=state.barber_membership_id,
        limit=48,
    )
    if not slots:
        return CustomerFlowResponse(
            text=MESSAGES[language]["no_slots"],
            keyboard=_keyboard(
                (
                    ((BUTTONS[language]["date"], "dateagain"),),
                    ((BUTTONS[language]["home"], "home"),),
                )
            ),
            language=language,
        )
    rows = [(slot.starts_at.isoformat(), f"slot{index}") for index, slot in enumerate(slots)]
    return CustomerFlowResponse(
        text=MESSAGES[language]["choose_slot"],
        keyboard=_paged_keyboard(rows, page=state.page, language=language),
        language=language,
    )


async def _complete_operation(
    pool: Any,
    *,
    state: CustomerFlowState,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    language: str,
    request_id: str,
) -> CustomerFlowResponse:
    key = f"telegram:{bot_id}:{request_id}"
    if state.flow == "queue":
        result = await create_booking(
            pool,
            actor_id=None,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=key,
            request_id=request_id,
            payload=BookingCreateRequest(
                booking_type="queue",
                customer_id=customer_id,
                barber_membership_id=state.barber_membership_id,
                service_ids=state.service_ids,
            ),
            telegram_user_id=telegram_user_id,
        )
        text = MESSAGES[language]["queue_created"]
        if result.queue_number is not None:
            text += f" Token: {result.queue_number}."
    elif state.flow == "cancel" and state.booking_id is not None:
        await transition_booking(
            pool,
            actor_id=None,
            business_id=business_id,
            shop_id=shop_id,
            booking_id=state.booking_id,
            target_status="cancelled",
            idempotency_key=key,
            request_id=request_id,
            payload=BookingTransitionRequest(reason="customer requested"),
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
        )
        text = MESSAGES[language]["cancelled"]
    elif state.flow == "appointment" and state.booking_id is not None:
        await transition_booking(
            pool,
            actor_id=None,
            business_id=business_id,
            shop_id=shop_id,
            booking_id=state.booking_id,
            target_status="confirmed",
            idempotency_key=key,
            request_id=request_id,
            payload=BookingTransitionRequest(reason="customer confirmed hold"),
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
        )
        text = MESSAGES[language]["appointment_confirmed"]
    else:
        raise CustomerMenuExpiredError
    async with pool.connection(timeout=5) as connection, connection.transaction():
        await _require_customer(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
        )
        await _delete_session(connection, bot_id=bot_id, telegram_user_id=telegram_user_id)
    return CustomerFlowResponse(
        text=text,
        keyboard=customer_menu(language),
        language=language,
    )


async def handle_customer_callback(
    pool: Any,
    *,
    bot_id: UUID,
    business_id: UUID,
    shop_id: UUID,
    customer_id: UUID,
    telegram_user_id: int,
    callback: str,
    request_id: str,
) -> CustomerFlowResponse:
    if not callback.startswith("v1."):
        raise CustomerMenuExpiredError
    action = callback[3:]
    state_result: CustomerFlowState | None = None
    language = "en"
    async with pool.connection(timeout=5) as connection, connection.transaction():
        language = await _require_customer(
            connection,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
        )
        direct = await _direct_action(
            connection,
            action=action,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
            language=language,
            request_id=request_id,
        )
        if direct is not None:
            return direct
        state = (
            CustomerFlowState(flow="cancel", step="cancel_confirm")
            if action in {"bookcancel", "bookresched"}
            else await _load_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
            )
        )
        if action == "dateagain" and state.step == "slot":
            state.step = "date"
            state.day = None
            state.barber_membership_id = None
            state.page = 0
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            today = await _shop_today(connection, business_id=business_id, shop_id=shop_id)
            return _date_response(today, language)
        if action.startswith("slot") and action[4:].isdigit() and state.step == "slot":
            if state.operation_id not in {None, request_id}:
                raise CustomerMenuExpiredError
            state.operation_id = request_id
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state,
            )
            state_result = state
        else:
            result = await _begin_booking_action(
                connection,
                action=action,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                customer_id=customer_id,
                telegram_user_id=telegram_user_id,
                state=state,
                language=language,
                request_id=request_id,
            )
            if isinstance(result, CustomerFlowResponse):
                return result
            state_result = result

    assert state_result is not None
    if state_result.step == "slot" and not (action.startswith("slot") and action[4:].isdigit()):
        return await _render_slots(
            pool,
            state=state_result,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
            language=language,
        )
    if state_result.step == "slot":
        if state_result.day is None:
            raise CustomerMenuExpiredError
        if state_result.slot_start is None:
            slots = await find_customer_appointment_slots(
                pool,
                business_id=business_id,
                shop_id=shop_id,
                customer_id=customer_id,
                telegram_user_id=telegram_user_id,
                service_ids=state_result.service_ids,
                day=state_result.day,
                barber_membership_id=state_result.barber_membership_id,
                limit=48,
            )
            index = int(action[4:])
            if index >= len(slots):
                raise CustomerMenuExpiredError
            state_result.slot_start = slots[index].starts_at
            state_result.barber_membership_id = slots[index].barber_membership_id
            async with pool.connection(timeout=5) as connection, connection.transaction():
                await _require_customer(
                    connection,
                    business_id=business_id,
                    shop_id=shop_id,
                    customer_id=customer_id,
                    telegram_user_id=telegram_user_id,
                )
                await _save_session(
                    connection,
                    bot_id=bot_id,
                    business_id=business_id,
                    shop_id=shop_id,
                    telegram_user_id=telegram_user_id,
                    state=state_result,
                )
        if state_result.flow == "reschedule" and state_result.booking_id is not None:
            state_result.step = "processing"
            state_result.operation_id = request_id
            await reschedule_booking(
                pool,
                actor_id=None,
                business_id=business_id,
                shop_id=shop_id,
                booking_id=state_result.booking_id,
                idempotency_key=f"telegram:{bot_id}:{request_id}",
                request_id=request_id,
                payload=BookingRescheduleRequest(scheduled_start=state_result.slot_start),
                customer_id=customer_id,
                telegram_user_id=telegram_user_id,
            )
            text = MESSAGES[language]["rescheduled"]
            async with pool.connection(timeout=5) as connection, connection.transaction():
                await _delete_session(connection, bot_id=bot_id, telegram_user_id=telegram_user_id)
            return CustomerFlowResponse(
                text=text,
                keyboard=customer_menu(language),
                language=language,
            )
        held = await create_booking(
            pool,
            actor_id=None,
            business_id=business_id,
            shop_id=shop_id,
            idempotency_key=f"telegram:{bot_id}:{request_id}",
            request_id=request_id,
            payload=BookingCreateRequest(
                booking_type="appointment",
                customer_id=customer_id,
                barber_membership_id=state_result.barber_membership_id,
                service_ids=state_result.service_ids,
                scheduled_start=state_result.slot_start,
            ),
            telegram_user_id=telegram_user_id,
        )
        state_result.booking_id = held.booking_id
        state_result.step = "hold_confirm"
        state_result.operation_id = None
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await _require_customer(
                connection,
                business_id=business_id,
                shop_id=shop_id,
                customer_id=customer_id,
                telegram_user_id=telegram_user_id,
            )
            await _save_session(
                connection,
                bot_id=bot_id,
                business_id=business_id,
                shop_id=shop_id,
                telegram_user_id=telegram_user_id,
                state=state_result,
            )
        text = MESSAGES[language]["hold_created"]
        if held.hold_expires_at is not None:
            text += f" {held.hold_expires_at.isoformat()}"
        return CustomerFlowResponse(
            text=text,
            keyboard=_keyboard(
                (
                    ((BUTTONS[language]["confirm_appointment"], "holdok"),),
                    ((BUTTONS[language]["home"], "home"),),
                )
            ),
            language=language,
        )
    if state_result.step == "processing":
        return await _complete_operation(
            pool,
            state=state_result,
            bot_id=bot_id,
            business_id=business_id,
            shop_id=shop_id,
            customer_id=customer_id,
            telegram_user_id=telegram_user_id,
            language=language,
            request_id=request_id,
        )
    return await _render_slots(
        pool,
        state=state_result,
        business_id=business_id,
        shop_id=shop_id,
        customer_id=customer_id,
        telegram_user_id=telegram_user_id,
        language=language,
    )


__all__ = [
    "BUTTONS",
    "CustomerFlowResponse",
    "CustomerMenuExpiredError",
    "MESSAGES",
    "MENU_LABELS",
    "customer_menu",
    "handle_customer_callback",
    "language_menu",
]
