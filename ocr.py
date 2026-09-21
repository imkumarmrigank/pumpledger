"""OCR extraction of Bajrang Petroleum daily voucher images into structured data.

The voucher images are a fixed two-table layout: a left "Details / Inflow" table
(Opening Cash, HSD, MS, and optional extra inflow rows like Lube/Coffee/Tank Sell/CSP)
and a right "Expenses" table (a variable list of named expense/payment lines).

Tesseract with --psm 4 ("assume a single column of text of variable sizes") reliably
separates the two tables into two sequential blocks of lines even without manually
cropping columns, because it performs its own column layout analysis. We lean on that,
then parse each block with regex + keyword matching. OCR on this kind of two-column
financial table is never perfect, so every field extracted here is meant to be reviewed
and corrected by a human in the Streamlit UI before it is trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps
import pytesseract

NUM_RE = re.compile(r'-?\d[\d,]*\.\d+|-?\d[\d,]*')
DATE_RE = re.compile(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})')

# Known left-table row labels, in the fixed order they appear on every slip.
# OCR mangles these badly (grid lines bleed into characters), so we match loosely
# on just the first couple of surviving letters rather than the whole word.
LEFT_LABEL_HINTS = {
    'opening_cash': ('open', 'opn', 'opni'),
    'hsd': ('hsd', 'hsp', 'h5d'),
    'ms': ('ms', 'm5'),
}
# Extra (variable) left-table rows we recognize by keyword; anything else with a
# number becomes an "unknown extra inflow" row that the reviewer must label.
EXTRA_INFLOW_KEYWORDS = {
    'lube': ('lube', 'lub'),
    'coffee': ('coffee', 'cofee', 'cofe'),
    'tank_sell': ('tank sell', 'tanksell', 'hsdpc tank'),
    'csp': ('csp',),
    'collection': ('collection', 'as per', 'aspar'),
}

FOOTER_KEYWORDS = (
    'stock', 'cash in hand', 'cashinhand', 'note', 'paytm12', 'paytm 12',
    'total inflow', 'total expenses', 'कुल', 'kul',
)
HEADER_SKIP_KEYWORDS = (
    'bajrang', 'petroleum', 'sonho', 'details (', 'expenses (', 'expenses/',
    'unit (unit)', 'daily ledger', 'दिनांक', 'inflow / आय', 'kharcha',
    'particulars',
)
# a column-header row varies in punctuation between templates ("DETAILS UNIT
# RATE AMOUNT EXPENSES AMOUNT" vs "DETAILS (Inflow) EXPENSES" etc.) so instead
# of matching exact phrases, treat a line as a header if EVERY word on it is a
# known header word
HEADER_WORDS = {
    'details', 'unit', 'rate', 'amount', 'expenses', 'qty', 'particulars',
    'inflow', 'kharcha',
}


@dataclass
class DetailRow:
    label: str
    unit: float | None
    rate: float | None
    amount: float | None
    raw: str = ''


@dataclass
class ExpenseLine:
    label: str
    amount: float
    raw: str = ''


@dataclass
class ExtractedVoucher:
    source_path: str
    date_str: str | None
    opening_cash: float | None
    hsd_unit: float | None
    hsd_rate: float | None
    hsd_amount: float | None
    ms_unit: float | None
    ms_rate: float | None
    ms_amount: float | None
    extra_inflow: list = field(default_factory=list)   # list[DetailRow]
    expense_lines: list = field(default_factory=list)  # list[ExpenseLine]
    stated_total_inflow: float | None = None
    stated_total_expenses: float | None = None
    cash_in_hand: float | None = None
    raw_text: str = ''
    warnings: list = field(default_factory=list)


def _to_float(s: str) -> float | None:
    s = s.replace(',', '').strip().strip('.')
    if not s or s in ('-', '.'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clean(line: str) -> str:
    return line.replace('|', ' ').replace('₹', ' ')


def _number_matches(line: str):
    return list(NUM_RE.finditer(_clean(line)))


def _numbers_and_label(line: str) -> tuple[list[float], str]:
    """Find every numeric token on the line, and recover the text label as
    everything before the LAST number (so embedded digits, e.g. a vehicle
    plate like "BR04 PA 6799", stay part of the label instead of truncating
    it — only the trailing amount is stripped off)."""
    s = _clean(line)
    matches = _number_matches(line)
    nums = [v for m in matches if (v := _to_float(m.group(0))) is not None]
    if matches:
        label = s[:matches[-1].start()]
    else:
        label = s
    label = re.sub(r'\s+', ' ', label).strip(' -:|/').strip()
    return nums, label


def _split_merged_row(line: str, expected_left_count: int):
    """Tesseract occasionally prints a Details-table row and the Expenses-table
    row that happens to sit beside it as ONE line (e.g.
    "OPNING CASH - - 90,606.00 PAYTM 261,830.66"), instead of the two separate
    blocks it usually produces. If a left-table row has more numbers than it
    should, the extra tail is really a right-table expense entry glued on —
    split it off and return it separately."""
    s = _clean(line)
    matches = _number_matches(line)
    if len(matches) <= expected_left_count:
        return None
    split_at = matches[expected_left_count - 1].end()
    right_part = s[split_at:]
    r_nums, r_label = _numbers_and_label(right_part)
    return r_nums, r_label


def _is_noise_label(label: str) -> bool:
    """Tesseract sometimes hallucinates short letter fragments out of empty
    '-' placeholder cells and grid lines (e.g. "Ee Ca a a"). Treat a label as
    noise if most of its ALPHABETIC words are 1-2 letters long (purely numeric
    tokens, e.g. leftover unit/rate figures on a merged row, don't count
    against it)."""
    words = [w for w in label.split() if re.search('[A-Za-z]', w)]
    if len(words) < 2:
        return False
    short = sum(1 for w in words if len(re.sub(r'[^A-Za-z]', '', w)) <= 2)
    return short / len(words) >= 0.6


def _ocr_lines(image: Image.Image, psm: int) -> list[str]:
    text = pytesseract.image_to_string(image, config=f'--psm {psm}')
    return [ln.rstrip() for ln in text.split('\n')]


def extract(image_path: str) -> ExtractedVoucher:
    path = Path(image_path)
    im = Image.open(path).convert('L')
    w, h = im.size
    scale = 2 if max(w, h) < 1400 else 1
    im2 = im.resize((w * scale, h * scale), Image.LANCZOS) if scale != 1 else im

    lines = _ocr_lines(im2, psm=4)
    raw_text = '\n'.join(lines)

    warnings: list[str] = []

    # ---- date ----
    date_str = None
    for ln in lines[:6]:
        m = DATE_RE.search(ln)
        if m:
            d, mo, y = m.groups()
            date_str = f'{int(d):02d}-{int(mo):02d}-{y}'
            break
    if date_str is None:
        m = DATE_RE.search(raw_text)
        if m:
            d, mo, y = m.groups()
            date_str = f'{int(d):02d}-{int(mo):02d}-{y}'
    if date_str is None:
        warnings.append('Could not find a date on this slip — set it manually.')

    # ---- classify each non-empty line as details-row / expense-row / footer / skip ----
    opening_cash = hsd_unit = hsd_rate = hsd_amount = None
    ms_unit = ms_rate = ms_amount = None
    extra_inflow: list[DetailRow] = []
    expense_lines: list[ExpenseLine] = []
    stated_total_inflow = stated_total_expenses = cash_in_hand = None

    # The left ("Details/Inflow") table always has Opening Cash, then HSD, then MS
    # as its first three rows, in that fixed order — but OCR frequently drops the
    # row's label text (leaving just bare numbers) or garbles it beyond keyword
    # matching. So position is the primary signal; a label keyword match is only
    # used to catch up if an earlier slot's row was skipped/unreadable entirely.
    slot = 0          # 0=opening,1=hsd,2=ms,3=extras — advances as left-table rows are consumed
    in_left_block = True

    for raw_line in lines:
        line = raw_line.strip()
        if not line or len(line) < 2:
            continue
        low = line.lower()

        if any(k in low for k in HEADER_SKIP_KEYWORDS):
            continue
        if DATE_RE.search(line):
            # title / subtitle line carrying the slip's date — not a data row
            continue
        words_only = re.findall(r'[A-Za-z]+', low)
        if words_only and all(w in HEADER_WORDS for w in words_only):
            continue

        nums, label = _numbers_and_label(line)

        if 'cash in hand' in low or 'cashinhand' in low:
            # this line sometimes also carries a second figure tacked on after
            # it (e.g. "Cash in Hand: 137,558.44 paytm 12: 255,486.36") — the
            # FIRST number is the one that actually follows "Cash in Hand"
            if nums:
                cash_in_hand = nums[0]
            continue
        if 'total inflow' in low or ('कुल' in line and 'inflow' in low):
            if nums:
                stated_total_inflow = nums[-1]
            continue
        if 'total expense' in low or 'kul kharch' in low:
            if nums:
                stated_total_expenses = nums[-1]
            continue
        if any(k in low for k in ('stock', 'paytm12', 'paytm 12', 'note:', 'manual')):
            continue
        if re.search(r'\b(HSD|MS)\s*[:-]\s*[\d,]', line, re.I):
            # "STOCK-ATG"/"STOCK-MANUAL" footer figures like "HSD-24684.6" or
            # "MS: 7,137.95" — not a data row. (Note the colon/hyphen: the
            # real HSD/MS detail rows are "HSD 2239.95 ...", space-separated.)
            continue

        if not nums:
            # A row that produced no digits at all is still a row: if it's long
            # enough to be real (garbled) text rather than stray noise, it counts
            # as an attempted-but-unreadable left-table row so positional slot
            # counting for the rows after it doesn't shift out of alignment.
            if in_left_block and slot < 3 and len(re.sub(r'[^A-Za-z]', '', label)) >= 5:
                slot += 1
            continue
        if _is_noise_label(label):
            continue

        low_label = label.lower()
        is_hsd_hint = any(low_label.startswith(h) for h in LEFT_LABEL_HINTS['hsd'])
        is_ms_hint = any(low_label.startswith(h) for h in LEFT_LABEL_HINTS['ms'])
        matched_extra = next(
            (key for key, hints in EXTRA_INFLOW_KEYWORDS.items() if any(h in low_label for h in hints)),
            None,
        )

        if in_left_block:
            # a keyword hint lets us jump straight to the right slot if an
            # earlier row (e.g. Opening Cash) was unreadable and skipped
            if slot == 0 and is_hsd_hint:
                slot = 1
            elif slot <= 1 and is_ms_hint:
                slot = 2

            if slot in (0, 1, 2):
                expected = 1 if slot == 0 else 3
                split_result = _split_merged_row(raw_line, expected)
                if split_result:
                    r_nums, r_label = split_result
                    if r_label and r_nums and re.search(r'[A-Za-z]{2,}', r_label):
                        expense_lines.append(ExpenseLine(label=r_label, amount=r_nums[-1], raw=raw_line))
                    nums = nums[:expected]

            if slot == 0:
                opening_cash = nums[-1]
                slot = 1
                continue
            if slot == 1:
                if len(nums) >= 3:
                    hsd_unit, hsd_rate, hsd_amount = nums[0], nums[1], nums[2]
                elif len(nums) == 2:
                    hsd_unit, hsd_amount = nums[0], nums[1]
                    hsd_rate = round(hsd_amount / hsd_unit, 2) if hsd_unit else None
                    warnings.append('HSD rate column missing on slip; inferred rate from amount/unit — verify.')
                elif len(nums) == 1:
                    hsd_amount = nums[0]
                    warnings.append('Only one number found on the HSD row; unit/rate may be missing — verify.')
                slot = 2
                continue
            if slot == 2:
                if len(nums) >= 3:
                    ms_unit, ms_rate, ms_amount = nums[0], nums[1], nums[2]
                elif len(nums) == 2:
                    ms_unit, ms_amount = nums[0], nums[1]
                    ms_rate = round(ms_amount / ms_unit, 2) if ms_unit else None
                    warnings.append('MS rate column missing on slip; inferred rate from amount/unit — verify.')
                elif len(nums) == 1:
                    ms_amount = nums[0]
                    warnings.append('Only one number found on the MS row; unit/rate may be missing — verify.')
                slot = 3
                continue
            # slot >= 3: either another known extra-inflow row, or the left
            # table has ended and this is actually the first expense line
            if matched_extra:
                unit = rate = amount = None
                if len(nums) >= 3:
                    unit, rate, amount = nums[0], nums[1], nums[2]
                elif len(nums) == 2:
                    unit, amount = nums
                elif len(nums) == 1:
                    amount = nums[0]
                extra_inflow.append(DetailRow(label=label or matched_extra, unit=unit, rate=rate, amount=amount, raw=raw_line))
                continue
            in_left_block = False
            # fall through to expense-line handling below for this line

        # ---- right ("Expenses") table: label + trailing amount ----
        letters_only = re.sub(r'[^A-Za-z]', '', label)
        if len(letters_only) <= 3 and len(nums) == 2:
            # the "कुल" (total) row — both column totals OCR'd onto one line
            # with the Hindi/garbled label in between, e.g. "562,736.10 aa 425,177.66"
            if stated_total_inflow is None:
                stated_total_inflow = nums[0]
            if stated_total_expenses is None:
                stated_total_expenses = nums[1]
            continue
        # a label with no letters at all is a stray footer/total figure, not a
        # real named expense line
        if label and re.search(r'[A-Za-z]{2,}', label):
            expense_lines.append(ExpenseLine(label=label, amount=nums[-1], raw=raw_line))

    return ExtractedVoucher(
        source_path=str(path),
        date_str=date_str,
        opening_cash=opening_cash,
        hsd_unit=hsd_unit, hsd_rate=hsd_rate, hsd_amount=hsd_amount,
        ms_unit=ms_unit, ms_rate=ms_rate, ms_amount=ms_amount,
        extra_inflow=extra_inflow,
        expense_lines=expense_lines,
        stated_total_inflow=stated_total_inflow,
        stated_total_expenses=stated_total_expenses,
        cash_in_hand=cash_in_hand,
        raw_text=raw_text,
        warnings=warnings,
    )
