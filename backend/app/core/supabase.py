from supabase import Client, create_client

from app.core.config import Settings


def create_supabase_admin(settings: Settings) -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key.get_secret_value():
        raise RuntimeError("Supabase admin settings are not configured")
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key.get_secret_value(),
    )
