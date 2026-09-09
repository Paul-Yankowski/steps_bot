"""
Ежедневный дайджест марафона: лидерборд по сумме шагов за все дни
(по умолчанию — по вчерашний день включительно) с диаграммой.

Использование:
  - планово вызывается из bot_old.py через JobQueue раз в сутки;
  - вручную — командой /digest в чате (см. bot_old.py).
"""

import io
import logging
from collections import defaultdict
from datetime import date, timedelta

import matplotlib

matplotlib.use("Agg")  # без GUI — нужно для работы на сервере
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


def compute_totals(sheet, upto_date_str: str):
    """
    Возвращает список (имя, сумма_шагов), отсортированный по убыванию,
    суммируя все записи из таблицы с датой <= upto_date_str.
    """
    records = sheet.ws.get_all_values()[1:]  # пропускаем заголовок
    totals = defaultdict(int)

    for row in records:
        if len(row) < 3:
            continue
        row_date, name, steps_str = row[0], row[1], row[2]
        if not row_date or not name or row_date > upto_date_str:
            continue
        if not steps_str.isdigit():
            continue
        totals[name] += int(steps_str)

    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def build_chart(totals, title: str) -> bytes:
    """Рисует горизонтальную столбчатую диаграмму лидерборда, возвращает PNG-байты."""
    if not totals:
        totals = [("Пока нет данных", 0)]

    # переворачиваем, чтобы лидер отображался сверху (barh рисует снизу вверх)
    names = [t[0] for t in totals][::-1]
    values = [t[1] for t in totals][::-1]

    fig_height = max(3, 0.55 * len(names) + 1.2)
    fig, ax = plt.subplots(figsize=(10, fig_height))

    colors = plt.cm.viridis(
        [i / max(len(names) - 1, 1) for i in range(len(names))]
    )
    bars = ax.barh(names, values, color=colors)

    max_val = max(values) if values else 0
    for bar, value in zip(bars, values):
        label = f"{value:,}".replace(",", " ")
        ax.text(
            bar.get_width() + max_val * 0.015,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center",
            fontsize=11,
        )

    ax.set_title(title, fontsize=15, fontweight="bold")
    ax.set_xlabel("Шаги (сумма)")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, max_val * 1.15 if max_val else 1)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_caption(totals, digest_date_str: str) -> str:
    """Текстовая подпись к диаграмме — топ участников текстом (дублирует график)."""
    if not totals:
        return f"📊 Дайджест за {digest_date_str}\n\nПока нет данных."

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"📊 Дайджест марафона на {digest_date_str}", ""]
    for i, (name, steps) in enumerate(totals):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        steps_fmt = f"{steps:,}".replace(",", " ")
        lines.append(f"{prefix} {name} — {steps_fmt} шагов")
    return "\n".join(lines)


async def send_digest(bot, chat_id: int, sheet, for_date: date | None = None):
    """
    Формирует и отправляет дайджест в чат.
    for_date — за какой день считать нарастающий итог (по умолчанию — вчера).
    """
    target_date = for_date or (date.today() - timedelta(days=1))
    date_str = target_date.isoformat()

    totals = compute_totals(sheet, date_str)
    chart_bytes = build_chart(totals, f"Лидерборд на {date_str}")
    caption = build_caption(totals, date_str)

    await bot.send_photo(chat_id=chat_id, photo=chart_bytes, caption=caption)
    logger.info("Digest sent for %s to chat %s (%d participants)", date_str, chat_id, len(totals))
