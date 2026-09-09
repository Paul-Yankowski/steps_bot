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
from matplotlib.patches import FancyBboxPatch

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
    """
    Рисует диаграмму-"трек прогресса": светло-серая дорожка на всю ширину
    + цветной заполненный бар пропорционально месту в рейтинге.
    Возвращает PNG-байты.
    """
    if not totals:
        totals = [("Пока нет данных", 0)]

    # переворачиваем, чтобы лидер отображался сверху
    names = [t[0] for t in totals][::-1]
    values = [t[1] for t in totals][::-1]
    n = len(names)
    max_val = max(values) if any(values) else 1

    rank_colors = []
    for i in range(n):
        rank = n - i  # names перевёрнуты, лидер — последний элемент списка
        if rank == 1:
            rank_colors.append("#FFB800")
        elif rank == 2:
            rank_colors.append("#9CA3AF")
        elif rank == 3:
            rank_colors.append("#B87333")
        else:
            rank_colors.append("#3B82F6")

    fig_height = max(3.5, 0.9 * n + 1.4)
    fig, ax = plt.subplots(figsize=(9.5, fig_height))

    bar_h = 0.5
    for i, (val, color) in enumerate(zip(values, rank_colors)):
        # фон-дорожка на всю ширину
        track = FancyBboxPatch(
            (0, i - bar_h / 2), max_val, bar_h,
            boxstyle=f"round,pad=0,rounding_size={bar_h / 2}",
            facecolor="#EEEEEE", edgecolor="none", zorder=1,
        )
        ax.add_patch(track)

        # заполненная часть (минимальная ширина — чтобы даже 0 было видно скруглённым краем)
        fill_w = max(val, max_val * 0.04)
        fill_rounding = min(bar_h / 2, fill_w / 2)  # не даём скруглению вылезти за пределы узкого бара
        fill = FancyBboxPatch(
            (0, i - bar_h / 2), fill_w, bar_h,
            boxstyle=f"round,pad=0,rounding_size={fill_rounding}",
            facecolor=color, edgecolor="none", zorder=2,
        )
        ax.add_patch(fill)

        # номер места слева
        ax.text(
            -max_val * 0.02, i, str(n - i),
            ha="right", va="center", fontsize=15, fontweight="bold", color=color,
        )
        # значение справа от дорожки
        label = f"{val:,}".replace(",", " ")
        ax.text(
            max_val * 1.02, i, label,
            va="center", fontsize=14, fontweight="bold",
        )

    ax.set_yticks(range(n))
    ax.set_yticklabels(names, fontsize=15)
    ax.set_xlim(-max_val * 0.15, max_val * 1.25)
    ax.set_ylim(-0.7, n - 0.3)
    ax.set_title(title, fontsize=20, fontweight="bold", loc="left")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor="white")
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
