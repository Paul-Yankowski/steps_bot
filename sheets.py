"""
Работа с Google Таблицей: запись/обновление результатов участников.

Формат листа "Steps" (создаётся автоматически при первом запуске):
  A: Дата (YYYY-MM-DD)
  B: Участник (Telegram имя)
  C: Шаги
  D: Статус (auto / confirmed / manual)
  E: Ссылка на сообщение со скриншотом (для проверки в спорных случаях)
"""

import logging
import gspread
from google.oauth2.service_account import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import ConnectionError, Timeout

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER = ["Дата", "Участник", "Шаги", "Статус", "Ссылка на сообщение"]
WORKSHEET_TITLE = "Steps"

# Повторяем сетевые запросы к Google при обрывах/таймаутах связи —
# до 3 попыток с нарастающей паузой (2с, 4с, 8с)
network_retry = retry(
    retry=retry_if_exception_type((ConnectionError, Timeout)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    reraise=True,
)


class StepsSheet:
    def __init__(self, credentials_path: str, spreadsheet_id: str):
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        client = gspread.authorize(creds)
        self.spreadsheet = client.open_by_key(spreadsheet_id)
        try:
            self.ws = self.spreadsheet.worksheet(WORKSHEET_TITLE)
        except gspread.WorksheetNotFound:
            self.ws = self.spreadsheet.add_worksheet(
                title=WORKSHEET_TITLE, rows=1000, cols=len(HEADER)
            )
            self.ws.append_row(HEADER)

        # если лист пустой (только что создан) - добавим заголовок
        if self.ws.row_count == 0 or not self.ws.get_all_values():
            self.ws.append_row(HEADER)

    @network_retry
    def _find_row(self, date_str: str, username: str):
        """Возвращает номер строки (1-based, с учётом заголовка), если запись за этот день уже есть."""
        records = self.ws.get_all_values()
        for idx, row in enumerate(records[1:], start=2):  # пропускаем заголовок
            if len(row) >= 2 and row[0] == date_str and row[1] == username:
                return idx
        return None

    @network_retry
    def upsert_entry(self, date_str: str, username: str, steps: int, status: str, link: str = ""):
        """Добавляет запись или обновляет существующую за тот же день/участника."""
        row_idx = self._find_row(date_str, username)
        row_values = [date_str, username, str(steps), status, link]

        if row_idx:
            self.ws.update(f"A{row_idx}:E{row_idx}", [row_values])
            logger.info("Updated row %s for %s / %s", row_idx, username, date_str)
            return "updated"
        else:
            self.ws.append_row(row_values)
            logger.info("Appended new row for %s / %s", username, date_str)
            return "inserted"

    @network_retry
    def get_existing(self, date_str: str, username: str):
        """Возвращает (steps, status) если запись есть, иначе None."""
        records = self.ws.get_all_values()
        for row in records[1:]:
            if len(row) >= 4 and row[0] == date_str and row[1] == username:
                return int(row[2]), row[3]
        return None
