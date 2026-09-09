"""
Распознавание количества шагов со скриншотов фитнес-приложений.

Так как используется бесплатный Tesseract (а не платное API), точность
ниже, особенно на скриншотах из разных приложений (Google Fit, Apple
Health, Mi Fitness, Samsung Health и т.д.). Поэтому:
  1) Делаем агрессивную предобработку картинки (апскейл, контраст,
     авто-инверсия под тёмную тему).
  2) Прогоняем OCR дважды (обычный + инвертированный вариант) и
     выбираем лучший кандидат.
  3) Итоговое число ВСЕГДА показывается пользователю на подтверждение
     в чате (см. bot_old.py) — автоматическое распознавание не считается
     финальным результатом.
"""

import re
import io
import logging
from PIL import Image, ImageOps, ImageFilter
import numpy as np
import cv2
import pytesseract

logger = logging.getLogger(__name__)

# Ключевые слова рядом с которыми обычно стоит число шагов
STEP_KEYWORDS = [
    "шаг", "шагов", "шага", "steps", "step count", "pass",
    "пройдено", "walked",
]

# Разумный диапазон суточных шагов (фильтр мусора вида "калории",
# "расстояние в метрах", таймкоды и т.п.)
MIN_STEPS = 50
MAX_STEPS = 200_000


def _preprocess_variants(image_bytes: bytes):
    """Возвращает несколько подготовленных версий картинки для OCR."""
    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Апскейл мелких скриншотов — сильно помогает Tesseract
    w, h = pil_img.size
    scale = 2 if max(w, h) < 1600 else 1
    if scale > 1:
        pil_img = pil_img.resize((w * scale, h * scale), Image.LANCZOS)

    gray = ImageOps.grayscale(pil_img)
    gray = gray.filter(ImageFilter.SHARPEN)

    np_gray = np.array(gray)

    # Автоопределение тёмной темы — если фон в среднем тёмный, инвертируем
    mean_val = np_gray.mean()
    variants = []

    # Вариант 1: обычная бинаризация (Otsu)
    _, th1 = cv2.threshold(np_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(Image.fromarray(th1))

    # Вариант 2: инвертированная бинаризация (для тёмных тем)
    inverted = cv2.bitwise_not(np_gray)
    _, th2 = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(Image.fromarray(th2))

    # Вариант 3: адаптивный порог — помогает при неравномерном освещении
    adaptive = cv2.adaptiveThreshold(
        np_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11
    )
    variants.append(Image.fromarray(adaptive))

    logger.debug("Preprocessing done, mean brightness=%.1f", mean_val)
    return variants


def _extract_candidates(text: str):
    """Ищет в распознанном тексте числа-кандидаты на количество шагов."""
    candidates = []
    # число: 3-7 цифр, может содержать пробелы/запятые/точки как разделители тысяч
    pattern = re.compile(r"\d{1,3}(?:[ ,.\u00A0]\d{3})+|\d{4,7}")
    lower_text = text.lower()

    for match in pattern.finditer(text):
        raw = match.group(0)
        normalized = re.sub(r"[ ,.\u00A0]", "", raw)
        if not normalized.isdigit():
            continue
        value = int(normalized)
        if not (MIN_STEPS <= value <= MAX_STEPS):
            continue

        # бонус в приоритете, если рядом (в пределах 25 символов) есть ключевое слово
        start, end = match.span()
        window = lower_text[max(0, start - 25): end + 25]
        has_keyword = any(kw in window for kw in STEP_KEYWORDS)

        candidates.append((value, has_keyword))

    return candidates


def recognize_steps(image_bytes: bytes):
    """
    Возвращает (steps:int|None, confidence:str, raw_text:str)
    confidence: "keyword" — число найдено рядом со словом "шаги" (надёжнее)
                "guess"   — число выбрано эвристикой (самое большое в диапазоне)
                None      — ничего не найдено
    """
    all_candidates = []
    all_text = []

    for variant in _preprocess_variants(image_bytes):
        text = pytesseract.image_to_string(variant, lang="eng+rus", config="--psm 6")
        all_text.append(text)
        all_candidates.extend(_extract_candidates(text))

    if not all_candidates:
        return None, None, "\n---\n".join(all_text)

    keyword_candidates = [c for c in all_candidates if c[1]]
    if keyword_candidates:
        # среди кандидатов рядом с ключевым словом берём самый частый/большой
        value = max(keyword_candidates, key=lambda c: c[0])[0]
        return value, "keyword", "\n---\n".join(all_text)

    # иначе — эвристика: берём самое большое правдоподобное число
    value = max(all_candidates, key=lambda c: c[0])[0]
    return value, "guess", "\n---\n".join(all_text)
