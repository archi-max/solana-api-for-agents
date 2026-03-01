from supabase import create_client, Client
from app.config import settings


def get_supabase() -> Client:
    """Create and return Supabase client using service role key."""
    return create_client(settings.supabase_url, settings.supabase_service_key)


# Global client instance
supabase: Client = get_supabase()
