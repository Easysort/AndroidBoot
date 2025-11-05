from datetime import datetime, timezone, timedelta
from applications.common.env import Env

class Helper:
    @staticmethod
    def current_time(zone: timezone = Env.TIMEZONE) -> datetime: return datetime.now(zone)