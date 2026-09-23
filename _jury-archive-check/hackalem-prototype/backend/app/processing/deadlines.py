"""Conservative calendar-date rules. Never read the current clock or call a model."""
from datetime import date, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS = (
    ('понедельник', 'дүйсенбі'), ('вторник', 'сейсенбі'),
    ('среда', 'среду', 'сәрсенбі'), ('четверг', 'бейсенбі'),
    ('пятница', 'пятницу', 'жұма'), ('суббота', 'субботу', 'сенбі'),
    ('воскресенье', 'жексенбі'),
)


def normalize_deadline(phrase: str, meeting_date: date | str | None, timezone: str | None) -> date | None:
    """Full-phrase matches only; unknown or ambiguous text remains unresolved.

    Weekdays mean the nearest occurrence including the meeting date. End of week
    means ISO Sunday. Explicit next/келесі means a strictly later occurrence.
    A meeting date is already a date in its supplied IANA zone, not a UTC instant.
    """
    if not meeting_date or not timezone:
        return None
    try:
        ZoneInfo(timezone)
        base = date.fromisoformat(meeting_date) if isinstance(meeting_date, str) else meeting_date
        text = ' '.join(phrase.casefold().replace('ё', 'е').split())
        offsets = {'сегодня': 0, 'завтра': 1, 'послезавтра': 2,
                   'бүгін': 0, 'ертең': 1, 'бүрсігүні': 2}
        if text in offsets:
            return base + timedelta(days=offsets[text])
        if text in ('до конца недели', 'аптаның соңына дейін', 'осы аптаның соңына дейін'):
            return base + timedelta(days=6 - base.weekday())
        match = re.fullmatch(r'через ([0-9]{1,4}) (день|дня|дней|неделю|недели|недель)', text)
        if match:
            count = int(match[1])
            return base + timedelta(days=count * (7 if match[2].startswith('недел') else 1))
        match = re.fullmatch(r'([0-9]{1,4}) (күннен|аптадан) кейін', text)
        if match:
            return base + timedelta(days=int(match[1]) * (7 if match[2] == 'аптадан' else 1))
        for weekday, names in enumerate(WEEKDAYS):
            for name in names:
                # Accept only explicit vocabulary, not substrings of compound deadlines.
                if text in (name, 'в ' + name, name + ' күні'):
                    return base + timedelta(days=(weekday - base.weekday()) % 7)
                if text in ('келесі ' + name, 'келесі ' + name + ' күні'):
                    return base + timedelta(days=(weekday - base.weekday()) % 7 or 7)
        # Russian next-weekday forms, avoiding guesses about "на следующей неделе".
        next_days = ('в следующий понедельник', 'в следующий вторник', 'в следующую среду',
                     'в следующий четверг', 'в следующую пятницу', 'в следующую субботу', 'в следующее воскресенье')
        if text in next_days:
            return base + timedelta(days=(next_days.index(text) - base.weekday()) % 7 or 7)
        until_days = ('до понедельника', 'до вторника', 'до среды', 'до четверга',
                      'до пятницы', 'до субботы', 'до воскресенья')
        kazakh_until = ('дүйсенбіге дейін', 'сейсенбіге дейін', 'сәрсенбіге дейін', 'бейсенбіге дейін',
                        'жұмаға дейін', 'сенбіге дейін', 'жексенбіге дейін')
        for phrases in (until_days, kazakh_until):
            if text in phrases:
                return base + timedelta(days=(phrases.index(text) - base.weekday()) % 7)
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return None
    return None
