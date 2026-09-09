"""
Ежедневный дайджест марафона: лидерборд по сумме шагов за все дни
(по умолчанию — по вчерашний день включительно) с диаграммой.

Использование:
  - планово вызывается из bot.py через JobQueue раз в сутки;
  - вручную — командой /digest в чате (см. bot.py).
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
    """Рисует lollipop-диаграмму лидерборда (точка + линия), возвращает PNG-байты."""
    if not totals:
        totals = [("Пока нет данных", 0)]

    # переворачиваем, чтобы лидер отображался сверху
    names = [t[0] for t in totals][::-1]
    values = [t[1] for t in totals][::-1]
    max_val = max(values) if values else 0
    n = len(names)

    # цвета по рангу: 1 место — золото, 2 — серебро, 3 — бронза, остальные — синий
    colors = []
    for i in range(n):
        rank_from_top = n - i  # names перевёрнуты, лидер — последний элемент списка
        if rank_from_top == 1:
            colors.append("#FFD700")
        elif rank_from_top == 2:
            colors.append("#C0C0C0")
        elif rank_from_top == 3:
            colors.append("#CD7F32")
        else:
            colors.append("#4C72B0")

    fig_height = max(3.5, 0.75 * n + 1.4)
    fig, ax = plt.subplots(figsize=(9, fig_height))

    y_pos = list(range(n))
    ax.hlines(y=y_pos, xmin=0, xmax=values, color=colors, alpha=0.6, linewidth=3)
    ax.scatter(values, y_pos, color=colors, s=400, zorder=3, edgecolor="white", linewidth=2)

    for i, value in enumerate(values):
        label = f"{value:,}".replace(",", " ")
        ax.text(
            value + (max_val * 0.035 if max_val else 0.5),
            i,
            label,
            va="center",
            fontsize=14,
            fontweight="bold",
        )

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=15)
    ax.set_xlim(0, max_val * 1.22 if max_val else 1)
    ax.set_title(title, fontsize=20, fontweight="bold", loc="left")
    ax.set_xlabel("Шаги (сумма)", fontsize=13)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
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
