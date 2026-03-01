from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    solana_rpc_url: str = "https://api.devnet.solana.com"
    solana_keypair_path: str = "~/.config/solana/id.json"
    solana_keypair: str | None = None  # JSON array of bytes, used when keypair file isn't available (e.g. Railway)
    program_id: str = "TShUF8MeAKE46dz75je7KQEdAahdRQhS3vN7ffDoEds"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
