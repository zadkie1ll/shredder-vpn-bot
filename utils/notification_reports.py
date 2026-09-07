from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DAILY_REPORT_HOUR = 19
DAILY_REPORT_MINUTE = 0


def seconds_until_next_daily_report(now: datetime | None = None) -> float:
    if now is None:
        now = datetime.now(MOSCOW_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW_TZ)
    else:
        now = now.astimezone(MOSCOW_TZ)

    next_report = now.replace(
        hour=DAILY_REPORT_HOUR,
        minute=DAILY_REPORT_MINUTE,
        second=0,
        microsecond=0,
    )
    if next_report <= now:
        next_report += timedelta(days=1)

    return (next_report - now).total_seconds()


def format_notification_report(stats: dict[str, int], bot_instance_id: str) -> str:
    total = stats.get("total", 0)
    sent = stats.get("status:sent", 0)
    undeliverable = stats.get("status:undeliverable", 0)
    failed = stats.get("status:failed", 0)
    skipped = sum(
        value
        for key, value in stats.items()
        if key.startswith("status:skipped_") or key == "status:unknown_type"
    )

    lines = [
        "📬 <b>Ежедневный отчет по уведомлениям</b>",
        f"Бот: <code>{bot_instance_id}</code>",
        "Период: с прошлого отчета до 19:00 МСК",
        "",
        f"Всего обработано: <b>{total}</b>",
        f"Отправлено: <b>{sent}</b>",
        f"Недоставляемые чаты: <b>{undeliverable}</b>",
        f"Ошибки отправки: <b>{failed}</b>",
        f"Пропущено: <b>{skipped}</b>",
    ]

    type_rows = sorted(
        (
            (key.removeprefix("type:"), value)
            for key, value in stats.items()
            if key.startswith("type:")
        ),
        key=lambda item: (-item[1], item[0]),
    )

    if type_rows:
        lines.extend(["", "<b>По типам:</b>"])
        for notification_type, count in type_rows[:12]:
            lines.append(f"- <code>{notification_type}</code>: {count}")

    return "\n".join(lines)
