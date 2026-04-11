import os

# telegram bot
MI_VPN_BOT_LOG_LEVEL = "MI_VPN_BOT_LOG_LEVEL"
MI_VPN_BOT_TOKEN = "MI_VPN_BOT_TOKEN"
MI_VPN_BOT_TRIAL_PERIOD_DAYS = "MI_VPN_BOT_TRIAL_PERIOD_DAYS"
MI_VPN_BOT_ADMINS = "MI_VPN_BOT_ADMINS"
MI_VPN_BOT_BANNED = "MI_VPN_BOT_BANNED"
MI_VPN_BOT_REDIRECT_URL = "MI_VPN_BOT_REDIRECT_URL"
MI_VPN_BOT_INTERNAL_SQUADS_UUIDS = "MI_VPN_BOT_INTERNAL_SQUADS_UUIDS"
MI_VPN_BOT_REDIS_QUEUE_NAME = "MI_VPN_BOT_REDIS_QUEUE_NAME"
MI_VPN_TW_ENABLED = "MI_VPN_TW_ENABLED"
MI_VPN_BOT_REFERRAL_BONUS = "MI_VPN_BOT_REFERRAL_BONUS"
MI_VPN_BOT_REFERRER_BONUS = "MI_VPN_BOT_REFERRER_BONUS"
MI_VPN_BOT_PROXY_URL = "MI_VPN_BOT_PROXY_URL"

# mbms
MI_VPN_BOT_RWMS_ADDR = "MI_VPN_BOT_RWMS_ADDR"
MI_VPN_BOT_RWMS_PORT = "MI_VPN_BOT_RWMS_PORT"

# rwms
MI_VPN_BOT_RWMS_ADDR = "MI_VPN_BOT_RWMS_ADDR"
MI_VPN_BOT_RWMS_PORT = "MI_VPN_BOT_RWMS_PORT"

# yookassa
MI_VPN_BOT_SHOP_ID = "MI_VPN_BOT_SHOP_ID"
MI_VPN_BOT_SECRET = "MI_VPN_BOT_SECRET"

# redis
MI_VPN_BOT_REDIS_HOST = "MI_VPN_BOT_REDIS_HOST"
MI_VPN_BOT_REDIS_PORT = "MI_VPN_BOT_REDIS_PORT"
MI_VPN_BOT_REDIS_PASSWORD = "MI_VPN_BOT_REDIS_PASSWORD"

# postgres
MI_VPN_BOT_POSTGRES_HOST = "MI_VPN_BOT_POSTGRES_HOST"
MI_VPN_BOT_POSTGRES_PORT = "MI_VPN_BOT_POSTGRES_PORT"
MI_VPN_BOT_POSTGRES_USER = "MI_VPN_BOT_POSTGRES_USER"
MI_VPN_BOT_POSTGRES_PASSWORD = "MI_VPN_BOT_POSTGRES_PASSWORD"
MI_VPN_BOT_POSTGRES_DB = "MI_VPN_BOT_POSTGRES_DB"


class Config:
    def __init__(self):
        # telegram bot envs
        self.technical_work_enabled: bool = (
            os.getenv(MI_VPN_TW_ENABLED, "false").lower() == "true"
        )

        self.redirect_url = self.__read_required_str_env(MI_VPN_BOT_REDIRECT_URL)

        admins_value = os.getenv(MI_VPN_BOT_ADMINS, "")

        self.admins = []
        if admins_value:
            for squad_uuid in admins_value.split(","):
                squad_uuid = squad_uuid.strip()
                if squad_uuid:
                    try:
                        self.admins.append(int(squad_uuid))
                    except ValueError:
                        ...

        banned_list = os.getenv(MI_VPN_BOT_BANNED, "")

        self.banned = []
        if banned_list:
            for banned in banned_list.split(","):
                banned = banned.strip()
                if banned:
                    try:
                        self.banned.append(int(banned))
                    except ValueError:
                        ...

        self.log_level: str = os.getenv(MI_VPN_BOT_LOG_LEVEL, "info")
        self.bot_token: str = self.__read_required_str_env(MI_VPN_BOT_TOKEN)
        self.proxy_url: str | None = os.getenv(MI_VPN_BOT_PROXY_URL)

        self.trial_period_days: int = self.__read_int_env(
            MI_VPN_BOT_TRIAL_PERIOD_DAYS, "7"
        )

        squads_uuids_value = os.getenv(MI_VPN_BOT_INTERNAL_SQUADS_UUIDS, "")
        self.squads_uuids = []
        if squads_uuids_value:
            for squad_uuid in squads_uuids_value.split(","):
                squad_uuid = squad_uuid.strip()
                if squad_uuid:
                    try:
                        self.squads_uuids.append(squad_uuid)
                    except ValueError:
                        ...

        self.redis_queue_name: str = self.__read_required_str_env(
            MI_VPN_BOT_REDIS_QUEUE_NAME
        )

        self.referrer_bonus_days: int = self.__read_int_env(
            MI_VPN_BOT_REFERRER_BONUS, "10"
        )
        self.referral_bonus_days: int = self.__read_int_env(
            MI_VPN_BOT_REFERRAL_BONUS, "15"
        )

        # mbms envs
        self.mbms_address: str = self.__read_required_str_env(MI_VPN_BOT_RWMS_ADDR)
        self.mbms_port: int = self.__read_required_int_env(MI_VPN_BOT_RWMS_PORT)

        # rwms envs
        self.rwms_address: str = self.__read_required_str_env(MI_VPN_BOT_RWMS_ADDR)
        self.rwms_port: int = self.__read_required_int_env(MI_VPN_BOT_RWMS_PORT)

        # yookassa envs
        self.shop_id: str = self.__read_required_str_env(MI_VPN_BOT_SHOP_ID)
        self.secret: str = self.__read_required_str_env(MI_VPN_BOT_SECRET)

        # redis envs
        self.redis_host: str = self.__read_required_str_env(MI_VPN_BOT_REDIS_HOST)
        self.redis_port: int = self.__read_required_int_env(MI_VPN_BOT_REDIS_PORT)
        self.redis_password: str = self.__read_required_str_env(
            MI_VPN_BOT_REDIS_PASSWORD
        )

        # postgres envs
        self.pg_host: str = self.__read_required_str_env(MI_VPN_BOT_POSTGRES_HOST)
        self.pg_port: int = self.__read_required_int_env(MI_VPN_BOT_POSTGRES_PORT)
        self.pg_user: str = self.__read_required_str_env(MI_VPN_BOT_POSTGRES_USER)
        self.pg_password: str = self.__read_required_str_env(
            MI_VPN_BOT_POSTGRES_PASSWORD
        )
        self.pg_db: str = self.__read_required_str_env(MI_VPN_BOT_POSTGRES_DB)

    def __read_int_env(self, name: str, default) -> int:
        value = os.getenv(name, default)

        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got {value!r}")

    def __read_required_int_env(self, name: str) -> int:
        value = os.getenv(name)

        if value is None:
            raise ValueError(f"{name} environment variable is not set.")

        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got {value!r}")

    def __read_required_str_env(self, name: str) -> str:
        value = os.getenv(name)

        if value is None:
            raise ValueError(f"{name} environment variable is not set.")

        return value
